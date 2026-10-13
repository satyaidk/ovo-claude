// Arduino.h - the few Arduino definitions Adafruit GFX needs, so the real
// library builds on a PC for tools/preview/claude_screens.cpp. Not a general
// Arduino emulation: only what Adafruit_GFX.cpp and Print.h use.
#pragma once
#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define PROGMEM
#ifndef pgm_read_byte
#define pgm_read_byte(addr) (*(const unsigned char *)(addr))
#endif

#ifndef DEG_TO_RAD
#define DEG_TO_RAD 0.017453292519943295769236907684886
#endif
#define radians(deg) ((deg) * DEG_TO_RAD)

#if defined(_MSC_VER) && !defined(__clang__)
#define __builtin_bswap16 _byteswap_ushort   // GCC builtin used by GFXcanvas16
#endif

inline void yield() {}

class __FlashStringHelper;

// Only what Adafruit_GFX::getTextBounds(const String&) touches.
class String {
 public:
  String(const char* s = "") : s_(s) {}
  const char* c_str() const { return s_; }
  unsigned int length() const { return (unsigned int)strlen(s_); }
 private:
  const char* s_;
};

#include "Print.h"
