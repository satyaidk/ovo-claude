// ClaudeView.h - decides what the Claude Code face says: which screen, and
// the text of each line, already cut to fit 128x64 pixels.
//
// The sketch (drawClaudeMode() in MochiEyes.ino) only places these strings and
// draws the icons. Keeping the words here means every screen, including the
// ones with missing model, effort or activity, is checked on a PC: the tests
// assert that no line is wider than the display. Font maths: the built-in
// 5x7 font is 6 px per character, so 21 characters at size 1 and 10 at size 2.
#pragma once
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "ClaudeMonitor.h"

namespace claude {

static const uint8_t kSmallCols  = 21;   // 128 px / 6 px
static const uint8_t kBigCols    = 10;   // 128 px / 12 px
static const uint8_t kHeaderCols = 19;   // leaves room for the Wi-Fi dot at x = 122

enum class Screen : uint8_t { Waiting, Idle, Running, Approval, Done, Failed, Ended, Stale, Closed };

inline const char* screenName(Screen s) {
  switch (s) {
    case Screen::Waiting:  return "waiting";
    case Screen::Idle:     return "idle";
    case Screen::Running:  return "running";
    case Screen::Approval: return "approval";
    case Screen::Done:     return "done";
    case Screen::Failed:   return "failed";
    case Screen::Ended:    return "ended";
    case Screen::Stale:    return "stale";
    case Screen::Closed:   return "closed";
  }
  return "waiting";
}

struct Lines {
  Screen screen = Screen::Waiting;
  char header[kHeaderCols + 1] = "";   // inverted title bar
  char big[kBigCols + 1] = "";         // size-2 word, may be empty
  char l1[kSmallCols + 1] = "";
  char l2[kSmallCols + 1] = "";
  char l3[kSmallCols + 1] = "";
  char time[kBigCols + 1] = "";        // size-2 timer, may be empty
  char badge[8] = "";                  // approval mode label, may be empty
  bool animate = false;                // loading dots and scanner
  bool warn = false;                   // blinking warning header and triangle
  bool check = false;                  // completion tick
};

// Copies at most `cols` characters. A cut string ends in '.' so the reader
// can tell it was shortened.
inline void fit(const char* src, char* dst, uint8_t cols) {
  if (!src) src = "";
  const size_t n = strlen(src);
  if (n <= cols) {
    memcpy(dst, src, n + 1);
    return;
  }
  memcpy(dst, src, cols);
  if (cols > 0) dst[cols - 1] = '.';
  dst[cols] = 0;
}

inline Screen pickScreen(const Monitor& m) {
  switch (m.link()) {
    case Link::Never:  return Screen::Waiting;
    case Link::Stale:  return Screen::Stale;
    case Link::Closed: return Screen::Closed;
    case Link::Live:   break;
  }
  const Update& u = m.last();
  if (u.approval) return Screen::Approval;
  switch (u.state) {
    case State::Idle:     return Screen::Idle;
    case State::Running:  return Screen::Running;
    case State::Approval: return Screen::Approval;
    case State::TurnDone: return Screen::Done;
    case State::TaskDone: return Screen::Done;
    case State::Failed:   return Screen::Failed;
    case State::Ended:    return Screen::Ended;
  }
  return Screen::Idle;
}

// "Opus | HIGH", or just the part that is known. The model name is shortened
// first so the effort label always survives.
inline void modelLine(const Update& u, char* out, uint8_t cols) {
  const char* eff = effortLabel(u.effort);
  char model[kModelLen + 1];
  fit(u.model[0] ? u.model : "Model --", model, cols);
  if (!eff[0]) { fit(model, out, cols); return; }
  const uint8_t effLen = (uint8_t)strlen(eff);
  const uint8_t room = (uint8_t)(cols > effLen + 3 ? cols - effLen - 3 : 0);
  char cut[kModelLen + 1];
  fit(model, cut, room);
  char tmp[64];
  snprintf(tmp, sizeof(tmp), "%s | %s", cut, eff);
  fit(tmp, out, cols);
}

// Animation phases are pure functions of time: nothing to reset, nothing that
// waits. dots: 0..3, scanner: index of the lit cell in a back-and-forth sweep.
inline uint8_t dotsPhase(uint32_t now) { return (uint8_t)((now / 350) % 4); }
inline uint8_t scannerPos(uint32_t now, uint8_t cells) {
  if (cells < 2) return 0;
  const uint32_t span = 2u * (cells - 1);
  const uint32_t p = (now / 120) % span;
  return (uint8_t)(p < cells ? p : span - p);
}

inline void buildLines(const Monitor& m, uint32_t now, const char* deviceAddr, Lines& out) {
  out = Lines();
  out.screen = pickScreen(m);
  const Update& u = m.last();

  char head[40];
  if (m.hasData() && u.sessions > 1) snprintf(head, sizeof(head), "CLAUDE CODE  x%u", (unsigned)u.sessions);
  else snprintf(head, sizeof(head), "CLAUDE CODE");
  fit(head, out.header, kHeaderCols);
  fit(permLabel(u.perm), out.badge, sizeof(out.badge) - 1);

  char t[16];
  switch (out.screen) {
    case Screen::Waiting:
      out.badge[0] = 0;
      fit("Waiting for the", out.l1, kSmallCols);
      fit("companion on your PC", out.l2, kSmallCols);
      fit(deviceAddr ? deviceAddr : "", out.l3, kSmallCols);
      break;

    case Screen::Idle:
      if (u.session[0]) {                  // a session is open, waiting for your next prompt
        fit("READY", out.big, kBigCols);
        modelLine(u, out.l1, kSmallCols);
        fit("Waiting for a prompt", out.l2, kSmallCols);
      } else {
        fit("IDLE", out.big, kBigCols);
        fit("No active session", out.l1, kSmallCols);
        fit("Companion connected", out.l2, kSmallCols);
      }
      break;

    case Screen::Running:
      out.animate = true;
      modelLine(u, out.l1, kSmallCols);
      fit(u.activity[0] ? u.activity : "Working", out.l2, kSmallCols);
      formatElapsed(m.elapsedS(now), out.time, sizeof(out.time));
      break;

    case Screen::Approval:
      out.warn = true;
      fit("APPROVAL NEEDED", out.header, kHeaderCols);
      fit(u.activity[0] ? u.activity : "Permission request", out.l1, kSmallCols - 3);   // icon on the left
      fit("Answer on computer", out.l2, kSmallCols);
      formatElapsed(m.elapsedS(now), out.time, sizeof(out.time));
      break;

    case Screen::Done:
      out.check = true;
      fit(u.state == State::TaskDone ? "TASK DONE" : "TURN DONE", out.big, kBigCols);
      modelLine(u, out.l1, kSmallCols - 2);   // tick on the left
      formatElapsed(m.elapsedS(now), out.time, sizeof(out.time));
      break;

    case Screen::Failed:
      out.warn = true;
      fit("FAILED", out.big, kBigCols);
      fit(u.activity[0] ? u.activity : "Task failed", out.l1, kSmallCols);
      formatElapsed(m.elapsedS(now), out.time, sizeof(out.time));
      break;

    case Screen::Ended:
      fit("ENDED", out.big, kBigCols);
      fit("Session ended", out.l1, kSmallCols);
      formatElapsed(m.sessionS(now), out.time, sizeof(out.time));
      break;

    case Screen::Stale: {
      fit("STALE", out.big, kBigCols);
      formatElapsed(m.sinceRxMs(now) / 1000, t, sizeof(t));
      char tmp[48];
      snprintf(tmp, sizeof(tmp), "No update for %s", t);
      fit(tmp, out.l1, kSmallCols);
      snprintf(tmp, sizeof(tmp), "Last: %s", u.activity[0] ? u.activity : stateName(u.state));
      fit(tmp, out.l2, kSmallCols);
      out.badge[0] = 0;
      break;
    }

    case Screen::Closed:
      fit("OFFLINE", out.big, kBigCols);
      fit("DISCONNECTED", out.l1, kSmallCols);
      fit("Companion stopped", out.l2, kSmallCols);
      out.badge[0] = 0;
      break;
  }

  // The mode label lives in the timer row, so a screen without a timer has
  // none (its text uses that space). Past 10 hours a long label would collide
  // with the timer, so it yields; the details page still shows the mode.
  if (!out.time[0] || strlen(out.time) * 12 + strlen(out.badge) * 6 > 128) out.badge[0] = 0;
}

}  // namespace claude
