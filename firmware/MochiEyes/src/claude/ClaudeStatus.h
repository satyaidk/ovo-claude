// ClaudeStatus.h - the message the companion sends to POST /api/claude/status,
// and the code that validates it.
//
// The companion on the computer (companion/ in the repository) owns all
// knowledge about Claude Code: hooks, sessions, the status line. The device
// only receives a small, already-summarised status and draws it. Keeping that
// boundary narrow means the firmware never sees prompts, file contents or
// commands, and the schema below is the whole contract between the two.
//
// Validation is strict on purpose: types, lengths and enumerations are checked
// and a bad message is rejected as a whole, never half-applied. Free text
// (model, activity) is limited to printable ASCII because the OLED font has no
// other glyphs. Arduino-free apart from ArduinoJson, which also builds on a PC,
// so tests/host/test_claude.cpp exercises it directly.
#pragma once
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include <ArduinoJson.h>

namespace claude {

static const uint8_t  kProtocolVersion = 1;
static const size_t   kMaxBody        = 1024;   // bytes; larger requests get HTTP 413
static const size_t   kCidLen         = 16;
static const size_t   kSessionLen     = 16;
static const size_t   kEventLen       = 24;
static const size_t   kModelLen       = 24;
static const size_t   kActivityLen    = 32;
static const uint32_t kMaxSeconds     = 30UL * 24UL * 3600UL;   // 30 days
static const uint8_t  kMaxSessions    = 99;

// What the primary session is doing, as decided by the companion.
enum class State : uint8_t {
  Idle,      // companion connected, no session
  Running,   // a prompt is being worked on
  Approval,  // Claude Code is waiting for a permission decision on the computer
  TurnDone,  // Claude stopped responding; background work may still run
  TaskDone,  // Claude stopped and Claude Code reported no running background work
  Failed,    // the turn ended with an API error (StopFailure)
  Ended,     // the session ended (SessionEnd)
};

// Claude Code's permission_mode values, plus Unknown (not reported yet) and
// Other (a value this firmware does not know). Display labels are below.
enum class PermMode : uint8_t { Unknown, Default, Plan, AcceptEdits, Auto, DontAsk, Bypass, Other };

enum class Effort : uint8_t { Unknown, Low, Medium, High, XHigh, Max };

struct Update {
  uint32_t seq = 0;                    // increases with every message from one companion run
  char     cid[kCidLen + 1] = "";      // companion run id; a new id means the companion restarted
  bool     connected = true;           // false: the companion is shutting down
  State    state = State::Idle;
  char     session[kSessionLen + 1] = "";   // short session id, display only
  uint8_t  sessions = 0;               // live sessions the companion is tracking
  char     event[kEventLen + 1] = "";  // last hook event name, for the details page
  char     model[kModelLen + 1] = "";
  Effort   effort = Effort::Unknown;
  char     activity[kActivityLen + 1] = "";
  PermMode perm = PermMode::Unknown;
  bool     approval = false;
  uint32_t elapsedS = 0;               // task time at the moment of sending
  uint32_t sessionS = 0;               // session time at the moment of sending
};

// ---- Names and labels ----
inline const char* stateName(State s) {
  switch (s) {
    case State::Idle:     return "idle";
    case State::Running:  return "running";
    case State::Approval: return "approval";
    case State::TurnDone: return "turn_done";
    case State::TaskDone: return "task_done";
    case State::Failed:   return "failed";
    case State::Ended:    return "ended";
  }
  return "idle";
}

// The raw Claude Code value ("" while unknown).
inline const char* permName(PermMode p) {
  switch (p) {
    case PermMode::Default:     return "default";
    case PermMode::Plan:        return "plan";
    case PermMode::AcceptEdits: return "acceptEdits";
    case PermMode::Auto:        return "auto";
    case PermMode::DontAsk:     return "dontAsk";
    case PermMode::Bypass:      return "bypassPermissions";
    case PermMode::Other:       return "other";
    case PermMode::Unknown:     break;
  }
  return "";
}

// Compact OLED label. Only "auto" (classifier-reviewed approvals) is shown as
// AUTO; acceptEdits approves edits only, bypassPermissions skips all checks,
// and dontAsk denies anything not pre-approved, so each keeps its own label.
inline const char* permLabel(PermMode p) {
  switch (p) {
    case PermMode::Default:     return "MANUAL";
    case PermMode::Plan:        return "PLAN";
    case PermMode::AcceptEdits: return "EDITS";
    case PermMode::Auto:        return "AUTO";
    case PermMode::DontAsk:     return "DONTASK";
    case PermMode::Bypass:      return "BYPASS";
    case PermMode::Other:       return "OTHER";
    case PermMode::Unknown:     break;
  }
  return "";
}

inline const char* effortName(Effort e) {
  switch (e) {
    case Effort::Low:    return "low";
    case Effort::Medium: return "medium";
    case Effort::High:   return "high";
    case Effort::XHigh:  return "xhigh";
    case Effort::Max:    return "max";
    case Effort::Unknown: break;
  }
  return "";
}

inline const char* effortLabel(Effort e) {
  switch (e) {
    case Effort::Low:    return "LOW";
    case Effort::Medium: return "MEDIUM";
    case Effort::High:   return "HIGH";
    case Effort::XHigh:  return "XHIGH";
    case Effort::Max:    return "MAX";
    case Effort::Unknown: break;
  }
  return "";
}

// ---- Parsing ----
namespace detail {

inline bool parseState(const char* s, State& out) {
  static const char* const names[] = {"idle", "running", "approval", "turn_done", "task_done", "failed", "ended"};
  for (uint8_t i = 0; i < sizeof(names) / sizeof(names[0]); ++i)
    if (!strcmp(s, names[i])) { out = (State)i; return true; }
  return false;
}

inline bool parsePerm(const char* s, PermMode& out) {
  if (!*s) { out = PermMode::Unknown; return true; }
  for (uint8_t i = 1; i <= (uint8_t)PermMode::Other; ++i)
    if (!strcmp(s, permName((PermMode)i))) { out = (PermMode)i; return true; }
  return false;
}

inline bool parseEffort(const char* s, Effort& out) {
  if (!*s) { out = Effort::Unknown; return true; }
  for (uint8_t i = 1; i <= (uint8_t)Effort::Max; ++i)
    if (!strcmp(s, effortName((Effort)i))) { out = (Effort)i; return true; }
  return false;
}

// Copies an optional string field into dst (capacity cap, including the
// terminator). Missing or null leaves dst empty. Non-printable bytes become
// '?' so a stray character can never upset the display.
inline bool readText(JsonObjectConst o, const char* key, char* dst, size_t cap, bool required,
                     const char*& err) {
  dst[0] = 0;
  JsonVariantConst v = o[key];
  if (v.isNull()) {
    if (required) { err = "missing"; return false; }
    return true;
  }
  if (!v.is<const char*>()) { err = "expected a string"; return false; }
  const char* s = v.as<const char*>();
  const size_t n = strlen(s);
  if (n >= cap) { err = "too long"; return false; }
  for (size_t i = 0; i < n; ++i) {
    const unsigned char c = (unsigned char)s[i];
    dst[i] = (c >= 0x20 && c <= 0x7E) ? (char)c : '?';
  }
  dst[n] = 0;
  return true;
}

inline bool readUint(JsonObjectConst o, const char* key, uint32_t maxValue, bool required, uint32_t& out,
                     const char*& err) {
  JsonVariantConst v = o[key];
  if (v.isNull()) {
    if (required) { err = "missing"; return false; }
    return true;
  }
  if (!v.is<uint32_t>()) { err = "expected an unsigned integer"; return false; }
  const uint32_t x = v.as<uint32_t>();
  if (x > maxValue) { err = "out of range"; return false; }
  out = x;
  return true;
}

inline bool readBool(JsonObjectConst o, const char* key, bool& out, const char*& err) {
  JsonVariantConst v = o[key];
  if (v.isNull()) return true;
  if (!v.is<bool>()) { err = "expected true or false"; return false; }
  out = v.as<bool>();
  return true;
}

// Short identifiers (cid, event) are limited to letters, digits, '-' and '_'.
inline bool isIdent(const char* s) {
  for (; *s; ++s) {
    const char c = *s;
    const bool ok = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') ||
                    c == '-' || c == '_';
    if (!ok) return false;
  }
  return true;
}

}  // namespace detail

// Parses and validates one request body. On failure returns false, leaves
// `out` untouched, and sets `field` (the offending key, or "body") and `err`
// (a short reason) for the HTTP reply. Unknown keys are ignored so a newer
// companion can add fields without breaking older firmware.
inline bool parseUpdate(const char* body, size_t len, Update& out, const char*& field, const char*& err) {
  field = "body";
  if (!body || len == 0) { err = "empty"; return false; }
  if (len > kMaxBody) { err = "too large"; return false; }

  JsonDocument doc;
  const DeserializationError de = deserializeJson(doc, body, len, DeserializationOption::NestingLimit(2));
  if (de) { err = "invalid JSON"; return false; }
  if (!doc.is<JsonObjectConst>()) { err = "expected a JSON object"; return false; }
  JsonObjectConst o = doc.as<JsonObjectConst>();

  Update u;
  uint32_t num = 0;
  char buf[24];

  field = "v";
  if (!detail::readUint(o, "v", 255, true, num, err)) return false;
  if (num != kProtocolVersion) { err = "unsupported version"; return false; }

  field = "seq";
  if (!detail::readUint(o, "seq", 0xFFFFFFFFUL, true, u.seq, err)) return false;

  field = "cid";
  if (!detail::readText(o, "cid", u.cid, sizeof(u.cid), true, err)) return false;
  if (!u.cid[0] || !detail::isIdent(u.cid)) { err = "expected letters, digits, - or _"; return false; }

  field = "state";
  if (!detail::readText(o, "state", buf, sizeof(buf), true, err)) return false;
  if (!detail::parseState(buf, u.state)) { err = "unknown value"; return false; }

  field = "connected";
  if (!detail::readBool(o, "connected", u.connected, err)) return false;
  field = "approval_required";
  if (!detail::readBool(o, "approval_required", u.approval, err)) return false;

  field = "session_id";
  if (!detail::readText(o, "session_id", u.session, sizeof(u.session), false, err)) return false;
  field = "event";
  if (!detail::readText(o, "event", u.event, sizeof(u.event), false, err)) return false;
  if (!detail::isIdent(u.event)) { err = "expected letters, digits, - or _"; return false; }
  field = "model";
  if (!detail::readText(o, "model", u.model, sizeof(u.model), false, err)) return false;
  field = "activity";
  if (!detail::readText(o, "activity", u.activity, sizeof(u.activity), false, err)) return false;

  field = "effort";
  if (!detail::readText(o, "effort", buf, sizeof(buf), false, err)) return false;
  if (!detail::parseEffort(buf, u.effort)) { err = "unknown value"; return false; }

  field = "permission_mode";
  if (!detail::readText(o, "permission_mode", buf, sizeof(buf), false, err)) return false;
  if (!detail::parsePerm(buf, u.perm)) { err = "unknown value"; return false; }

  field = "sessions";
  num = 0;
  if (!detail::readUint(o, "sessions", kMaxSessions, false, num, err)) return false;
  u.sessions = (uint8_t)num;
  field = "elapsed_seconds";
  if (!detail::readUint(o, "elapsed_seconds", kMaxSeconds, false, u.elapsedS, err)) return false;
  field = "session_seconds";
  if (!detail::readUint(o, "session_seconds", kMaxSeconds, false, u.sessionS, err)) return false;

  if (u.state == State::Approval) u.approval = true;
  out = u;
  field = "";
  err = "";
  return true;
}

}  // namespace claude
