// MochiEyes - eyes, clock and Claude Code monitor desk companion for the
// ESP32-C3 SuperMini.
//
// Arduino IDE > Tools:
//   Board            ESP32C3 Dev Module
//   USB CDC On Boot  Enabled   (GPIO20/21 drive the OLED, not UART0)
//   Partition Scheme Minimal SPIFFS (1.9MB APP with OTA/128KB SPIFFS)   <- required
// Libraries: Adafruit SSD1306, Adafruit GFX, ArduinoJson 7
// Wi-Fi: copy secrets.example.h to secrets.h and edit it.

#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <ESPmDNS.h>
#include <Preferences.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <ArduinoJson.h>
#include <time.h>
#include <vector>

#include "src/app/AppModes.h"
#include "src/claude/ClaudeDraw.h"
#include "src/claude/ClaudeMonitor.h"
#include "src/claude/ClaudeStatus.h"
#include "src/claude/ClaudeView.h"
#include "src/eye/EyeCommands.h"
#include "src/eye/EyeEngine.h"
#include "src/eye/EyeRenderer.h"
#include "src/touch/TouchGestures.h"
#include "src/web/WebUi.h"

#if __has_include("secrets.h")
  #include "secrets.h"
#else
  #include "secrets.example.h"
#endif

// ---- Pins ----
#define PIN_SDA     20
#define PIN_SCL     21
#define PIN_TOUCH    1   // TTP223, HIGH while touched
#define PIN_BUZZER   2   // passive buzzer

#define I2C_AUTODETECT true      // search other pins if the OLED is not on SDA/SCL
#define I2C_HZ         400000UL

// ---- Settings ----
#define OWNER_NAME   "Kitty"
#define MDNS_NAME    "mochi"     // http://mochi.local
#define TZ_STRING    "IST-5:30"  // POSIX sign is inverted
#define UNITS_METRIC true
#define TOUCH_SIDE   (-1)        // pad on the left as you face the screen; +1 if on the right

#define EYE_MAX_FPS           45
#define CLOCK_REDRAW_MS       250
#define WEATHER_REFRESH_MS    (15UL * 60UL * 1000UL)
#define CRYPTO_REFRESH_MS     (60UL * 1000UL)
#define CRYPTO_IDLE_MS        (10UL * 60UL * 1000UL)   // while the eyes are shown
#define FETCH_RETRY_MS        (30UL * 1000UL)
#define NOTE_PAGE_MS          (5UL * 1000UL)
#define IDLE_DROWSY_MS        (2UL * 60UL * 1000UL)
#define IDLE_SLEEP_MS         (5UL * 60UL * 1000UL)
#define REFRESH_HOLD_MS       1500
#define HOLD_BAR_FROM_MS      900
#define HOLD_BAR_EYES_FROM_MS 1800
#define CLAUDE_ANIM_MS        125     // loading dots while Claude works
#define CLAUDE_REDRAW_MS      250     // timers on the other Claude screens
#define CLAUDE_STALE_S        15      // default; the companion sends a heartbeat every 5 s
#define CLAUDE_STALE_S_MIN    5
#define CLAUDE_STALE_S_MAX    600

// With USB CDC off, Serial would take GPIO20/21 from the OLED.
#if ARDUINO_USB_CDC_ON_BOOT
  #define SERIAL_SAFE 1
#else
  #define SERIAL_SAFE ((PIN_SDA != 20) && (PIN_SDA != 21) && (PIN_SCL != 20) && (PIN_SCL != 21))
#endif

#if SERIAL_SAFE
  #define LOG(...)    Serial.printf(__VA_ARGS__)
  #define LOGLN(x)    Serial.println(x)
#else
  #define LOG(...)    do{}while(0)
  #define LOGLN(x)    do{}while(0)
#endif

// ---- Types (kept above all functions for Arduino's prototype generator) ----
#define SCREEN_W 128
#define SCREEN_H 64

// Allocates the frame buffer without a panel, so drawing and the web mirror
// work even when no OLED is connected.
class Oled : public Adafruit_SSD1306 {
 public:
  using Adafruit_SSD1306::Adafruit_SSD1306;
  bool allocBuffer() {
    if (!buffer) buffer = (uint8_t*)calloc(1, SCREEN_W * SCREEN_H / 8);
    return buffer != nullptr;
  }
};

// AppMode (eyes, clock, claude) and its cycle order live in src/app/AppModes.h.
enum Screen  : uint8_t { SCR_CLOCK = 0, SCR_WEATHER, SCR_CRYPTO, SCR_NOTES, SCR_COUNT };
enum class React : uint8_t { None, Glance, Happy, Grumpy, Dizzy, Pet, Wake };

enum Sfx : uint8_t {
  SFX_GLANCE, SFX_HAPPY, SFX_GRUMPY, SFX_DIZZY, SFX_PET, SFX_WAKE,
  SFX_MODE, SFX_PAGE, SFX_TICK, SFX_REFRESH, SFX_SAVED,
  SFX_CC_PERMISSION, SFX_CC_DONE, SFX_CC_FAIL, SFX_CC_LOST
};

struct Note { uint16_t hz; uint16_t ms; };   // hz = 0 is a rest

struct Settings {
  bool     sound     = true;
  bool     autoSleep = true;
  uint8_t  startMode = MODE_EYES;
  // Claude Code alerts, each switchable; "sound" above still mutes everything.
  bool     ccPermission = true;
  bool     ccDone       = true;
  bool     ccFail       = true;
  bool     ccLost       = false;   // optional: off by default so a sleeping PC does not beep
  uint16_t ccStaleS     = CLAUDE_STALE_S;
};

struct Weather {
  bool   valid = false;
  String city;
  float  temp = 0, tmin = 0, tmax = 0, wind = 0;
  int    hum = 0, code = -1;
};

struct Coin { const char* id; const char* tag; float usd; float chg; bool ok; };

struct Transition {
  bool     active  = false;
  bool     swapped = false;
  uint32_t t0      = 0;
  AppMode  to      = MODE_EYES;
};

// ---- Globals ----
Oled display(SCREEN_W, SCREEN_H, &Wire, -1, I2C_HZ, I2C_HZ);
bool    haveDisplay = false;
uint8_t oledAddr    = 0x3C;
uint8_t usedSda     = PIN_SDA;
uint8_t usedScl     = PIN_SCL;

eye::EyeEngine   engine;
eye::EyeRenderer renderer;
eye::EyeCommands commands(engine, renderer);
touch::TouchGestures gestures;

WebServer   server(80);
Preferences prefs;
Settings    settings;

AppMode    appMode = MODE_EYES;
Transition trans;
uint8_t    screen = SCR_CLOCK;
bool       redrawNow = true;

String wifiSsid, wifiPass;
bool   apMode = false;
String noteTitle = "Notes";
String noteText  = "Tap the pad to change pages.\n\nOpen http://mochi.local\nto write here.";
std::vector<String> noteLines;
uint8_t  notePage = 0;
uint32_t lastNoteFlip = 0;

// Written by fetchTask, read by the loop task: always access through DataLock.
Weather wx;
Coin coins[3] = {
  { "bitcoin",  "BTC", 0, 0, false },
  { "ethereum", "ETH", 0, 0, false },
  { "solana",   "SOL", 0, 0, false }
};
SemaphoreHandle_t dataLock = nullptr;
TaskHandle_t      fetchTaskHandle = nullptr;
volatile bool     fetchBusy = false, fetchForce = false;
volatile uint32_t lastWeatherOk = 0, lastCryptoOk = 0;
uint32_t          refreshBannerUntil = 0;

// Base = what the user chose; reactions and idle are temporary on top of it.
eye::Mood       baseMood = eye::Mood::Calm;
eye::Expression baseExpr = eye::Expression::Neutral;
React    react = React::None;
uint32_t reactT0 = 0, reactUntil = 0;   // reactUntil 0 = until released
uint8_t  reactPhase = 0;
uint32_t lastInteraction = 0;
uint8_t  idleStage = 0;                 // 0 awake, 1 drowsy, 2 asleep
bool     demoOn = false;
uint32_t demoT0 = 0, nextSpeechPause = 0;
int      demoStep = -1;

char     caption[24] = "";
uint32_t captionUntil = 0;

Note     melody[6];
uint8_t  melodyLen = 0, melodyPos = 0;
uint32_t noteEndAt = 0;

bool     refreshArmed = false;
uint32_t restartAt = 0;
float    fps = 0;

// Claude Code monitor. Written by the web handler and read by the renderer,
// both in the loop task, so it needs no lock (see the concurrency table in
// docs/DESIGN.md). The body buffer is fixed: an oversized request is counted
// and rejected, never allocated.
claude::Monitor claudeMon;
bool     claudeDetails = false;     // tap toggles a details page
Sfx      claudeAlertQueued = SFX_CC_DONE;
bool     claudeAlertPending = false;
uint32_t claudeRejected = 0;
char     ccBody[claude::kMaxBody + 1];
size_t   ccBodyLen = 0;
bool     ccBodyTooBig = false;
uint32_t lastPushedHash = 0;

static const char* const kMoodNames[] = {"calm", "listening", "talking", "curious", "focused", "sleepy"};
static const char* const kExprNames[] = {"neutral", "happy", "sad", "angry", "surprised", "sleepy", "suspicious"};
static const char* const kScreenNames[] = {"clock", "weather", "crypto", "notes"};

struct DataLock {
  bool held;
  DataLock() : held(dataLock && xSemaphoreTake(dataLock, pdMS_TO_TICKS(50)) == pdTRUE) {}
  ~DataLock() { if (held) xSemaphoreGive(dataLock); }
  bool ok() const { return held; }
};

// ---- Time ----
// getLocalTime() blocks while unsynced, which would stutter the eyes.
bool nowLocal(struct tm& t) {
  time_t now = time(nullptr);
  localtime_r(&now, &t);
  return t.tm_year > (2016 - 1900);
}

// ---- Sound ----
void startNote() {
  if (melodyPos >= melodyLen) { noTone(PIN_BUZZER); melodyLen = 0; return; }
  const Note& n = melody[melodyPos];
  if (n.hz) tone(PIN_BUZZER, n.hz); else noTone(PIN_BUZZER);
  noteEndAt = millis() + n.ms;
}

void playNotes(const Note* notes, uint8_t count) {
  if (!settings.sound) return;
  melodyLen = 0;
  for (uint8_t i = 0; i < count && i < 6; i++) melody[melodyLen++] = notes[i];
  melodyPos = 0;
  startNote();
}

void sfx(Sfx s) {
  static const Note glance[]  = {{2093, 22}};
  static const Note happy[]   = {{1568, 50}, {0, 15}, {2093, 80}};
  static const Note grumpy[]  = {{330, 90}, {0, 20}, {247, 150}};
  static const Note dizzy[]   = {{1760, 40}, {1319, 40}, {1760, 40}, {1175, 40}, {1568, 40}, {988, 90}};
  static const Note pet[]     = {{1047, 40}, {1319, 70}};
  static const Note wake[]    = {{784, 60}, {0, 20}, {1175, 90}};
  static const Note mode[]    = {{1319, 45}, {1760, 45}, {2637, 80}};
  static const Note page[]    = {{2200, 35}};
  static const Note tick[]    = {{900, 30}};
  static const Note refresh[] = {{900, 120}};
  static const Note saved[]   = {{1800, 60}};
  // Claude Code alerts: distinct from the touch chirps above.
  static const Note ccPerm[]  = {{1760, 70}, {0, 90}, {1760, 70}};                       // two short beeps
  static const Note ccDone[]  = {{1047, 60}, {1568, 420}};                               // one long tone
  static const Note ccFail[]  = {{440, 80}, {0, 40}, {440, 80}, {0, 40}, {294, 220}};   // warning
  static const Note ccLost[]  = {{784, 120}, {0, 60}, {523, 240}};
  switch (s) {
    case SFX_GLANCE:  playNotes(glance, 1);  break;
    case SFX_HAPPY:   playNotes(happy, 3);   break;
    case SFX_GRUMPY:  playNotes(grumpy, 3);  break;
    case SFX_DIZZY:   playNotes(dizzy, 6);   break;
    case SFX_PET:     playNotes(pet, 2);     break;
    case SFX_WAKE:    playNotes(wake, 3);    break;
    case SFX_MODE:    playNotes(mode, 3);    break;
    case SFX_PAGE:    playNotes(page, 1);    break;
    case SFX_TICK:    playNotes(tick, 1);    break;
    case SFX_REFRESH: playNotes(refresh, 1); break;
    case SFX_SAVED:   playNotes(saved, 1);   break;
    case SFX_CC_PERMISSION: playNotes(ccPerm, 3); break;
    case SFX_CC_DONE:       playNotes(ccDone, 2); break;
    case SFX_CC_FAIL:       playNotes(ccFail, 5); break;
    case SFX_CC_LOST:       playNotes(ccLost, 3); break;
  }
}

// A Claude alert waits for the current melody (a touch chirp, a mode switch)
// to finish instead of cutting it off. One slot: a newer alert replaces an
// older one that has not started yet.
void queueClaudeAlert(Sfx s) {
  claudeAlertQueued = s;
  claudeAlertPending = true;
}

void serviceSound() {
  if (melodyLen && (int32_t)(millis() - noteEndAt) >= 0) { melodyPos++; startNote(); }
  if (claudeAlertPending && melodyLen == 0) {
    claudeAlertPending = false;
    sfx(claudeAlertQueued);
  }
}

void beepBlocking(unsigned int freq, unsigned long ms) {
  if (!settings.sound) { delay(ms); return; }
  tone(PIN_BUZZER, freq); delay(ms); noTone(PIN_BUZZER);
}

// ---- Text helpers ----
void centerText(const String& s, int y, uint8_t size) {
  int16_t x1, y1; uint16_t w, h;
  display.setTextSize(size);
  display.getTextBounds(s, 0, y, &x1, &y1, &w, &h);
  display.setCursor((SCREEN_W - (int)w) / 2, y);
  display.print(s);
}

void wrapInto(const String& src, uint8_t maxChars, std::vector<String>& out) {
  out.clear();
  int start = 0;
  while (start <= (int)src.length()) {
    int nl = src.indexOf('\n', start);
    String para = (nl < 0) ? src.substring(start) : src.substring(start, nl);
    para.replace("\r", "");
    if (para.length() == 0) out.push_back("");
    while (para.length() > 0) {
      if (para.length() <= maxChars) { out.push_back(para); break; }
      int cut = -1;
      for (int i = maxChars; i > 0; i--) if (para.charAt(i) == ' ') { cut = i; break; }
      if (cut <= 0) cut = maxChars;
      out.push_back(para.substring(0, cut));
      para = para.substring(cut);
      para.trim();
    }
    if (nl < 0) break;
    start = nl + 1;
  }
  if (out.empty()) out.push_back("");
}
void rebuildNoteLines() { wrapInto(noteText, 21, noteLines); notePage = 0; }

void setCaption(const char* text, uint32_t ms) {
  strncpy(caption, text, sizeof(caption) - 1);
  caption[sizeof(caption) - 1] = 0;
  captionUntil = ms ? millis() + ms : 0xFFFFFFFFu;
}

// ---- Display ----
bool probe(uint8_t sda, uint8_t scl, uint8_t addr) {
  Wire.end();
  delay(5);
  if (!Wire.begin(sda, scl, 100000)) return false;
  delay(10);
  Wire.beginTransmission(addr);
  return Wire.endTransmission() == 0;
}

bool sweepAllowed(uint8_t p) { return p != PIN_TOUCH && p != PIN_BUZZER; }

bool startDisplay() {
  const uint8_t addrs[] = {0x3C, 0x3D};

  for (uint8_t a : addrs) {
    if (probe(PIN_SDA, PIN_SCL, a)) { usedSda = PIN_SDA; usedScl = PIN_SCL; oledAddr = a; goto found; }
  }
  if (!I2C_AUTODETECT) return false;

  {
    LOGLN("OLED not on configured pins, searching...");
    const uint8_t common[][2] = {{8,9},{9,8},{5,6},{6,5},{4,5},{6,7},{7,6},{10,8},{20,21},{21,20}};
    for (auto &p : common)
      for (uint8_t a : addrs)
        if (sweepAllowed(p[0]) && sweepAllowed(p[1]) && probe(p[0], p[1], a)) {
          usedSda = p[0]; usedScl = p[1]; oledAddr = a; goto found;
        }

    const uint8_t pins[] = {0,1,2,3,4,5,6,7,8,9,10,20,21};
    for (uint8_t i = 0; i < sizeof(pins); i++)
      for (uint8_t j = 0; j < sizeof(pins); j++) {
        if (i == j || !sweepAllowed(pins[i]) || !sweepAllowed(pins[j])) continue;
        for (uint8_t a : addrs)
          if (probe(pins[i], pins[j], a)) { usedSda = pins[i]; usedScl = pins[j]; oledAddr = a; goto found; }
      }
  }
  Wire.end();
  return false;

found:
  LOG("OLED found: SDA=%d SCL=%d addr=0x%02X\n", usedSda, usedScl, oledAddr);
  Wire.end();
  delay(5);
  Wire.begin(usedSda, usedScl, I2C_HZ);
  if (!display.begin(SSD1306_SWITCHCAPVCC, oledAddr)) {
    LOGLN("SSD1306 responded on I2C but begin() failed");
    return false;
  }
  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);
  display.display();
  return true;
}

void pushFrame() { if (haveDisplay) display.display(); }

// ---- Storage ----
// Same namespace and keys as the old clock firmware, so saved data survives.
void loadConfig() {
  prefs.begin("terminal", true);
  wifiSsid  = prefs.getString("ssid",  WIFI_SSID_DEFAULT);
  wifiPass  = prefs.getString("pass",  WIFI_PASS_DEFAULT);
  noteTitle = prefs.getString("title", noteTitle);
  noteText  = prefs.getString("text",  noteText);
  settings.sound     = prefs.getBool("sound", true);
  settings.autoSleep = prefs.getBool("asleep", true);
  settings.startMode = modeFromByte(prefs.getUChar("start", MODE_EYES));
  settings.ccPermission = prefs.getBool("cc_perm", true);
  settings.ccDone       = prefs.getBool("cc_done", true);
  settings.ccFail       = prefs.getBool("cc_fail", true);
  settings.ccLost       = prefs.getBool("cc_lost", false);
  settings.ccStaleS     = constrain(prefs.getUShort("cc_stale", CLAUDE_STALE_S), CLAUDE_STALE_S_MIN, CLAUDE_STALE_S_MAX);
  prefs.end();
  rebuildNoteLines();
}
void saveNote() {
  prefs.begin("terminal", false);
  prefs.putString("title", noteTitle);
  prefs.putString("text",  noteText);
  prefs.end();
}
void saveWifi(const String& s, const String& p) {
  prefs.begin("terminal", false);
  prefs.putString("ssid", s);
  prefs.putString("pass", p);
  prefs.end();
}
void saveSettings() {
  prefs.begin("terminal", false);
  prefs.putBool("sound", settings.sound);
  prefs.putBool("asleep", settings.autoSleep);
  prefs.putUChar("start", settings.startMode);
  prefs.putBool("cc_perm", settings.ccPermission);
  prefs.putBool("cc_done", settings.ccDone);
  prefs.putBool("cc_fail", settings.ccFail);
  prefs.putBool("cc_lost", settings.ccLost);
  prefs.putUShort("cc_stale", settings.ccStaleS);
  prefs.end();
}

// ---- Network fetches ----
// HTTPS handshakes take seconds; they run in fetchTask so the eyes never freeze.
bool fetchJson(const String& url, JsonDocument& doc) {
  if (WiFi.status() != WL_CONNECTED) return false;
  int code = -1;
  String payload;

  if (url.startsWith("https")) {
    WiFiClientSecure client;
    client.setInsecure();   // public, read-only data
    HTTPClient http;
    http.setTimeout(12000);
    http.setUserAgent("esp32-mochi");
    if (http.begin(client, url)) {
      code = http.GET();
      if (code == HTTP_CODE_OK) payload = http.getString();
      http.end();
    }
  } else {
    WiFiClient client;
    HTTPClient http;
    http.setTimeout(12000);
    if (http.begin(client, url)) {
      code = http.GET();
      if (code == HTTP_CODE_OK) payload = http.getString();
      http.end();
    }
  }
  if (code != HTTP_CODE_OK || payload.length() == 0) {
    LOG("HTTP %d for %s\n", code, url.substring(0, 48).c_str());
    return false;
  }
  DeserializationError e = deserializeJson(doc, payload);
  if (e) { LOG("JSON error: %s\n", e.c_str()); return false; }
  return true;
}

static double geoLat = 0, geoLon = 0;
static bool   geoValid = false;
static String geoCity;

bool fetchLocation() {
  JsonDocument doc;
  if (!fetchJson("http://ip-api.com/json/?fields=status,city,lat,lon", doc)) return false;
  if (String((const char*)(doc["status"] | "")) != "success") return false;
  geoCity  = String((const char*)(doc["city"] | "Here"));
  geoLat   = doc["lat"] | 0.0;
  geoLon   = doc["lon"] | 0.0;
  geoValid = true;
  LOG("Location: %s (%.3f, %.3f)\n", geoCity.c_str(), geoLat, geoLon);
  return true;
}

bool fetchWeather(Weather& out) {
  if (!geoValid && !fetchLocation()) return false;
  String url = "https://api.open-meteo.com/v1/forecast?latitude=" + String(geoLat, 4) +
               "&longitude=" + String(geoLon, 4) +
               "&current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m" +
               "&daily=temperature_2m_max,temperature_2m_min&forecast_days=1&timezone=auto";
  if (!UNITS_METRIC) url += "&temperature_unit=fahrenheit&wind_speed_unit=mph";

  JsonDocument doc;
  if (!fetchJson(url, doc)) return false;
  JsonObject cur = doc["current"];
  if (cur.isNull()) return false;
  out.city = geoCity;
  out.temp = cur["temperature_2m"]       | 0.0f;
  out.hum  = cur["relative_humidity_2m"] | 0;
  out.code = cur["weather_code"]         | -1;
  out.wind = cur["wind_speed_10m"]       | 0.0f;
  out.tmax = doc["daily"]["temperature_2m_max"][0] | out.temp;
  out.tmin = doc["daily"]["temperature_2m_min"][0] | out.temp;
  out.valid = true;
  return true;
}

bool fetchCrypto(Coin out[3]) {
  JsonDocument doc;
  const char* url = "https://api.coingecko.com/api/v3/simple/price"
                    "?ids=bitcoin,ethereum,solana&vs_currencies=usd&include_24hr_change=true";
  if (!fetchJson(url, doc)) return false;
  bool any = false;
  for (int i = 0; i < 3; i++) {
    JsonObject o = doc[out[i].id];
    if (o.isNull()) { out[i].ok = false; continue; }
    out[i].usd = o["usd"] | 0.0f;
    out[i].chg = o["usd_24h_change"] | 0.0f;
    out[i].ok  = true;
    any = true;
  }
  return any;
}

void fetchTask(void*) {
  uint32_t weatherRetryAt = 0, cryptoRetryAt = 0;
  for (;;) {
    ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(5000));   // woken by requestFetch() or every 5 s
    if (WiFi.status() != WL_CONNECTED) continue;
    const bool force = fetchForce;
    fetchForce = false;
    fetchBusy = true;

    bool wxOk, coinsOk;
    { DataLock l; wxOk = wx.valid; coinsOk = coins[0].ok; }

    uint32_t now = millis();
    if (force || ((!wxOk || now - lastWeatherOk > WEATHER_REFRESH_MS) && (int32_t)(now - weatherRetryAt) >= 0)) {
      Weather w;
      if (fetchWeather(w)) {
        DataLock l;
        if (l.ok()) wx = w;
        lastWeatherOk = millis();
      } else {
        weatherRetryAt = millis() + FETCH_RETRY_MS;
      }
    }

    now = millis();
    const uint32_t every = (appMode == MODE_CLOCK) ? CRYPTO_REFRESH_MS : CRYPTO_IDLE_MS;
    if (force || ((!coinsOk || now - lastCryptoOk > every) && (int32_t)(now - cryptoRetryAt) >= 0)) {
      Coin fresh[3];
      { DataLock l; memcpy(fresh, coins, sizeof(fresh)); }
      if (fetchCrypto(fresh)) {
        DataLock l;
        if (l.ok()) memcpy(coins, fresh, sizeof(coins));
        lastCryptoOk = millis();
      } else {
        cryptoRetryAt = millis() + FETCH_RETRY_MS;
      }
    }
    fetchBusy = false;
    redrawNow = true;
  }
}

void requestFetch(bool force) {
  if (force) fetchForce = true;
  if (fetchTaskHandle) xTaskNotifyGive(fetchTaskHandle);
}

// ---- Wi-Fi ----
const char* wifiReason(wl_status_t s) {
  switch (s) {
    case WL_NO_SSID_AVAIL:  return "network not found (check name, and that it is 2.4GHz)";
    case WL_CONNECT_FAILED: return "wrong password";
    case WL_IDLE_STATUS:    return "idle";
    case WL_DISCONNECTED:   return "disconnected";
    default:                return "timed out";
  }
}

void startAP() {
  apMode = true;
  WiFi.mode(WIFI_AP);
  WiFi.softAP(AP_SSID, AP_PASS);
  LOG("Hotspot started: %s  ->  http://%s/\n", AP_SSID, WiFi.softAPIP().toString().c_str());
}

// ---- Eyes: reactions ----
const char* reactName(React r) {
  switch (r) {
    case React::Glance: return "glance";
    case React::Happy:  return "happy";
    case React::Grumpy: return "grumpy";
    case React::Dizzy:  return "dizzy";
    case React::Pet:    return "petting";
    case React::Wake:   return "waking";
    default:            return "none";
  }
}

void restoreBase() {
  engine.setMode(eye::Mode::Auto);
  engine.setMood(baseMood);
  engine.setExpression(baseExpr);
}

void endReaction() {
  if (react == React::None) return;
  react = React::None;
  restoreBase();
}

void startReaction(React r, uint32_t durMs) {
  if (demoOn) return;
  restoreBase();
  const uint32_t now = millis();
  react = r;
  reactT0 = now;
  reactUntil = durMs ? now + durMs : 0;
  reactPhase = 0;
  switch (r) {
    case React::Glance:
      engine.lookAt(TOUCH_SIDE * 34.0f, -2.0f);
      engine.setArousal(0.6f);
      sfx(SFX_GLANCE);
      break;
    case React::Happy:
      engine.setExpression(eye::Expression::Happy);
      engine.lookAt(0.0f, 2.0f);
      engine.setArousal(0.8f);
      sfx(SFX_HAPPY);
      break;
    case React::Grumpy:
      engine.setExpression(eye::Expression::Angry);
      engine.lookAt(TOUCH_SIDE * 26.0f, -6.0f);
      sfx(SFX_GRUMPY);
      break;
    case React::Dizzy:   // gaze is overridden in drawEyes()
      engine.setExpression(eye::Expression::Surprised);
      sfx(SFX_DIZZY);
      break;
    case React::Pet:
      engine.setMood(eye::Mood::Sleepy);
      engine.setExpression(eye::Expression::Happy);
      engine.lookAt(0.0f, -4.0f);
      sfx(SFX_PET);
      break;
    case React::Wake:
      engine.setSleeping(false);
      engine.setMood(eye::Mood::Sleepy);
      sfx(SFX_WAKE);
      break;
    default:
      break;
  }
}

void serviceReaction(uint32_t now) {
  if (react == React::None) return;
  if (react == React::Grumpy && reactPhase == 0 && now - reactT0 > 1100) {
    reactPhase = 1;
    engine.lookAt(0.0f, -3.0f);
  }
  if (reactUntil && (int32_t)(now - reactUntil) >= 0) {
    const React done = react;
    if (done == React::Pet) { startReaction(React::Happy, 1400); return; }
    endReaction();
    if (done == React::Dizzy || done == React::Grumpy) engine.blinkNow();
  }
}

// Touches and web actions count as company; polling the page does not.
void noteInteraction(bool wakeFromIdle) {
  lastInteraction = millis();
  if (idleStage == 1 && react == React::None) engine.setMood(baseMood);
  if (idleStage == 2 && wakeFromIdle) engine.setSleeping(false);
  idleStage = 0;
}

void serviceIdle(uint32_t now) {
  if (!settings.autoSleep || appMode != MODE_EYES || demoOn || react != React::None) return;
  const uint32_t idle = now - lastInteraction;
  if (idleStage == 0 && idle > IDLE_DROWSY_MS) {
    idleStage = 1;
    engine.setMood(eye::Mood::Sleepy);
    LOGLN("idle: drowsy");
  } else if (idleStage == 1 && idle > IDLE_SLEEP_MS) {
    idleStage = 2;
    engine.setSleeping(true);
    LOGLN("idle: asleep");
  }
}

// ---- Eyes: demo ----
void runDemo(uint32_t now) {
  struct Step { uint32_t atMs; const char* cmd; };
  static const Step steps[] = {
      {0, "mood calm"},          {10000, "mood curious"},    {20000, "mood listening"},
      {30000, "mood talking"},   {40000, "mood focused"},    {48000, "mood calm"},
      {50000, "expr happy"},     {54000, "expr surprised"},  {57000, "expr suspicious"},
      {61000, "expr angry"},     {64000, "expr sad"},        {68000, "expr neutral"},
      {70000, "startle -22 6"},  {74000, "startle 20 -4"},   {78000, "mood sleepy"},
      {88000, "sleep"},          {94000, "wake"},            {97000, "mood calm"},
  };
  static const int n = sizeof(steps) / sizeof(steps[0]);
  const uint32_t t = now - demoT0;
  while (demoStep + 1 < n && t >= steps[demoStep + 1].atMs) {
    ++demoStep;
    char reply[160];
    commands.execute(steps[demoStep].cmd, reply, sizeof reply);
    LOG("[demo] %s\n", steps[demoStep].cmd);
  }
  if (engine.mood() == eye::Mood::Talking && (int32_t)(now - nextSpeechPause) >= 0) {
    engine.speechPause();
    nextSpeechPause = now + 1500 + (uint32_t)random(1500);
  }
  if (t > 103000) { demoT0 = now; demoStep = -1; }
}

void setDemo(bool on) {
  if (on) endReaction();
  demoOn = on;
  demoT0 = millis();
  demoStep = -1;
  if (!demoOn) {
    engine.setSleeping(false);
    restoreBase();
  }
}

// ---- Clock pages ----
const char* weatherWord(int c) {
  switch (c) {
    case 0:  return "Clear";
    case 1:  return "Mostly clear";
    case 2:  return "Part cloudy";
    case 3:  return "Overcast";
    case 45: case 48: return "Fog";
    case 51: case 53: case 55: return "Drizzle";
    case 56: case 57: return "Frz drizzle";
    case 61: case 63: return "Rain";
    case 65: return "Heavy rain";
    case 66: case 67: return "Freezing rain";
    case 71: case 73: case 75: case 77: return "Snow";
    case 80: case 81: return "Showers";
    case 82: return "Heavy showers";
    case 85: case 86: return "Snow showers";
    case 95: return "Thunderstorm";
    case 96: case 99: return "Storm + hail";
    default: return "--";
  }
}

void drawWeatherIcon(int x, int y, int c) {
  if (c == 0 || c == 1) {
    display.fillCircle(x + 10, y + 10, 5, SSD1306_WHITE);
    for (int a = 0; a < 8; a++) {
      float r = a * PI / 4;
      display.drawLine(x + 10 + cos(r) * 8, y + 10 + sin(r) * 8,
                       x + 10 + cos(r) * 11, y + 10 + sin(r) * 11, SSD1306_WHITE);
    }
    return;
  }
  display.fillCircle(x + 7,  y + 10, 5, SSD1306_WHITE);
  display.fillCircle(x + 14, y + 10, 6, SSD1306_WHITE);
  display.fillRect(x + 4, y + 11, 14, 5, SSD1306_WHITE);
  if ((c >= 51 && c <= 67) || (c >= 80 && c <= 82))
    for (int i = 0; i < 3; i++) display.drawLine(x + 5 + i * 5, y + 18, x + 3 + i * 5, y + 23, SSD1306_WHITE);
  if (c >= 71 && c <= 77)
    for (int i = 0; i < 3; i++) display.fillCircle(x + 6 + i * 5, y + 20, 1, SSD1306_WHITE);
  if (c >= 95) {
    display.drawLine(x + 11, y + 17, x + 7, y + 22, SSD1306_WHITE);
    display.drawLine(x + 7,  y + 22, x + 12, y + 21, SSD1306_WHITE);
    display.drawLine(x + 12, y + 21, x + 8, y + 26, SSD1306_WHITE);
  }
}

String money(float v) {
  if (v >= 1000) return String((long)(v + 0.5f));
  if (v >= 1)    return String(v, 2);
  return String(v, 4);
}

void drawHeader(const char* title) {
  display.fillRect(0, 0, SCREEN_W, 11, SSD1306_WHITE);
  display.setTextColor(SSD1306_BLACK);
  display.setTextSize(1);
  display.setCursor(2, 2);
  display.print(title);
  if (WiFi.status() == WL_CONNECTED) display.fillCircle(122, 5, 2, SSD1306_BLACK);
  else                               display.drawCircle(122, 5, 2, SSD1306_BLACK);
  display.setTextColor(SSD1306_WHITE);
}

void drawClockFace() {
  struct tm t;
  if (!nowLocal(t)) {
    drawHeader("Clock");
    centerText("Waiting for time", 30, 1);
    return;
  }
  char hhmm[8], ss[4], date[24], wd[12];
  strftime(hhmm, sizeof(hhmm), "%H:%M", &t);
  strftime(ss,   sizeof(ss),   "%S",    &t);
  strftime(date, sizeof(date), "%d %b %Y", &t);
  strftime(wd,   sizeof(wd),   "%A",    &t);

  display.setTextSize(3);
  display.setCursor(8, 12);
  display.print(hhmm);
  display.setTextSize(1);
  display.setCursor(98, 28);
  display.print(ss);
  display.drawFastHLine(0, 42, SCREEN_W, SSD1306_WHITE);
  centerText(String(wd), 46, 1);
  centerText(String(date), 56, 1);
}

void drawWeather() {
  drawHeader("Weather");
  DataLock l;
  if (!l.ok() || !wx.valid) {
    centerText(WiFi.status() == WL_CONNECTED ? "Loading..." : "No Wi-Fi", 32, 1);
    return;
  }
  drawWeatherIcon(2, 14, wx.code);
  display.setTextSize(2);
  display.setCursor(34, 16);
  display.print(String(wx.temp, 1));
  display.setTextSize(1);
  display.print(UNITS_METRIC ? "C" : "F");
  display.setCursor(34, 34);
  display.print(weatherWord(wx.code));
  display.drawFastHLine(0, 44, SCREEN_W, SSD1306_WHITE);
  display.setCursor(2, 47);
  display.print(wx.city.substring(0, 12));
  display.setCursor(2, 56);
  display.print(String((int)round(wx.tmin)) + "/" + String((int)round(wx.tmax)) + "  " +
                String(wx.hum) + "%  " + String((int)round(wx.wind)) + (UNITS_METRIC ? "km/h" : "mph"));
}

void drawCrypto() {
  drawHeader("Crypto  USD");
  DataLock l;
  if (!l.ok() || (!coins[0].ok && !coins[1].ok && !coins[2].ok)) {
    centerText(WiFi.status() == WL_CONNECTED ? "Loading..." : "No Wi-Fi", 32, 1);
    return;
  }
  int y = 15;
  for (int i = 0; i < 3; i++) {
    display.setTextSize(1);
    display.setCursor(2, y);
    display.print(coins[i].tag);
    if (!coins[i].ok) { display.setCursor(30, y); display.print("--"); y += 17; continue; }
    display.setCursor(28, y);
    display.print(money(coins[i].usd));

    String ch = (coins[i].chg >= 0 ? "+" : "") + String(coins[i].chg, 1) + "%";
    int16_t x1, y1; uint16_t w, h;
    display.getTextBounds(ch, 0, y, &x1, &y1, &w, &h);
    display.setCursor(SCREEN_W - w - 2, y);
    display.print(ch);
    if (coins[i].chg >= 0) display.fillTriangle(SCREEN_W-w-10, y+6, SCREEN_W-w-6, y+6, SCREEN_W-w-8, y+1, SSD1306_WHITE);
    else                   display.fillTriangle(SCREEN_W-w-10, y+1, SCREEN_W-w-6, y+1, SCREEN_W-w-8, y+6, SSD1306_WHITE);

    y += 17;
    if (i < 2) display.drawFastHLine(0, y - 5, SCREEN_W, SSD1306_WHITE);
  }
}

void drawNotes() {
  drawHeader(noteTitle.substring(0, 18).c_str());
  const uint8_t rows = 6;
  uint16_t pages = (noteLines.size() + rows - 1) / rows;
  if (pages == 0) pages = 1;
  if (notePage >= pages) notePage = 0;
  for (uint8_t r = 0; r < rows; r++) {
    uint16_t idx = notePage * rows + r;
    if (idx >= noteLines.size()) break;
    display.setTextSize(1);
    display.setCursor(0, 14 + r * 8);
    display.print(noteLines[idx]);
  }
  if (pages > 1)
    for (uint16_t p = 0; p < pages && p < 8; p++) {
      if (p == notePage) display.fillCircle(125, 16 + p * 6, 1, SSD1306_WHITE);
      else               display.drawPixel(125, 16 + p * 6, SSD1306_WHITE);
    }
}

void drawClockMode(uint32_t now) {
  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);
  switch (screen) {
    case SCR_CLOCK:   drawClockFace(); break;
    case SCR_WEATHER: drawWeather();   break;
    case SCR_CRYPTO:  drawCrypto();    break;
    case SCR_NOTES:   drawNotes();     break;
  }
  if (apMode) {
    display.fillRect(0, 56, SCREEN_W, 8, SSD1306_BLACK);
    display.setTextSize(1);
    display.setCursor(0, 56);
    display.print("AP " AP_SSID " 192.168.4.1");
  }
  if ((int32_t)(refreshBannerUntil - now) > 0 && (fetchBusy || fetchForce)) {
    display.fillRoundRect(24, 22, 80, 20, 3, SSD1306_BLACK);
    display.drawRoundRect(24, 22, 80, 20, 3, SSD1306_WHITE);
    centerText("Refreshing", 28, 1);
  }
}

// ---- Claude Code face ----
// The words come from src/claude/ClaudeView.h and the drawing from
// src/claude/ClaudeDraw.h, both also built on a PC (tests and screenshots).
// This section adds what only the device knows: its address and settings.
void deviceAddress(char* out, size_t cap) {
  const String ip = apMode ? WiFi.softAPIP().toString() : WiFi.localIP().toString();
  snprintf(out, cap, "%s", ip.c_str());
}

void drawClaudeDetails(uint32_t now) {
  drawHeader("Claude details");
  const claude::Update& u = claudeMon.last();
  char line[40], t[12];
  int y = 14;
  auto row = [&](const char* s) { display.setCursor(0, y); display.print(s); y += 8; };
  snprintf(line, sizeof(line), "Link    %s", claude::linkName(claudeMon.link()));                    row(line);
  snprintf(line, sizeof(line), "Session %s", u.session[0] ? u.session : "--");                       row(line);
  snprintf(line, sizeof(line), "Mode    %s", claude::permName(u.perm)[0] ? claude::permName(u.perm) : "--"); row(line);
  snprintf(line, sizeof(line), "Event   %s", u.event[0] ? u.event : "--");                           row(line);
  claude::formatElapsed(claudeMon.sinceRxMs(now) / 1000, t, sizeof(t));
  snprintf(line, sizeof(line), "Update  %s ago", claudeMon.hasData() ? t : "--");                   row(line);
  snprintf(line, sizeof(line), "Alerts  %c%c%c%c %s", settings.ccPermission ? 'P' : '-', settings.ccDone ? 'D' : '-',
           settings.ccFail ? 'F' : '-', settings.ccLost ? 'L' : '-', settings.sound ? "" : "muted"); row(line);
}

void drawClaudeMode(uint32_t now) {
  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);
  display.setTextSize(1);
  if (claudeDetails) { drawClaudeDetails(now); return; }

  char addr[24];
  deviceAddress(addr, sizeof(addr));
  claude::Lines v;
  claude::buildLines(claudeMon, now, addr, v);
  claude::draw::screen(display, v, now, WiFi.status() == WL_CONNECTED);
}

// FNV-1a over the frame buffer: lets the Claude face skip the 23 ms I2C
// transfer when nothing on screen changed since the last frame.
uint32_t frameHash() {
  const uint8_t* b = display.getBuffer();
  uint32_t h = 2166136261u;
  for (int i = 0; i < SCREEN_W * SCREEN_H / 8; i++) { h ^= b[i]; h *= 16777619u; }
  return h;
}

void serviceClaude(uint32_t now) {
  const claude::Link before = claudeMon.link();
  claudeMon.tick(now);
  if (claudeMon.link() != before) {
    redrawNow = true;
    LOG("claude: link %s\n", claude::linkName(claudeMon.link()));
  }
  const uint8_t a = claudeMon.takeAlerts();
  // One sound per batch, most urgent first.
  if      ((a & claude::AlertFailure)    && settings.ccFail)       queueClaudeAlert(SFX_CC_FAIL);
  else if ((a & claude::AlertPermission) && settings.ccPermission) queueClaudeAlert(SFX_CC_PERMISSION);
  else if ((a & claude::AlertComplete)   && settings.ccDone)       queueClaudeAlert(SFX_CC_DONE);
  else if ((a & claude::AlertLinkLost)   && settings.ccLost)       queueClaudeAlert(SFX_CC_LOST);
}

// ---- Eyes: drawing ----
void drawEyes(uint32_t now) {
  engine.update(now);
  if (react == React::Dizzy) {
    // Each eye orbits in the opposite direction; blinks and lids stay from the engine.
    eye::FaceState fs = engine.state();
    const float t = (now - reactT0) * 0.001f;
    const float span = (reactUntil - reactT0) * 0.001f;
    float amp = t < 0.3f ? t / 0.3f : (t > span - 0.5f ? (span - t) / 0.5f : 1.0f);
    if (amp < 0) amp = 0;
    const float w = 2.0f * PI * 1.1f;
    for (int i = 0; i < 2; i++) {
      const float a = (i == 0) ? w * t : -w * t + PI;
      fs.eye[i].yawDeg   = (1 - amp) * fs.eye[i].yawDeg   + amp * 22.0f * cosf(a);
      fs.eye[i].pitchDeg = (1 - amp) * fs.eye[i].pitchDeg + amp * 16.0f * sinf(a);
    }
    fs.headYawDeg = (1 - amp) * fs.headYawDeg + amp * 8.0f * sinf(w * t * 0.5f);
    renderer.render(fs, display.getBuffer());
  } else {
    renderer.render(engine.state(), display.getBuffer());
  }

  if (caption[0] && (int32_t)(captionUntil - now) > 0) {
    display.fillRect(0, 54, SCREEN_W, 10, SSD1306_BLACK);
    display.setTextColor(SSD1306_WHITE);
    centerText(caption, 56, 1);
  }
}

// ---- Overlays ----
// Shoji doors between faces. closed01: 0 = open, 1 = closed.
void drawShoji(float closed01) {
  const int w = (int)lroundf(closed01 * (SCREEN_W / 2));
  if (w <= 0) return;
  for (int side = 0; side < 2; side++) {
    const int x0 = side == 0 ? 0 : SCREEN_W - w;
    const int lead = side == 0 ? x0 + w - 1 : x0;
    const int dir = side == 0 ? -1 : 1;
    display.fillRect(x0, 0, w, SCREEN_H, SSD1306_WHITE);
    for (int k = 1; k <= 3; k++) {
      const int x = lead + dir * 16 * k;
      if (x >= x0 && x < x0 + w) display.drawFastVLine(x, 0, SCREEN_H, SSD1306_BLACK);
    }
    display.drawFastHLine(x0, 21, w, SSD1306_BLACK);
    display.drawFastHLine(x0, 42, w, SSD1306_BLACK);
    display.drawFastHLine(x0, 0, w, SSD1306_BLACK);
    display.drawFastHLine(x0, SCREEN_H - 1, w, SSD1306_BLACK);
    const int frameX = side == 0 ? lead - 1 : lead;
    display.fillRect(frameX, 0, 2, SCREEN_H, SSD1306_BLACK);
  }
}

// Progress toward the 5 s mode switch; on the clock a notch marks the refresh point.
void drawHoldBar(uint32_t now) {
  if (!gestures.pressed() || gestures.modeHoldConsumed()) return;
  const uint32_t held = gestures.heldMs(now);
  const uint32_t from = appMode == MODE_EYES ? HOLD_BAR_EYES_FROM_MS : HOLD_BAR_FROM_MS;
  if (held < from) return;
  const uint32_t span = gestures.config().modeHoldMs - from;
  float p = (float)(held - from) / span;
  if (p > 1) p = 1;
  display.fillRect(8, 55, 112, 9, SSD1306_BLACK);
  display.drawRoundRect(12, 58, 104, 5, 2, SSD1306_WHITE);
  display.drawFastHLine(14, 60, (int)(p * 100), SSD1306_WHITE);
  if (appMode == MODE_CLOCK) {
    const int nx = 14 + (int)((float)(REFRESH_HOLD_MS - from) / span * 100);
    display.drawFastVLine(nx, 55, 2, SSD1306_WHITE);
  }
}

// ---- Modes ----
void enterMode(AppMode m) {
  appMode = m;
  redrawNow = true;
  if (m == MODE_CLOCK) {
    notePage = 0;
    lastNoteFlip = millis();
    requestFetch(false);
  } else if (m == MODE_CLAUDE) {
    claudeDetails = false;
    lastPushedHash = 0;
  } else {
    noteInteraction(false);
    engine.blinkNow();
  }
  LOG("Mode -> %s\n", modeName(m));
}

// The mode the screen is showing or is about to show.
AppMode shownMode() { return trans.active ? trans.to : appMode; }

void requestMode(AppMode m) {
  if (trans.active ? trans.to == m : appMode == m) return;
  endReaction();
  trans.active = true;
  trans.swapped = false;
  trans.t0 = millis();
  trans.to = m;
  sfx(SFX_MODE);
}

// ---- Touch ----
void onEyesGesture(touch::Event e) {
  if (demoOn && e != touch::Event::HoldEnd) setDemo(false);
  switch (e) {
    case touch::Event::Taps: {
      if (engine.sleeping()) { startReaction(React::Wake, 3000); return; }
      const uint8_t n = gestures.taps();
      if (n == 1)      startReaction(React::Glance, 1700);
      else if (n == 2) startReaction(React::Happy, 2600);
      else if (n == 3) startReaction(React::Grumpy, 2600);
      else             startReaction(React::Dizzy, 2600);
      LOG("touch: %u tap(s) -> %s\n", (unsigned)n, reactName(react));
      break;
    }
    case touch::Event::HoldStart:
      if (engine.sleeping()) startReaction(React::Wake, 3000);
      else                   startReaction(React::Pet, 0);
      break;
    case touch::Event::HoldEnd:
      if (react == React::Pet) startReaction(React::Happy, 1400);
      break;
    default:
      break;
  }
}

void onClockGesture(touch::Event e) {
  switch (e) {
    case touch::Event::Taps: {
      const uint8_t n = gestures.taps();
      if (n == 1)      screen = (screen + 1) % SCR_COUNT;
      else if (n == 2) screen = (screen + SCR_COUNT - 1) % SCR_COUNT;
      else             screen = SCR_CLOCK;
      notePage = 0;
      lastNoteFlip = millis();
      redrawNow = true;
      sfx(SFX_PAGE);
      LOG("Screen -> %s\n", kScreenNames[screen]);
      break;
    }
    case touch::Event::HoldEnd:
      if (gestures.lastHoldMs() >= REFRESH_HOLD_MS) {
        sfx(SFX_REFRESH);
        refreshBannerUntil = millis() + 15000;
        requestFetch(true);
        redrawNow = true;
      }
      break;
    default:
      break;
  }
}

// Claude face: a tap shows the details page, another tap goes back. Holds do
// nothing here except the 5 s mode switch, so a resting finger is harmless.
void onClaudeGesture(touch::Event e) {
  if (e != touch::Event::Taps) return;
  claudeDetails = gestures.taps() == 1 ? !claudeDetails : false;
  redrawNow = true;
  sfx(SFX_PAGE);
}

void serviceTouch(uint32_t now) {
  const touch::Event e = gestures.update(digitalRead(PIN_TOUCH) == HIGH, now);

  if (appMode == MODE_CLOCK && gestures.pressed() && !refreshArmed &&
      !gestures.modeHoldConsumed() && gestures.heldMs(now) >= REFRESH_HOLD_MS) {
    refreshArmed = true;
    sfx(SFX_TICK);
  }

  if (e == touch::Event::None) return;
  if (e == touch::Event::Press) {
    refreshArmed = false;
    noteInteraction(false);
    if (appMode == MODE_EYES && !engine.sleeping()) engine.setArousal(0.5f);
    return;
  }
  // ModeHold fires once per press (TouchGestures), so one long hold moves
  // exactly one step: EYES -> CLOCK -> CLAUDE -> EYES.
  if (e == touch::Event::ModeHold) {
    screen = SCR_CLOCK;
    requestMode(nextMode(shownMode()));
    return;
  }
  if (trans.active) return;
  switch (appMode) {
    case MODE_EYES:   onEyesGesture(e);   break;
    case MODE_CLOCK:  onClockGesture(e);  break;
    case MODE_CLAUDE: onClaudeGesture(e); break;
  }
}

// ---- Commands (Serial and web) ----
bool runCommand(const char* line, char* out, size_t outLen) {
  char buf[96];
  strncpy(buf, line, sizeof(buf) - 1);
  buf[sizeof(buf) - 1] = 0;
  char* verb = strtok(buf, " ");
  char* arg  = strtok(nullptr, " ");
  if (!verb) { snprintf(out, outLen, "?"); return false; }

  if (!strcasecmp(verb, "mode")) {
    AppMode m;
    for (char* p = arg; p && *p; ++p) *p = (char)tolower((unsigned char)*p);
    if (!arg)                     requestMode(nextMode(shownMode()));
    else if (parseMode(arg, m))   requestMode(m);
    else { snprintf(out, outLen, "err mode: eyes|clock|claude"); return false; }
    snprintf(out, outLen, "ok mode");
    return true;
  }
  if (!strcasecmp(verb, "demo")) {
    setDemo(arg ? strcasecmp(arg, "off") != 0 : !demoOn);
    snprintf(out, outLen, "ok demo %s", demoOn ? "on" : "off");
    return true;
  }
  if (!strcasecmp(verb, "gesture") && arg) {
    noteInteraction(true);
    setDemo(false);
    requestMode(MODE_EYES);
    React r = React::None;
    uint32_t dur = 2600;
    if (!strcmp(arg, "1"))        { r = React::Glance; dur = 1700; }
    else if (!strcmp(arg, "2"))   r = React::Happy;
    else if (!strcmp(arg, "3"))   r = React::Grumpy;
    else if (!strcmp(arg, "4"))   r = React::Dizzy;
    else if (!strcasecmp(arg, "pet")) { r = React::Pet; dur = 2500; }
    if (r == React::None) { snprintf(out, outLen, "err gesture: 1|2|3|4|pet"); return false; }
    if (engine.sleeping()) engine.setSleeping(false);
    startReaction(r, dur);
    snprintf(out, outLen, "ok gesture %s", reactName(r));
    return true;
  }

  // Engine command: apply on top of the base, then remember the result as the new base.
  noteInteraction(true);
  if (demoOn) setDemo(false);
  endReaction();
  const bool ok = commands.execute(line, out, outLen);
  baseMood = engine.mood();
  baseExpr = engine.expression();
  return ok;
}

// ---- Web server ----
void sendJson(int code, JsonDocument& d) {
  String s;
  serializeJson(d, s);
  server.sendHeader("Cache-Control", "no-store");
  server.send(code, "application/json", s);
}

void sendOk(const char* reply) {
  JsonDocument d;
  d["ok"] = true;
  d["reply"] = reply;
  sendJson(200, d);
}

void sendError(int code, const char* msg) {
  JsonDocument d;
  d["ok"] = false;
  d["error"] = msg;
  sendJson(code, d);
}

void handleRoot() {
  server.sendHeader("Cache-Control", "no-cache");
  server.sendHeader("Content-Encoding", "gzip");
  server.send_P(200, "text/html; charset=utf-8", (PGM_P)INDEX_HTML_GZ, INDEX_HTML_GZ_LEN);
}

void handleFrame() {
  server.sendHeader("Cache-Control", "no-store");
  server.send_P(200, "application/octet-stream", (PGM_P)display.getBuffer(), SCREEN_W * SCREEN_H / 8);
}

void handleState() {
  JsonDocument d;
  d["owner"]  = OWNER_NAME;
  d["mode"]   = modeName(appMode);
  d["screen"] = kScreenNames[screen];

  JsonObject e = d["eye"].to<JsonObject>();
  e["mood"]     = kMoodNames[(int)baseMood];
  e["expr"]     = kExprNames[(int)baseExpr];
  e["state"]    = engine.attentionName();
  e["react"]    = reactName(react);
  e["sleeping"] = engine.sleeping();
  e["drowsy"]   = idleStage == 1;
  e["demo"]     = demoOn;

  struct tm t;
  const bool synced = nowLocal(t);
  JsonObject tm = d["time"].to<JsonObject>();
  tm["synced"] = synced;
  if (synced) {
    char hhmm[8], date[24];
    strftime(hhmm, sizeof(hhmm), "%H:%M", &t);
    strftime(date, sizeof(date), "%a %d %b", &t);
    tm["hhmm"] = hhmm;
    tm["date"] = date;
  }

  JsonObject w = d["wifi"].to<JsonObject>();
  w["ap"]   = apMode;
  w["ssid"] = apMode ? String(AP_SSID) : wifiSsid;
  w["ip"]   = apMode ? WiFi.softAPIP().toString() : WiFi.localIP().toString();
  w["rssi"] = apMode ? 0 : WiFi.RSSI();
  w["up"]   = apMode || WiFi.status() == WL_CONNECTED;
  w["host"] = MDNS_NAME ".local";

  JsonObject o = d["oled"].to<JsonObject>();
  o["ok"]   = haveDisplay;
  o["sda"]  = usedSda;
  o["scl"]  = usedScl;
  o["addr"] = oledAddr;

  {
    DataLock l;
    if (l.ok()) {
      JsonObject x = d["weather"].to<JsonObject>();
      x["ok"] = wx.valid;
      if (wx.valid) {
        x["city"] = wx.city;
        x["temp"] = serialized(String(wx.temp, 1));
        x["unit"] = UNITS_METRIC ? "C" : "F";
        x["word"] = weatherWord(wx.code);
        x["hum"]  = wx.hum;
        x["min"]  = (int)round(wx.tmin);
        x["max"]  = (int)round(wx.tmax);
      }
      JsonArray c = d["coins"].to<JsonArray>();
      for (const Coin& k : coins) {
        JsonObject ko = c.add<JsonObject>();
        ko["tag"] = k.tag;
        ko["ok"]  = k.ok;
        if (k.ok) { ko["usd"] = money(k.usd); ko["chg"] = serialized(String(k.chg, 1)); }
      }
    }
  }
  d["fetching"] = fetchBusy;

  JsonObject n = d["note"].to<JsonObject>();
  n["title"] = noteTitle;
  n["text"]  = noteText;

  JsonObject s = d["settings"].to<JsonObject>();
  s["sound"]     = settings.sound;
  s["autosleep"] = settings.autoSleep;
  s["start"]     = modeName((AppMode)settings.startMode);
  s["cc_perm"]   = settings.ccPermission;
  s["cc_done"]   = settings.ccDone;
  s["cc_fail"]   = settings.ccFail;
  s["cc_lost"]   = settings.ccLost;
  s["cc_stale"]  = settings.ccStaleS;

  {
    const uint32_t now = millis();
    const claude::Update& u = claudeMon.last();
    JsonObject c = d["claude"].to<JsonObject>();
    c["link"]       = claude::linkName(claudeMon.link());
    c["screen"]     = claude::screenName(claude::pickScreen(claudeMon));
    c["state"]      = claude::stateName(u.state);
    c["model"]      = u.model;
    c["effort"]     = claude::effortName(u.effort);
    c["activity"]   = u.activity;
    c["permission_mode"] = claude::permName(u.perm);
    c["permission_label"] = claude::permLabel(u.perm);
    c["approval"]   = u.approval;
    c["elapsed"]    = claudeMon.elapsedS(now);
    c["session_elapsed"] = claudeMon.sessionS(now);
    c["sessions"]   = u.sessions;
    c["session"]    = u.session;
    c["event"]      = u.event;
    c["age_ms"]     = claudeMon.hasData() ? claudeMon.sinceRxMs(now) : 0;
    c["accepted"]   = claudeMon.accepted();
    c["rejected"]   = claudeRejected;
  }

  JsonObject y = d["sys"].to<JsonObject>();
  y["uptime"]  = millis() / 1000;
  y["heap"]    = ESP.getFreeHeap();
  y["minHeap"] = ESP.getMinFreeHeap();
  y["fps"]     = serialized(String(fps, 1));
  y["fetchStackFree"] = fetchTaskHandle ? (uint32_t)uxTaskGetStackHighWaterMark(fetchTaskHandle) : 0;
  sendJson(200, d);
}

void handleEye() {
  if (!server.hasArg("cmd")) { sendError(400, "missing cmd"); return; }
  char reply[200];
  const bool ok = runCommand(server.arg("cmd").c_str(), reply, sizeof(reply));
  if (ok) sendOk(reply); else sendError(400, reply);
}

void handleMode() {
  AppMode m;
  if (parseMode(server.arg("m").c_str(), m)) requestMode(m);
  else                                       requestMode(nextMode(shownMode()));   // "toggle", "next" or nothing
  noteInteraction(true);
  sendOk("ok mode");
}

void handleScreen() {
  const String s = server.arg("s");
  if (s == "next")      screen = (screen + 1) % SCR_COUNT;
  else if (s == "prev") screen = (screen + SCR_COUNT - 1) % SCR_COUNT;
  else {
    bool found = false;
    for (uint8_t i = 0; i < SCR_COUNT; i++)
      if (s == kScreenNames[i]) { screen = i; found = true; }
    if (!found) { sendError(400, "screen: clock|weather|crypto|notes|next|prev"); return; }
  }
  notePage = 0;
  lastNoteFlip = millis();
  redrawNow = true;
  requestMode(MODE_CLOCK);
  noteInteraction(true);
  sendOk("ok screen");
}

void handleNote() {
  if (server.hasArg("text"))  noteText  = server.arg("text");
  if (server.hasArg("title")) noteTitle = server.arg("title");
  noteTitle.trim();
  if (noteTitle.length() == 0) noteTitle = "Notes";
  if (noteText.length() > 1400) noteText = noteText.substring(0, 1400);
  rebuildNoteLines();
  saveNote();
  screen = SCR_NOTES;
  lastNoteFlip = millis();
  redrawNow = true;
  requestMode(MODE_CLOCK);
  sfx(SFX_SAVED);
  noteInteraction(true);
  sendOk("ok note");
}

void handleSettings() {
  if (server.hasArg("sound"))     settings.sound = server.arg("sound") == "1";
  if (server.hasArg("autosleep")) {
    settings.autoSleep = server.arg("autosleep") == "1";
    if (!settings.autoSleep && idleStage == 2) engine.setSleeping(false);
    if (!settings.autoSleep && idleStage == 1) engine.setMood(baseMood);
    if (!settings.autoSleep) idleStage = 0;
  }
  if (server.hasArg("start")) {
    AppMode m;
    settings.startMode = parseMode(server.arg("start").c_str(), m) ? m : MODE_EYES;
  }
  if (server.hasArg("cc_stale")) {
    const long v = server.arg("cc_stale").toInt();
    if (v < CLAUDE_STALE_S_MIN || v > CLAUDE_STALE_S_MAX) { sendError(400, "cc_stale: 5..600 seconds"); return; }
    settings.ccStaleS = (uint16_t)v;
    claudeMon.setStaleMs(settings.ccStaleS * 1000UL);
  }
  if (server.hasArg("cc_perm")) settings.ccPermission = server.arg("cc_perm") == "1";
  if (server.hasArg("cc_done")) settings.ccDone       = server.arg("cc_done") == "1";
  if (server.hasArg("cc_fail")) settings.ccFail       = server.arg("cc_fail") == "1";
  if (server.hasArg("cc_lost")) settings.ccLost       = server.arg("cc_lost") == "1";
  saveSettings();
  if (!settings.sound) { melodyLen = 0; claudeAlertPending = false; noTone(PIN_BUZZER); }
  sendOk("ok settings");
}

// POST /api/claude/status, step 1: WebServer streams the raw body here in
// chunks. Only the first kMaxBody bytes are kept; the rest is drained by
// the library and the request is answered 413 in step 2.
void handleClaudeBody() {
  HTTPRaw& raw = server.raw();
  if (raw.status == RAW_START) {
    ccBodyLen = 0;
    ccBodyTooBig = server.clientContentLength() > (int)claude::kMaxBody;
  } else if (raw.status == RAW_WRITE && !ccBodyTooBig) {
    if (ccBodyLen + raw.currentSize > claude::kMaxBody) { ccBodyTooBig = true; return; }
    memcpy(ccBody + ccBodyLen, raw.buf, raw.currentSize);
    ccBodyLen += raw.currentSize;
  }
}

// Step 2: validate and apply. Runs in the loop task like every handler, and
// parsing ~300 bytes takes well under a millisecond, so the eyes, the timer
// and the buzzer never wait for it. Does not count as interaction: a status
// heartbeat every few seconds must not keep the eyes awake.
void handleClaudeStatus() {
  if (!server.header("Content-Type").startsWith("application/json")) {
    ++claudeRejected;
    sendError(415, "expected Content-Type: application/json");
    return;
  }
  if (ccBodyTooBig) {
    ++claudeRejected;
    sendError(413, "body larger than 1024 bytes");
    return;
  }
  claude::Update u;
  const char* field = "";
  const char* err = "";
  if (!claude::parseUpdate(ccBody, ccBodyLen, u, field, err)) {
    ++claudeRejected;
    char msg[72];
    snprintf(msg, sizeof(msg), "%s: %s", field, err);
    LOG("claude: rejected (%s)\n", msg);
    sendError(400, msg);
    return;
  }
  switch (claudeMon.apply(u, millis())) {
    case claude::Apply::Accepted:
      if (appMode == MODE_CLAUDE) redrawNow = true;
      sendOk("ok claude");
      break;
    case claude::Apply::Repeated: sendOk("ok repeated"); break;
    case claude::Apply::Outdated: sendOk("ok outdated, ignored"); break;
  }
}

void handleRefresh() {
  refreshBannerUntil = millis() + 15000;
  requestFetch(true);
  redrawNow = true;
  sendOk("ok refresh");
}

void handleWifi() {
  const String s = server.arg("ssid");
  const String p = server.arg("pass");
  if (s.length() == 0) { sendError(400, "Enter the network name."); return; }
  saveWifi(s, p);
  sendOk("ok wifi, restarting");
  restartAt = millis() + 1200;   // let the reply reach the browser first
}

void handleNotFound() {
  if (server.uri().startsWith("/api/")) { sendError(404, "unknown endpoint"); return; }
  server.sendHeader("Location", "/");
  server.send(303);
}

void startWebServer() {
  server.on("/",             HTTP_GET,  handleRoot);
  server.on("/api/state",    HTTP_GET,  handleState);
  server.on("/api/frame",    HTTP_GET,  handleFrame);
  server.on("/api/eye",      HTTP_POST, handleEye);
  server.on("/api/mode",     HTTP_POST, handleMode);
  server.on("/api/screen",   HTTP_POST, handleScreen);
  server.on("/api/note",     HTTP_POST, handleNote);
  server.on("/api/settings", HTTP_POST, handleSettings);
  server.on("/api/refresh",  HTTP_POST, handleRefresh);
  server.on("/api/wifi",     HTTP_POST, handleWifi);
  // Raw body (fixed buffer) instead of the default, which would allocate
  // whatever Content-Length the client claims. The filter keeps multipart
  // uploads away from the raw handler.
  static const char* headerKeys[] = {"Content-Type"};
  server.collectHeaders(headerKeys, 1);
  server.on("/api/claude/status", HTTP_POST, handleClaudeStatus, handleClaudeBody)
      .setFilter([](WebServer& s) { return !s.header("Content-Type").startsWith("multipart/"); });
  server.onNotFound(handleNotFound);
  server.begin();
  LOGLN("Web server running.");
}

// ---- Serial console ----
void pollSerial() {
#if SERIAL_SAFE
  static char line[96];
  static size_t len = 0;
  while (Serial.available() > 0) {
    const char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (len == 0) continue;
      line[len] = 0;
      len = 0;
      if (!strcasecmp(line, "help")) {
        Serial.println("app: mode eyes|clock|claude | demo | gesture 1|2|3|4|pet");
      }
      char reply[200];
      runCommand(line, reply, sizeof(reply));
      Serial.println(reply);
    } else if (len < sizeof(line) - 1) {
      line[len++] = c;
    }
  }
#endif
}

// ---- Frame ----
static const uint32_t SHOJI_CLOSE_MS = 200, SHOJI_HOLD_MS = 70, SHOJI_OPEN_MS = 220;

float easeInOut(float u) { u = u < 0 ? 0 : (u > 1 ? 1 : u); return u * u * (3 - 2 * u); }

uint32_t claudeFrameMs() {
  const claude::Screen s = claude::pickScreen(claudeMon);
  return (!claudeDetails && (s == claude::Screen::Running || s == claude::Screen::Approval)) ? CLAUDE_ANIM_MS
                                                                                            : CLAUDE_REDRAW_MS;
}

bool frameDue(uint32_t now) {
  static uint32_t lastFrame = 0;
  const bool fast = appMode == MODE_EYES || trans.active || gestures.pressed();
  const uint32_t interval = fast ? 1000 / EYE_MAX_FPS : (appMode == MODE_CLAUDE ? claudeFrameMs() : CLOCK_REDRAW_MS);
  if (!redrawNow && now - lastFrame < interval) return false;
  lastFrame = now;
  redrawNow = false;
  return true;
}

void renderFrame(uint32_t now) {
  float shoji = 0;
  if (trans.active) {
    const uint32_t el = now - trans.t0;
    if (el < SHOJI_CLOSE_MS) {
      shoji = easeInOut((float)el / SHOJI_CLOSE_MS);
    } else {
      if (!trans.swapped) { trans.swapped = true; enterMode(trans.to); }
      const uint32_t openEl = el - SHOJI_CLOSE_MS - SHOJI_HOLD_MS;
      if (el < SHOJI_CLOSE_MS + SHOJI_HOLD_MS)  shoji = 1;
      else if (openEl < SHOJI_OPEN_MS)          shoji = 1 - easeInOut((float)openEl / SHOJI_OPEN_MS);
      else                                      trans.active = false;
    }
  }

  switch (appMode) {
    case MODE_EYES:   drawEyes(now);       break;
    case MODE_CLOCK:  drawClockMode(now);  break;
    case MODE_CLAUDE: drawClaudeMode(now); break;
  }
  if (shoji > 0) drawShoji(shoji);
  drawHoldBar(now);
  if (appMode == MODE_CLAUDE) {
    // The Claude face changes a few times a second at most; identical frames
    // are not sent again over I2C.
    const uint32_t h = frameHash();
    if (h != lastPushedHash) { lastPushedHash = h; pushFrame(); }
  } else {
    pushFrame();
  }

  static uint32_t fpsT0 = 0, frames = 0;
  if (fpsT0 == 0) fpsT0 = now;
  if (++frames >= 30 || now - fpsT0 > 2000) {
    fps = frames * 1000.0f / (float)((now - fpsT0) ? (now - fpsT0) : 1);
    frames = 0;
    fpsT0 = now;
  }
}

// ---- Setup / loop ----
bool joinWifi(uint32_t timeoutMs) {
  if (wifiSsid.length() == 0 || wifiSsid == "YOUR_WIFI_NAME") {
    LOGLN("No Wi-Fi credentials set (see secrets.example.h).");
    return false;
  }
  LOG("Connecting to \"%s\"\n", wifiSsid.c_str());
  WiFi.persistent(false);
  WiFi.setHostname(MDNS_NAME);   // must come before WiFi.mode() on core 3.x
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.disconnect(true);
  delay(100);
  WiFi.begin(wifiSsid.c_str(), wifiPass.c_str());

  const uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < timeoutMs) {   // eyes keep moving meanwhile
    const uint32_t now = millis();
    char c[24];
    snprintf(c, sizeof(c), "joining wi-fi%.*s", (int)((now - t0) / 400 % 4), "...");
    setCaption(c, 1000);
    serviceSound();
    if (frameDue(now)) renderFrame(now);
    else delay(1);
  }
  if (WiFi.status() == WL_CONNECTED) {
    LOG("Connected. IP: %s  RSSI: %d dBm\n", WiFi.localIP().toString().c_str(), WiFi.RSSI());
    return true;
  }
  LOG("Wi-Fi failed: %s\n", wifiReason((wl_status_t)WiFi.status()));
  return false;
}

void setup() {
#if SERIAL_SAFE
  Serial.begin(115200);
  delay(1500);
  Serial.println("\n=== MochiEyes booting ===");
#endif

  pinMode(PIN_TOUCH, INPUT);
  pinMode(PIN_BUZZER, OUTPUT);
  loadConfig();
  beepBlocking(1500, 80);                 // 1 beep: alive

  haveDisplay = startDisplay();
  display.allocBuffer();
  display.setTextColor(SSD1306_WHITE);
  if (haveDisplay) {
    beepBlocking(1900, 80);               // 2nd beep: OLED found
    display.clearDisplay();
    centerText("Hello, " OWNER_NAME, 26, 1);
    display.display();
    delay(700);
  } else {
    LOGLN("No OLED. Continuing without it - Wi-Fi and web page still work.");
    beepBlocking(400, 400);               // long low beep: no OLED
  }

  dataLock = xSemaphoreCreateMutex();
  renderer.setGeometry(eye::mochiGeometry());
  engine.begin((uint32_t)random(1, 0x7FFFFFFF), millis());
  engine.blinkNow();
  appMode = MODE_EYES;
  lastInteraction = millis();

  if (!joinWifi(20000)) {
    startAP();
    setCaption("AP " AP_SSID " 192.168.4.1", 10000);
  } else {
    beepBlocking(2400, 80);               // high beep: online
    setCaption(WiFi.localIP().toString().c_str(), 8000);
  }

  configTzTime(TZ_STRING, "pool.ntp.org", "time.google.com", "time.nist.gov");
  if (MDNS.begin(MDNS_NAME)) MDNS.addService("http", "tcp", 80);
  startWebServer();

  xTaskCreate(fetchTask, "fetch", 12288, nullptr, 1, &fetchTaskHandle);
  requestFetch(true);

  claudeMon.setStaleMs(settings.ccStaleS * 1000UL);
  if (settings.startMode != MODE_EYES) requestMode((AppMode)settings.startMode);
}

void loop() {
  const uint32_t now = millis();
  server.handleClient();
  pollSerial();
  serviceTouch(now);
  serviceSound();
  serviceReaction(now);
  serviceIdle(now);
  serviceClaude(now);   // in every mode, so alerts and the link watch keep running
  if (demoOn) runDemo(now);

  if (appMode == MODE_CLOCK && screen == SCR_NOTES && now - lastNoteFlip > NOTE_PAGE_MS) {
    uint16_t pages = (noteLines.size() + 5) / 6;
    if (pages > 1) { notePage = (notePage + 1) % pages; redrawNow = true; }
    lastNoteFlip = now;
  }

  static uint32_t lastRetry = 0;
  if (!apMode && WiFi.status() != WL_CONNECTED && now - lastRetry > 30000) {
    lastRetry = now;
    LOGLN("Wi-Fi dropped, reconnecting.");
    WiFi.reconnect();
  }

  static uint32_t lastBeat = 0;
  if (now - lastBeat > 10000) {
    lastBeat = now;
    LOG("alive  mode=%s  fps=%.1f  oled=%d  wifi=%d  heap=%u  claude=%s\n", modeName(appMode),
        fps, haveDisplay, WiFi.status() == WL_CONNECTED, (unsigned)ESP.getFreeHeap(),
        claude::linkName(claudeMon.link()));
  }

  if (restartAt && (int32_t)(now - restartAt) >= 0) ESP.restart();

  if (frameDue(now)) renderFrame(now);
  else delay(1);   // yield to fetchTask and Wi-Fi
}
