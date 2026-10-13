// claude_screens.cpp - draws the Claude Code screens on a PC with the
// firmware's own code (src/claude/ClaudeView.h + ClaudeDraw.h) and the real
// Adafruit GFX library, and writes raw OLED frames (1024 bytes each, SSD1306
// page layout, the same format as frames.cpp and GET /api/frame).
//
// It also checks every line of every screen against the real font metrics
// (getTextBounds): exit code 1 if anything would run off the 128x64 panel.
//
// Two outputs:
//   <out>.bin         a scripted session at 8 fps (for serve.py --frames and the GIF)
//   <out>-gallery.bin one frame per screen state (for the screenshot sheet)
//
// Build from the repository root (Adafruit GFX 1.12.6 and ArduinoJson 7.4.3 sources):
//   F=firmware/MochiEyes; GFX=<Adafruit GFX dir>; AJ=<ArduinoJson dir>/src
//   g++ -std=c++17 -O2 -DARDUINO=100 -DARDUINOJSON_ENABLE_ARDUINO_STRING=0 \
//       -DARDUINOJSON_ENABLE_ARDUINO_STREAM=0 -DARDUINOJSON_ENABLE_ARDUINO_PRINT=0 \
//       -DARDUINOJSON_ENABLE_PROGMEM=0 -Itools/preview/arduino_shim -I$GFX -I$AJ -I$F \
//       tools/preview/claude_screens.cpp $GFX/Adafruit_GFX.cpp -o build/claude_screens
//   ./build/claude_screens build/claude
// Then: python tools/preview/render_png.py (see that file) for PNG and GIF.
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#include "src/claude/ClaudeDraw.h"

using namespace claude;

namespace {

int overflows = 0;

std::string msg(const char* state, uint32_t seq, const std::string& extra) {
  return "{\"v\":1,\"seq\":" + std::to_string(seq) + ",\"cid\":\"demo\",\"state\":\"" + state +
         "\",\"session_id\":\"3f2a9c1e\"" + (extra.empty() ? "" : "," + extra) + "}";
}

void apply(Monitor& m, const char* state, uint32_t seq, const std::string& extra, uint32_t now) {
  Update u;
  const char* field = "";
  const char* err = "";
  const std::string body = msg(state, seq, extra);
  if (!parseUpdate(body.data(), body.size(), u, field, err)) {
    std::printf("bad fixture %s: %s %s\n", state, field, err);
    ++overflows;
    return;
  }
  m.apply(u, now);
}

// Width check with the library's own metrics, at the x each line is drawn at.
void checkWidth(Adafruit_GFX& g, const char* s, int16_t x, uint8_t size, const char* where) {
  if (!s || !*s) return;
  int16_t x1, y1;
  uint16_t w, h;
  g.setTextSize(size);
  g.getTextBounds(s, x, 0, &x1, &y1, &w, &h);
  if (x1 + (int16_t)w > g.width()) {
    std::printf("  OVERFLOW %s: \"%s\" ends at x=%d\n", where, s, x1 + (int)w);
    ++overflows;
  }
}

void checkLines(Adafruit_GFX& g, const Lines& v) {
  const char* name = screenName(v.screen);
  checkWidth(g, v.header, 2, 1, name);
  checkWidth(g, v.big, 0, 2, name);
  const int16_t l1x = v.screen == Screen::Approval ? 20 : (v.screen == Screen::Done ? 13 : 0);
  checkWidth(g, v.l1, l1x, 1, name);
  checkWidth(g, v.l2, 0, 1, name);
  checkWidth(g, v.l3, 0, 1, name);
  checkWidth(g, v.time, 0, 2, name);
  if (v.badge[0] && v.time[0]) {
    int16_t x1, y1;
    uint16_t w, h;
    g.setTextSize(2);
    g.getTextBounds(v.time, 0, 47, &x1, &y1, &w, &h);
    if ((int)w > g.width() - (int)strlen(v.badge) * 6) {
      std::printf("  OVERLAP %s: timer %s meets label %s\n", name, v.time, v.badge);
      ++overflows;
    }
  }
}

// GFXcanvas1 is row-major, MSB first; the OLED buffer is 8-pixel pages.
void toPages(const GFXcanvas1& c, std::vector<uint8_t>& out) {
  out.assign(1024, 0);
  for (int16_t y = 0; y < 64; ++y)
    for (int16_t x = 0; x < 128; ++x)
      if (c.getPixel(x, y)) out[(y >> 3) * 128 + x] |= (uint8_t)(1 << (y & 7));
}

struct Writer {
  FILE* f;
  int frames = 0;
  std::vector<uint8_t> page;
  void frame(GFXcanvas1& c, const Monitor& m, uint32_t now) {
    Lines v;
    buildLines(m, now, "192.168.1.42", v);
    checkLines(c, v);
    draw::screen(c, v, now, true);
    toPages(c, page);
    fwrite(page.data(), 1, page.size(), f);
    ++frames;
  }
};

const char* kRun = "\"model\":\"Opus\",\"effort\":\"high\",\"permission_mode\":\"default\",\"sessions\":1";

}  // namespace

int main(int argc, char** argv) {
  const std::string base = argc > 1 ? argv[1] : "claude";
  GFXcanvas1 canvas(128, 64);

  // ---- a scripted session, 8 frames per second ----
  FILE* f = std::fopen((base + ".bin").c_str(), "wb");
  if (!f) { std::perror(base.c_str()); return 1; }
  Writer w{f};
  Monitor m;
  uint32_t seq = 0;
  const uint32_t step = 125;
  uint32_t t = 0;
  auto run = [&](uint32_t ms) { for (uint32_t end = t + ms; t < end; t += step) w.frame(canvas, m, t); };

  apply(m, "running", ++seq, std::string(kRun) + ",\"activity\":\"Thinking...\",\"elapsed_seconds\":748", t);
  run(1500);
  apply(m, "running", ++seq, std::string(kRun) + ",\"activity\":\"Running tests\",\"elapsed_seconds\":750", t);
  run(3000);
  apply(m, "approval", ++seq, std::string(kRun) + ",\"activity\":\"Run: rm\",\"elapsed_seconds\":753", t);
  run(3000);
  apply(m, "running", ++seq, std::string(kRun) + ",\"activity\":\"Editing code\",\"elapsed_seconds\":756", t);
  run(2000);
  apply(m, "task_done", ++seq, std::string(kRun) + ",\"activity\":\"Task completed\",\"elapsed_seconds\":758", t);
  run(3000);
  std::fclose(f);
  std::printf("wrote %d frames (8 fps) to %s.bin\n", w.frames, base.c_str());

  // ---- one frame per screen, for the gallery ----
  FILE* g = std::fopen((base + "-gallery.bin").c_str(), "wb");
  if (!g) { std::perror(base.c_str()); return 1; }
  Writer gw{g};
  struct Shot { const char* state; const char* extra; uint32_t at; bool stale; bool never; };
  const Shot shots[] = {
      {"running", "\"activity\":\"Running tests\",\"elapsed_seconds\":763", 0, false, false},
      {"approval", "\"activity\":\"Run: npm\",\"elapsed_seconds\":771", 0, false, false},
      {"task_done", "\"activity\":\"Task completed\",\"elapsed_seconds\":842", 0, false, false},
      {"turn_done", "\"activity\":\"Background work\",\"elapsed_seconds\":604", 0, false, false},
      {"failed", "\"activity\":\"Rate limited\",\"elapsed_seconds\":95", 0, false, false},
      {"idle", "", 0, false, false},
      {"running", "\"activity\":\"Running tests\",\"elapsed_seconds\":763", 42000, true, false},
      {"idle", "\"connected\":false", 0, false, false},
      {"", "", 0, false, true},
  };
  uint32_t gseq = 100;
  for (const Shot& s : shots) {
    Monitor gm;
    if (!s.never) {
      std::string extra = std::string(kRun);
      if (s.extra[0]) extra += std::string(",") + s.extra;
      apply(gm, s.state, ++gseq, extra, 1000);
      if (s.stale) gm.tick(1000 + s.at);
    }
    gw.frame(canvas, gm, 1000 + s.at);
  }
  std::fclose(g);
  std::printf("wrote %d gallery frames to %s-gallery.bin\n", gw.frames, base.c_str());

  if (overflows) {
    std::printf("%d layout problem(s)\n", overflows);
    return 1;
  }
  std::printf("all lines fit the 128x64 panel\n");
  return 0;
}
