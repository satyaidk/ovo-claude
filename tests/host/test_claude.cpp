// test_claude.cpp - host unit tests for the Claude Code monitor mode:
//   src/app/AppModes.h        the EYES -> CLOCK -> CLAUDE cycle
//   src/claude/ClaudeStatus.h the POST /api/claude/status schema and validation
//   src/claude/ClaudeMonitor.h ordering, link health, local timer, alerts
//   src/claude/ClaudeView.h   screen choice and line fitting for 128x64
//
// Like test_touch.cpp, time is passed in, so every timeline is scripted.
//
// Build and run (needs ArduinoJson 7 on the include path; any C++11 compiler):
//   g++ -std=c++11 -Wall -Wextra -I../../firmware/MochiEyes -I<ArduinoJson>/src test_claude.cpp -o test_claude
//   ./test_claude
#include <cstdio>
#include <cstring>
#include <string>
#include <utility>
#include <vector>

#include "src/app/AppModes.h"
#include "src/claude/ClaudeMonitor.h"
#include "src/claude/ClaudeStatus.h"
#include "src/claude/ClaudeView.h"
#include "src/touch/TouchGestures.h"

using namespace claude;

namespace {

int failures = 0, checks = 0;

void check(bool ok, const std::string& what) {
  ++checks;
  if (!ok) {
    ++failures;
    std::printf("  FAIL: %s\n", what.c_str());
  }
}

bool parse(const std::string& body, Update& u, std::string* fieldOut = nullptr) {
  const char* field = "";
  const char* err = "";
  const bool ok = parseUpdate(body.data(), body.size(), u, field, err);
  if (fieldOut) *fieldOut = field;
  return ok;
}

bool rejects(const std::string& body, const std::string& expectField, const std::string& what) {
  Update u;
  std::string field;
  const bool ok = parse(body, u, &field);
  check(!ok, "rejects " + what);
  check(field == expectField, what + ": blames '" + expectField + "' (got '" + field + "')");
  return !ok;
}

// A complete message with the given state; seq and session vary per test.
std::string msg(const char* state, uint32_t seq, const char* extra = "", const char* session = "s1",
                const char* cid = "c1") {
  char buf[512];
  std::snprintf(buf, sizeof(buf),
                "{\"v\":1,\"seq\":%u,\"cid\":\"%s\",\"state\":\"%s\",\"session_id\":\"%s\"%s%s}",
                (unsigned)seq, cid, state, session, extra[0] ? "," : "", extra);
  return buf;
}

Update upd(const char* state, uint32_t seq, const char* extra = "", const char* session = "s1",
           const char* cid = "c1") {
  Update u;
  const bool ok = parse(msg(state, seq, extra, session, cid), u);
  check(ok, std::string("fixture parses: ") + state);
  return u;
}

// ---------------------------------------------------------------- modes ----

void testModeCycle() {
  std::printf("three-mode cycle EYES -> CLOCK -> CLAUDE -> EYES\n");
  check(nextMode(MODE_EYES) == MODE_CLOCK, "eyes -> clock");
  check(nextMode(MODE_CLOCK) == MODE_CLAUDE, "clock -> claude");
  check(nextMode(MODE_CLAUDE) == MODE_EYES, "claude -> eyes");
  check(nextMode((AppMode)7) == MODE_EYES, "out-of-range restarts at eyes");

  AppMode m = MODE_EYES;
  for (int i = 0; i < 3; ++i) m = nextMode(m);
  check(m == MODE_EYES, "three steps return to the start");

  AppMode p = MODE_EYES;
  check(parseMode("claude", p) && p == MODE_CLAUDE, "parse claude");
  check(parseMode("clock", p) && p == MODE_CLOCK, "parse clock");
  check(parseMode("eyes", p) && p == MODE_EYES, "parse eyes");
  check(!parseMode("toggle", p) && !parseMode("", p) && !parseMode(nullptr, p), "unknown names rejected");
  check(!std::strcmp(modeName(MODE_CLAUDE), "claude"), "name of claude");
  check(modeFromByte(2) == MODE_CLAUDE && modeFromByte(9) == MODE_EYES, "stored byte validated");
}

// The sketch maps TouchGestures events to modes: ModeHold -> nextMode(),
// Taps -> the current face's handler. Replay that wiring on scripted touches.
struct Device {
  touch::TouchGestures g;
  AppMode mode = MODE_EYES;
  int transitions = 0;
  std::vector<std::pair<AppMode, uint8_t> > taps;   // (mode, count) per Taps event
  uint32_t t = 1000;

  void run(bool on, uint32_t ms) {
    for (uint32_t i = 0; i < ms; ++i, ++t) {
      const touch::Event e = g.update(on, t);
      if (e == touch::Event::ModeHold) { mode = nextMode(mode); ++transitions; }
      if (e == touch::Event::Taps) taps.push_back(std::make_pair(mode, g.taps()));
    }
  }
};

void testLongHoldOneTransition() {
  std::printf("one continuous hold moves exactly one mode, however long\n");
  Device d;
  d.run(false, 50);
  d.run(true, 15000);   // three times the mode-hold time
  check(d.transitions == 1, "15 s hold -> exactly one transition");
  check(d.mode == MODE_CLOCK, "landed on clock");
  d.run(false, 1000);
  check(d.transitions == 1, "release adds nothing");

  std::printf("each new hold advances once: EYES -> CLOCK -> CLAUDE -> EYES\n");
  d.run(true, 5200);  d.run(false, 600);
  check(d.mode == MODE_CLAUDE, "second hold -> claude");
  d.run(true, 5200);  d.run(false, 600);
  check(d.mode == MODE_EYES, "third hold -> eyes");
  check(d.transitions == 3, "three holds, three transitions");

  std::printf("a 4.9 s hold does not switch\n");
  Device e;
  e.run(false, 50);
  e.run(true, 4900);
  e.run(false, 600);
  check(e.transitions == 0 && e.mode == MODE_EYES, "no transition below 5 s");
}

void testTapsStillWork() {
  std::printf("tap gestures still arrive in every mode\n");
  Device d;
  d.run(false, 50);
  const AppMode order[] = {MODE_EYES, MODE_CLOCK, MODE_CLAUDE};
  for (int k = 0; k < 3; ++k) {
    for (int n = 1; n <= 3; ++n) {      // 1, 2 and 3 taps
      for (int i = 0; i < n; ++i) { d.run(true, 90); d.run(false, 140); }
      d.run(false, 600);
    }
    if (k < 2) { d.run(true, 5100); d.run(false, 600); }
  }
  check(d.taps.size() == 9, "nine tap bursts recognised");
  bool countsOk = d.taps.size() == 9;
  for (size_t i = 0; countsOk && i < d.taps.size(); ++i)
    countsOk = d.taps[i].second == (uint8_t)(i % 3 + 1) && d.taps[i].first == order[i / 3];
  check(countsOk, "counts 1,2,3 in eyes, clock and claude");
}

// ------------------------------------------------------------- protocol ----

void testParseValid() {
  std::printf("a complete message parses\n");
  Update u;
  const bool ok = parse(
      "{\"v\":1,\"seq\":42,\"cid\":\"a1b2c3d4\",\"connected\":true,\"state\":\"running\","
      "\"session_id\":\"3f2a9c1e\",\"sessions\":2,\"event\":\"PreToolUse\",\"model\":\"Opus 4.7\","
      "\"effort\":\"xhigh\",\"activity\":\"Running tests\",\"permission_mode\":\"default\","
      "\"approval_required\":false,\"elapsed_seconds\":763,\"session_seconds\":4000}",
      u);
  check(ok, "parses");
  check(u.seq == 42 && !std::strcmp(u.cid, "a1b2c3d4"), "seq and cid");
  check(u.state == State::Running && u.connected, "state and connected");
  check(!std::strcmp(u.model, "Opus 4.7") && u.effort == Effort::XHigh, "model and effort");
  check(!std::strcmp(u.activity, "Running tests"), "activity");
  check(u.perm == PermMode::Default && !std::strcmp(permLabel(u.perm), "MANUAL"), "default shows MANUAL");
  check(u.elapsedS == 763 && u.sessionS == 4000 && u.sessions == 2, "numbers");

  std::printf("only v, seq, cid and state are required; the rest defaults\n");
  Update m;
  check(parse("{\"v\":1,\"seq\":0,\"cid\":\"x\",\"state\":\"idle\"}", m), "minimal message parses");
  check(m.model[0] == 0 && m.activity[0] == 0 && m.effort == Effort::Unknown && m.perm == PermMode::Unknown,
        "missing text fields are empty, enums unknown");
  check(m.connected && !m.approval && m.elapsedS == 0, "defaults: connected, no approval, 0 s");

  std::printf("null counts as missing, unknown keys are ignored\n");
  Update n;
  check(parse("{\"v\":1,\"seq\":1,\"cid\":\"x\",\"state\":\"idle\",\"model\":null,\"future\":{\"a\":1}}", n),
        "null and extra keys accepted");

  std::printf("approval state implies approval_required\n");
  Update a;
  check(parse(msg("approval", 3), a) && a.approval, "approval flag set");

  std::printf("every permission mode maps to its own label; only auto is AUTO\n");
  const char* raw[] = {"default", "plan", "acceptEdits", "auto", "dontAsk", "bypassPermissions", "other"};
  const char* label[] = {"MANUAL", "PLAN", "EDITS", "AUTO", "DONTASK", "BYPASS", "OTHER"};
  for (int i = 0; i < 7; ++i) {
    Update p;
    std::string extra = std::string("\"permission_mode\":\"") + raw[i] + "\"";
    check(parse(msg("running", 1, extra.c_str()), p) && !std::strcmp(permLabel(p.perm), label[i]),
          std::string(raw[i]) + " -> " + label[i]);
  }
  Update e;
  const char* eff[] = {"low", "medium", "high", "xhigh", "max"};
  for (int i = 0; i < 5; ++i) {
    std::string extra = std::string("\"effort\":\"") + eff[i] + "\"";
    check(parse(msg("running", 1, extra.c_str()), e) && !std::strcmp(effortName(e.effort), eff[i]),
          std::string("effort ") + eff[i]);
  }

  std::printf("non-printable bytes in free text become '?'\n");
  Update q;
  check(parse("{\"v\":1,\"seq\":1,\"cid\":\"x\",\"state\":\"running\",\"activity\":\"a\\tb\\u00e9\"}", q),
        "parses");
  check(!std::strcmp(q.activity, "a?b??"), "tab and UTF-8 replaced (got '" + std::string(q.activity) + "')");
}

void testParseRejects() {
  std::printf("malformed and oversized bodies are rejected\n");
  rejects("", "body", "empty body");
  rejects("{\"v\":1", "body", "truncated JSON");
  rejects("not json", "body", "plain text");
  rejects("[1,2,3]", "body", "an array");
  rejects("\"running\"", "body", "a string");
  rejects(std::string(1025, ' '), "body", "1025 bytes");
  rejects("{\"v\":1,\"seq\":1,\"cid\":\"x\",\"state\":\"idle\",\"a\":{\"b\":{\"c\":1}}}", "body", "deep nesting");

  std::printf("required fields\n");
  rejects("{\"seq\":1,\"cid\":\"x\",\"state\":\"idle\"}", "v", "missing v");
  rejects("{\"v\":2,\"seq\":1,\"cid\":\"x\",\"state\":\"idle\"}", "v", "version 2");
  rejects("{\"v\":1,\"cid\":\"x\",\"state\":\"idle\"}", "seq", "missing seq");
  rejects("{\"v\":1,\"seq\":1,\"state\":\"idle\"}", "cid", "missing cid");
  rejects("{\"v\":1,\"seq\":1,\"cid\":\"x\"}", "state", "missing state");
  rejects("{\"v\":1,\"seq\":1,\"cid\":\"\",\"state\":\"idle\"}", "cid", "empty cid");
  rejects("{\"v\":1,\"seq\":1,\"cid\":\"a b\",\"state\":\"idle\"}", "cid", "cid with a space");

  std::printf("types\n");
  rejects("{\"v\":1,\"seq\":\"1\",\"cid\":\"x\",\"state\":\"idle\"}", "seq", "seq as string");
  rejects(msg("idle", 1, "\"approval_required\":\"yes\""), "approval_required", "approval as string");
  rejects(msg("idle", 1, "\"connected\":1"), "connected", "connected as number");
  rejects(msg("idle", 1, "\"elapsed_seconds\":-5"), "elapsed_seconds", "negative elapsed");
  rejects(msg("idle", 1, "\"elapsed_seconds\":1.5"), "elapsed_seconds", "fractional elapsed");
  rejects(msg("idle", 1, "\"elapsed_seconds\":2592001"), "elapsed_seconds", "elapsed over 30 days");
  rejects(msg("idle", 1, "\"sessions\":100"), "sessions", "100 sessions");
  rejects(msg("idle", 1, "\"model\":7"), "model", "model as number");

  std::printf("lengths\n");
  const std::string m25 = "\"model\":\"" + std::string(25, 'm') + "\"";
  const std::string a33 = "\"activity\":\"" + std::string(33, 'a') + "\"";
  const std::string s17 = "\"session_id\":\"" + std::string(17, 's') + "\"";
  rejects(msg("idle", 1, m25.c_str()), "model", "25-char model");
  rejects(msg("idle", 1, a33.c_str()), "activity", "33-char activity");
  rejects("{\"v\":1,\"seq\":1,\"cid\":\"x\",\"state\":\"idle\"," + s17 + "}", "session_id", "17-char session");
  Update ok;
  const std::string a32 = "\"activity\":\"" + std::string(32, 'a') + "\"";
  check(parse(msg("idle", 1, a32.c_str()), ok), "32-char activity is accepted");

  std::printf("allowed values\n");
  rejects(msg("thinking", 1), "state", "unknown state");
  rejects(msg("idle", 1, "\"effort\":\"ultra\""), "effort", "effort 'ultra' (not a Claude Code value)");
  rejects(msg("idle", 1, "\"effort\":\"HIGH\""), "effort", "upper-case effort");
  rejects(msg("idle", 1, "\"permission_mode\":\"yolo\""), "permission_mode", "unknown permission mode");
  rejects(msg("idle", 1, "\"event\":\"Pre Tool\""), "event", "event with a space");

  std::printf("a rejected message leaves the previous value untouched\n");
  Update keep = upd("running", 5);
  const char* f = "";
  const char* err = "";
  const std::string bad = msg("idle", 6, "\"effort\":\"ultra\"");
  check(!parseUpdate(bad.data(), bad.size(), keep, f, err) && keep.seq == 5 && keep.state == State::Running,
        "out not modified on failure");
}

// -------------------------------------------------------------- monitor ----

void testOrdering() {
  std::printf("older seq ignored, same seq refreshes, new cid restarts\n");
  Monitor m;
  check(m.apply(upd("running", 10), 1000) == Apply::Accepted, "seq 10 accepted");
  check(m.apply(upd("task_done", 9), 1100) == Apply::Outdated, "seq 9 is outdated");
  check(m.last().state == State::Running, "state not rolled back");
  check(m.apply(upd("running", 10), 1200) == Apply::Repeated, "seq 10 again is a repeat");
  check(m.apply(upd("approval", 11), 1300) == Apply::Accepted, "seq 11 accepted");
  check(m.apply(upd("idle", 1, "", "s1", "c2"), 1400) == Apply::Accepted, "new companion run, seq 1, accepted");
  check(m.last().state == State::Idle && m.outdated() == 1, "restart applied, one outdated counted");
}

void testStaleLink() {
  std::printf("link: never -> live -> stale -> live, closed on goodbye\n");
  Monitor m;  // staleMs 15000
  check(m.link() == Link::Never, "never before the first message");
  m.apply(upd("running", 1), 1000);
  check(m.link() == Link::Live, "live after a message");
  m.tick(15999);
  check(m.link() == Link::Live, "still live at 14.999 s");
  m.tick(16000);
  check(m.link() == Link::Stale, "stale at 15 s");
  check(pickScreen(m) == Screen::Stale, "stale screen, not the running one");
  check((m.takeAlerts() & AlertLinkLost) != 0, "link-lost alert raised");
  m.tick(30000);
  check(m.takeAlerts() == AlertNone, "lost alert only once per loss");
  m.apply(upd("running", 2), 31000);
  check(m.link() == Link::Live, "next message revives the link");
  m.tick(46000);
  check((m.takeAlerts() & AlertLinkLost) != 0, "a second loss alerts again");

  Monitor c;
  c.apply(upd("running", 1), 1000);
  c.apply(upd("idle", 2, "\"connected\":false"), 2000);
  check(c.link() == Link::Closed && pickScreen(c) == Screen::Closed, "goodbye shows DISCONNECTED");
  c.tick(100000);
  check(c.link() == Link::Closed && c.takeAlerts() == AlertNone, "closed never turns stale or alerts");

  std::printf("a now read just before the message arrived does not look like 49 days\n");
  Monitor e;
  e.apply(upd("running", 1, "\"elapsed_seconds\":10"), 5000);
  e.tick(4998);
  check(e.link() == Link::Live && e.elapsedS(4998) == 10, "now < receipt counts as zero age");
}

void testAlerts() {
  std::printf("permission alert: once per request, not per heartbeat\n");
  Monitor m;
  m.apply(upd("running", 1), 1000);
  check(m.takeAlerts() == AlertNone, "running alone is silent");
  m.apply(upd("approval", 2), 2000);
  check(m.takeAlerts() == AlertPermission, "entering approval alerts");
  for (uint32_t s = 3; s < 10; ++s) m.apply(upd("approval", s), 2000 + s * 5000);   // heartbeats
  check(m.takeAlerts() == AlertNone, "heartbeats during approval are silent");
  m.apply(upd("running", 10, "\"approval_required\":true"), 60000);
  check(m.takeAlerts() == AlertNone, "approval flag on a running state is the same request");

  std::printf("permission debounce: a second request within 4 s stays silent\n");
  Monitor d;
  d.apply(upd("approval", 1), 1000);
  d.apply(upd("running", 2), 1500);
  d.apply(upd("approval", 3), 3000);
  check(d.takeAlerts() == AlertPermission, "only the first of two quick requests beeps");
  d.apply(upd("running", 4), 6000);
  d.apply(upd("approval", 5), 9000);
  check(d.takeAlerts() == AlertPermission, "a request 8 s later beeps again");

  std::printf("completion and failure alert on the transition only\n");
  Monitor c;
  c.apply(upd("running", 1), 1000);
  c.apply(upd("task_done", 2), 2000);
  check(c.takeAlerts() == AlertComplete, "running -> task_done");
  c.apply(upd("task_done", 3), 7000);
  check(c.takeAlerts() == AlertNone, "repeated done is silent");
  c.apply(upd("running", 4), 8000);
  c.apply(upd("turn_done", 5), 9000);
  check(c.takeAlerts() == AlertComplete, "running -> turn_done");
  c.apply(upd("running", 6), 10000);
  c.apply(upd("failed", 7), 11000);
  check(c.takeAlerts() == AlertFailure, "running -> failed");
  c.apply(upd("ended", 8), 12000);
  check(c.takeAlerts() == AlertNone, "session end is silent");

  std::printf("no completion alert for a session the device never saw running\n");
  Monitor b;
  b.apply(upd("task_done", 1), 1000);
  check(b.takeAlerts() == AlertNone, "first message after boot is silent");
  Monitor s;
  s.apply(upd("running", 1, "", "A"), 1000);
  s.apply(upd("task_done", 2, "", "B"), 2000);
  check(s.takeAlerts() == AlertNone, "primary switched to another session that is done: silent");
}

void testTimer() {
  std::printf("the task timer ticks on the device between messages\n");
  Monitor m;
  m.apply(upd("running", 1, "\"elapsed_seconds\":100,\"session_seconds\":500"), 10000);
  check(m.elapsedS(10000) == 100, "100 s at receipt");
  check(m.elapsedS(13999) == 103, "103 s four seconds later, no message needed");
  check(m.sessionS(13999) == 503, "session time ticks too");
  m.apply(upd("running", 2, "\"elapsed_seconds\":105,\"session_seconds\":505"), 15000);
  check(m.elapsedS(15000) == 105, "re-anchored by the heartbeat");

  std::printf("the timer stops when the task is done, and while the link is stale\n");
  m.apply(upd("task_done", 3, "\"elapsed_seconds\":107,\"session_seconds\":507"), 17000);
  check(m.elapsedS(40000) == 107, "frozen at the task's length");
  check(m.sessionS(20000) == 510, "the session is still open, its time ticks");
  Monitor s;
  s.apply(upd("running", 1, "\"elapsed_seconds\":50"), 1000);
  s.tick(17000);
  check(s.link() == Link::Stale && s.elapsedS(60000) == 50, "stale freezes at the last reported value");

  std::printf("works across the 49-day millis() wrap\n");
  Monitor w;
  const uint32_t t0 = 0xFFFFFFFFu - 1500;
  w.apply(upd("running", 1, "\"elapsed_seconds\":10"), t0);
  w.tick(t0 + 3000);
  check(w.link() == Link::Live && w.elapsedS(t0 + 3000) == 13, "3 s across the wrap");
  w.tick(t0 + 16000);
  check(w.link() == Link::Stale, "goes stale across the wrap");
}

void testFormatElapsed() {
  std::printf("timer format MM:SS, then H:MM:SS\n");
  struct Case { uint32_t s; const char* want; } cases[] = {
      {0, "00:00"}, {9, "00:09"}, {59, "00:59"}, {60, "01:00"}, {763, "12:43"}, {3599, "59:59"},
      {3600, "1:00:00"}, {3661, "1:01:01"}, {36061, "10:01:01"}, {359999, "99:59:59"}, {360000, "100:00:00"}};
  for (const Case& c : cases) {
    char buf[12];
    formatElapsed(c.s, buf, sizeof(buf));
    check(!std::strcmp(buf, c.want), std::to_string(c.s) + " s -> " + c.want + " (got " + buf + ")");
  }
  char small[4];
  formatElapsed(763, small, sizeof(small));
  check(std::strlen(small) == 3, "never overflows a short buffer");
}

// ----------------------------------------------------------------- view ----

void checkFits(const Lines& v, const std::string& name) {
  check(std::strlen(v.header) > 0 && std::strlen(v.header) <= kHeaderCols, name + ": header fits");
  check(std::strlen(v.big) <= kBigCols, name + ": big text fits");
  check(std::strlen(v.l1) <= kSmallCols && std::strlen(v.l2) <= kSmallCols && std::strlen(v.l3) <= kSmallCols,
        name + ": small lines fit");
  check(std::strlen(v.time) <= kBigCols, name + ": timer fits");
  // The timer (size 2) and the mode label (size 1) share the bottom row.
  check(std::strlen(v.time) * 12 + std::strlen(v.badge) * 6 <= 128, name + ": timer and label share a row");
  // That row exists only with a timer; text-only screens use the space
  // (a label there drew over READY's lines, caught in the rendered screenshot).
  check(v.time[0] || !v.badge[0], name + ": no mode label without the timer row");
}

void testViewMissingFields() {
  std::printf("every screen fits with missing fields and with maximal fields\n");
  const char* states[] = {"idle", "running", "approval", "turn_done", "task_done", "failed", "ended"};
  const std::string full =
      "\"model\":\"" + std::string(24, 'M') + "\",\"activity\":\"" + std::string(32, 'A') +
      "\",\"effort\":\"medium\",\"permission_mode\":\"dontAsk\",\"sessions\":99,"
      "\"elapsed_seconds\":2592000,\"session_seconds\":2592000";
  for (const char* st : states) {
    for (int variant = 0; variant < 2; ++variant) {
      Monitor m;
      m.apply(upd(st, 1, variant ? full.c_str() : ""), 1000);
      Lines v;
      buildLines(m, 2000, "192.168.100.200", v);
      checkFits(v, std::string(st) + (variant ? " (full)" : " (empty)"));
      if (!variant) {
        const bool hasText = v.big[0] || v.l1[0];
        check(hasText, std::string(st) + " (empty): still says something");
      }
    }
  }
  Monitor never;
  Lines w;
  buildLines(never, 0, nullptr, w);
  checkFits(w, "waiting (no address)");
  check(w.screen == Screen::Waiting && !std::strcmp(w.l1, "Waiting for the"), "waiting screen before any message");

  Monitor stale;
  stale.apply(upd("running", 1, "\"activity\":\"Running tests\""), 1000);
  stale.tick(70000);
  Lines s;
  buildLines(stale, 70000, "", s);
  checkFits(s, "stale");
  check(!std::strcmp(s.big, "STALE") && !std::strcmp(s.l1, "No update for 01:09"), "stale says how long ago");
  check(!std::strcmp(s.l2, "Last: Running tests"), "stale labels old data as last");
  check(s.time[0] == 0, "stale shows no running timer");
}

void testViewContent() {
  std::printf("screen content\n");
  Monitor m;
  m.apply(upd("running", 1, "\"model\":\"Opus\",\"effort\":\"high\",\"activity\":\"Running tests\","
                            "\"permission_mode\":\"default\",\"elapsed_seconds\":763"), 1000);
  Lines v;
  buildLines(m, 1000, "", v);
  check(v.screen == Screen::Running && v.animate, "running animates");
  check(!std::strcmp(v.l1, "Opus | HIGH"), "model | effort (got '" + std::string(v.l1) + "')");
  check(!std::strcmp(v.l2, "Running tests") && !std::strcmp(v.time, "12:43") && !std::strcmp(v.badge, "MANUAL"),
        "activity, timer and MANUAL");

  m.apply(upd("approval", 2, "\"activity\":\"Run: npm\",\"permission_mode\":\"default\""), 2000);
  buildLines(m, 2000, "", v);
  check(v.screen == Screen::Approval && v.warn && !std::strcmp(v.header, "APPROVAL NEEDED"), "approval screen");
  check(!std::strcmp(v.l1, "Run: npm") && !std::strcmp(v.l2, "Answer on computer"), "approval text");

  m.apply(upd("turn_done", 3, "\"model\":\"Opus\""), 3000);
  buildLines(m, 3000, "", v);
  check(v.screen == Screen::Done && v.check && !std::strcmp(v.big, "TURN DONE"), "turn done is not task done");
  m.apply(upd("task_done", 4, "\"model\":\"Opus\""), 4000);
  buildLines(m, 4000, "", v);
  check(!std::strcmp(v.big, "TASK DONE"), "task done");
  m.apply(upd("ended", 5), 5000);
  buildLines(m, 5000, "", v);
  check(v.screen == Screen::Ended && !std::strcmp(v.big, "ENDED"), "session ended");

  Monitor ready;
  ready.apply(upd("idle", 1, "\"model\":\"Opus\""), 1000);
  buildLines(ready, 1000, "", v);
  check(!std::strcmp(v.big, "READY") && !std::strcmp(v.l2, "Waiting for a prompt"), "open session: READY");
  Monitor none;
  none.apply(upd("idle", 1, "", ""), 1000);
  buildLines(none, 1000, "", v);
  check(!std::strcmp(v.big, "IDLE") && !std::strcmp(v.l1, "No active session"), "no session: IDLE");

  Monitor two;
  two.apply(upd("running", 1, "\"sessions\":2"), 1000);
  buildLines(two, 1000, "", v);
  check(!std::strcmp(v.header, "CLAUDE CODE  x2"), "second session shown in the header");

  std::printf("model line keeps the effort when the name is long\n");
  Update u;
  char out[22];
  parse(msg("running", 1, "\"model\":\"Claude Opus 4.7 (1M ctx)\",\"effort\":\"xhigh\""), u);
  modelLine(u, out, kSmallCols);
  check(std::strlen(out) <= kSmallCols && std::strstr(out, "| XHIGH") != nullptr, "effort survives: " + std::string(out));
  parse(msg("running", 1, "\"effort\":\"low\""), u);
  modelLine(u, out, kSmallCols);
  check(!std::strcmp(out, "Model -- | LOW"), "missing model: " + std::string(out));
  parse(msg("running", 1, "\"model\":\"Sonnet\""), u);
  modelLine(u, out, kSmallCols);
  check(!std::strcmp(out, "Sonnet"), "missing effort shows the model only");
}

void testAnimationIsPure() {
  std::printf("animation frames depend on time only (nothing waits, nothing to reset)\n");
  bool inRange = true, sweeps = false;
  uint8_t prev = scannerPos(0, 6);
  for (uint32_t t = 0; t < 5000; t += 10) {
    const uint8_t p = scannerPos(t, 6), d = dotsPhase(t);
    inRange = inRange && p < 6 && d < 4;
    if (p != prev) sweeps = true;
    prev = p;
    inRange = inRange && scannerPos(t, 6) == p && dotsPhase(t) == d;   // same time, same frame
  }
  check(inRange, "positions stay in range and repeat for the same time");
  check(sweeps, "the scanner moves");
  check(scannerPos(0, 6) == 0 && scannerPos(5 * 120, 6) == 5 && scannerPos(6 * 120, 6) == 4, "bounces at the end");
}

}  // namespace

int main() {
  testModeCycle();
  testLongHoldOneTransition();
  testTapsStillWork();
  testParseValid();
  testParseRejects();
  testOrdering();
  testStaleLink();
  testAlerts();
  testTimer();
  testFormatElapsed();
  testViewMissingFields();
  testViewContent();
  testAnimationIsPure();
  std::printf("\n%d checks, %d failures\n", checks, failures);
  return failures == 0 ? 0 : 1;
}
