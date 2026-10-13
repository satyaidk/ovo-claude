// AppModes.h - the three faces Mochi can show and the order the 5 s hold
// cycles through them. Arduino-free so the cycle is unit-tested on a PC
// (tests/host/test_claude.cpp).
//
// Why a separate header: with two modes "the other one" was a ternary in
// three places. With three, the order has to be defined once, or the touch
// pad, the web page and the Serial console would drift apart.
#pragma once
#include <stdint.h>
#include <string.h>

enum AppMode : uint8_t { MODE_EYES = 0, MODE_CLOCK = 1, MODE_CLAUDE = 2 };

static const uint8_t kModeCount = 3;

// EYES -> CLOCK -> CLAUDE -> EYES. An out-of-range value restarts at EYES.
inline AppMode nextMode(AppMode m) {
  return m + 1 < kModeCount ? (AppMode)(m + 1) : MODE_EYES;
}

inline const char* modeName(AppMode m) {
  switch (m) {
    case MODE_EYES:   return "eyes";
    case MODE_CLOCK:  return "clock";
    case MODE_CLAUDE: return "claude";
  }
  return "eyes";
}

// Accepts "eyes", "clock" or "claude". Returns false for anything else.
inline bool parseMode(const char* s, AppMode& out) {
  if (!s) return false;
  for (uint8_t i = 0; i < kModeCount; ++i) {
    if (!strcmp(s, modeName((AppMode)i))) { out = (AppMode)i; return true; }
  }
  return false;
}

// Settings are stored as a byte in NVS; anything unknown falls back to EYES.
inline AppMode modeFromByte(uint8_t b) { return b < kModeCount ? (AppMode)b : MODE_EYES; }
