// ClaudeMonitor.h - the device's memory of the last Claude Code status:
// message ordering, link health, a locally ticking timer, and which buzzer
// alerts a change deserves.
//
// Rules (each one is a test in tests/host/test_claude.cpp):
//   * Messages from one companion run carry an increasing `seq`. An older seq
//     is ignored (a delayed retry must not undo newer state); the same seq
//     only refreshes the link. A new `cid` means the companion restarted, so
//     its counter starts over.
//   * The link is Live after a message, Stale once nothing arrived for
//     staleMs, and Closed when the companion says goodbye (connected=false).
//     Stale and Closed never show old data as if it were live.
//   * The task timer ticks on the device between messages (it is anchored to
//     millis() at receipt), so it does not jump only when a packet arrives.
//     It stops when the task is no longer running or the link is not Live.
//   * Alerts fire on state transitions, never on repeats: entering approval
//     (debounced), running -> done, running -> failed, and Live -> Stale
//     (once per loss). Heartbeats repeat the state and stay silent.
//
// Arduino-free: every call takes `now` in milliseconds, so tests can play
// any timeline and the 49-day millis() wrap is handled by unsigned maths.
#pragma once
#include <stdint.h>
#include <string.h>

#include "ClaudeStatus.h"

namespace claude {

enum class Link : uint8_t { Never, Live, Stale, Closed };

enum Alert : uint8_t {
  AlertNone       = 0,
  AlertPermission = 1 << 0,
  AlertComplete   = 1 << 1,
  AlertFailure    = 1 << 2,
  AlertLinkLost   = 1 << 3,
};

enum class Apply : uint8_t { Accepted, Repeated, Outdated };

struct MonitorConfig {
  uint32_t staleMs = 15000;       // no message for this long: link is Stale
  uint32_t permRepeatMs = 4000;   // minimum gap between two permission alerts
};

inline const char* linkName(Link l) {
  switch (l) {
    case Link::Never:  return "never";
    case Link::Live:   return "live";
    case Link::Stale:  return "stale";
    case Link::Closed: return "closed";
  }
  return "never";
}

inline bool isWorking(State s) { return s == State::Running || s == State::Approval; }
inline bool isSessionOpen(State s) { return s != State::Idle && s != State::Ended; }

class Monitor {
 public:
  explicit Monitor(const MonitorConfig& c = MonitorConfig()) : cfg_(c) {}

  void setStaleMs(uint32_t ms) { cfg_.staleMs = ms; }
  const MonitorConfig& config() const { return cfg_; }

  Apply apply(const Update& u, uint32_t now) {
    if (has_ && !strcmp(u.cid, last_.cid)) {
      if (u.seq < last_.seq) { ++outdated_; return Apply::Outdated; }
      if (u.seq == last_.seq) {
        if (link_ != Link::Closed) { rxAt_ = now; link_ = Link::Live; }
        return Apply::Repeated;
      }
    }

    const bool  hadData   = has_;
    const State prevState = last_.state;
    const bool  prevAppr  = has_ && last_.approval;
    const bool  sameSess  = has_ && !strcmp(u.session, last_.session);
    const bool  nowAppr   = u.approval || u.state == State::Approval;

    if (u.connected) {
      if (nowAppr && !(prevAppr && sameSess) && permAlertDue(now)) {
        alerts_ |= AlertPermission;
        permAlertAt_ = now;
        permAlerted_ = true;
      }
      if (hadData && sameSess && isWorking(prevState)) {
        if (u.state == State::TurnDone || u.state == State::TaskDone) alerts_ |= AlertComplete;
        if (u.state == State::Failed) alerts_ |= AlertFailure;
      }
    }

    last_ = u;
    last_.approval = nowAppr;
    has_ = true;
    rxAt_ = now;
    link_ = u.connected ? Link::Live : Link::Closed;
    ++accepted_;
    return Apply::Accepted;
  }

  // Call every loop. Detects the Live -> Stale transition.
  void tick(uint32_t now) {
    if (link_ == Link::Live && ageMs(now) >= cfg_.staleMs) {
      link_ = Link::Stale;
      alerts_ |= AlertLinkLost;
    }
  }

  // Returns the alerts raised since the last call and clears them.
  uint8_t takeAlerts() {
    const uint8_t a = alerts_;
    alerts_ = AlertNone;
    return a;
  }

  Link link() const { return link_; }
  bool hasData() const { return has_; }
  const Update& last() const { return last_; }
  uint32_t accepted() const { return accepted_; }
  uint32_t outdated() const { return outdated_; }
  uint32_t sinceRxMs(uint32_t now) const { return has_ ? ageMs(now) : 0; }

  // Task time: ticks locally while the task runs and the link is Live.
  uint32_t elapsedS(uint32_t now) const {
    if (!has_) return 0;
    return last_.elapsedS + (link_ == Link::Live && isWorking(last_.state) ? ageMs(now) / 1000 : 0);
  }

  // Session time: ticks while a session is open and the link is Live.
  uint32_t sessionS(uint32_t now) const {
    if (!has_) return 0;
    return last_.sessionS + (link_ == Link::Live && isSessionOpen(last_.state) ? ageMs(now) / 1000 : 0);
  }

 private:
  // Time since the last message. A `now` read just before the message was
  // handled can be a few ms older than the receipt time; that counts as 0,
  // not as 49 days.
  uint32_t ageMs(uint32_t now) const {
    const int32_t d = (int32_t)(now - rxAt_);
    return d > 0 ? (uint32_t)d : 0;
  }

  bool permAlertDue(uint32_t now) const {
    return !permAlerted_ || (uint32_t)(now - permAlertAt_) >= cfg_.permRepeatMs;
  }

  MonitorConfig cfg_;
  Update   last_;
  bool     has_ = false;
  Link     link_ = Link::Never;
  uint32_t rxAt_ = 0;
  uint8_t  alerts_ = AlertNone;
  bool     permAlerted_ = false;
  uint32_t permAlertAt_ = 0;
  uint32_t accepted_ = 0, outdated_ = 0;
};

// "MM:SS" below an hour, "H:MM:SS" from one hour on. Needs 10 bytes.
inline void formatElapsed(uint32_t s, char* out, size_t cap) {
  if (!out || cap == 0) return;
  const uint32_t h = s / 3600, m = (s / 60) % 60, sec = s % 60;
  char tmp[16];
  size_t n = 0;
  if (h > 0) {
    char hb[8];
    size_t hn = 0;
    uint32_t hh = h > 999 ? 999 : h;
    do { hb[hn++] = (char)('0' + hh % 10); hh /= 10; } while (hh && hn < sizeof(hb));
    while (hn) tmp[n++] = hb[--hn];
    tmp[n++] = ':';
  }
  tmp[n++] = (char)('0' + m / 10);
  tmp[n++] = (char)('0' + m % 10);
  tmp[n++] = ':';
  tmp[n++] = (char)('0' + sec / 10);
  tmp[n++] = (char)('0' + sec % 10);
  if (n >= cap) n = cap - 1;
  memcpy(out, tmp, n);
  out[n] = 0;
}

}  // namespace claude
