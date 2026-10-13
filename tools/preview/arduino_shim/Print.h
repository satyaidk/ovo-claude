// Print.h - minimal stand-in for Arduino's Print class (see Arduino.h here).
#pragma once
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

class Print {
 public:
  virtual ~Print() {}
  virtual size_t write(uint8_t c) = 0;
  virtual size_t write(const uint8_t* buf, size_t n) {
    size_t done = 0;
    while (n--) done += write(*buf++);
    return done;
  }
  size_t write(const char* s) { return s ? write((const uint8_t*)s, strlen(s)) : 0; }
  size_t print(const char* s) { return write(s); }
  size_t print(char c) { return write((uint8_t)c); }
  size_t print(long v) { char b[24]; snprintf(b, sizeof b, "%ld", v); return write(b); }
  size_t print(int v) { return print((long)v); }
  size_t print(unsigned long v) { char b[24]; snprintf(b, sizeof b, "%lu", v); return write(b); }
  size_t print(unsigned int v) { return print((unsigned long)v); }
};
