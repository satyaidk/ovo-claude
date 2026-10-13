// ClaudeDraw.h - draws the Claude Code screens with Adafruit GFX.
//
// The words come from ClaudeView.h; this file places them and draws the
// icons. It takes any Adafruit_GFX, so the same code draws into the SSD1306
// on the device and into a GFXcanvas1 on a PC (tools/preview/claude_screens.cpp),
// which is how the screenshots in the docs are made and how CI checks that no
// line wraps on the real 128x64 font metrics.
//
// Layout (y in pixels):  0-10 title bar | 13-41 content | 43 rule | 47-62 timer + mode
#pragma once
#include <stdint.h>
#include <string.h>

#include <Adafruit_GFX.h>

#include "ClaudeView.h"

namespace claude {
namespace draw {

static const uint16_t kOn = 1, kOff = 0;   // SSD1306_WHITE / SSD1306_BLACK

// Same look as drawHeader() in MochiEyes.ino: inverted bar, Wi-Fi dot on the right.
inline void header(Adafruit_GFX& g, const char* text, bool wifiUp) {
  g.fillRect(0, 0, g.width(), 11, kOn);
  g.setTextColor(kOff);
  g.setTextSize(1);
  g.setCursor(2, 2);
  g.print(text);
  if (wifiUp) g.fillCircle(122, 5, 2, kOff);
  else        g.drawCircle(122, 5, 2, kOff);
  g.setTextColor(kOn);
}

inline void outlinedHeader(Adafruit_GFX& g, const char* text) {
  g.drawRect(0, 0, g.width(), 11, kOn);
  g.setTextColor(kOn);
  g.setTextSize(1);
  g.setCursor(2, 2);
  g.print(text);
}

inline void centered(Adafruit_GFX& g, const char* s, int16_t y, uint8_t size) {
  int16_t x1, y1;
  uint16_t w, h;
  g.setTextSize(size);
  g.getTextBounds(s, 0, y, &x1, &y1, &w, &h);
  g.setCursor((int16_t)((g.width() - (int16_t)w) / 2), y);
  g.print(s);
}

inline void text(Adafruit_GFX& g, const char* s, int16_t x, int16_t y) {
  g.setTextSize(1);
  g.setCursor(x, y);
  g.print(s);
}

inline void warnTriangle(Adafruit_GFX& g, int16_t x, int16_t y) {   // 15 x 13 px
  g.fillTriangle(x + 7, y, x, y + 12, x + 14, y + 12, kOn);
  g.fillRect(x + 6, y + 4, 3, 5, kOff);
  g.fillRect(x + 6, y + 10, 3, 2, kOff);
}

inline void tick(Adafruit_GFX& g, int16_t x, int16_t y) {   // 11 x 8 px
  for (int16_t k = 0; k < 2; k++) {
    g.drawLine(x + k, y + 4, x + 3 + k, y + 7, kOn);
    g.drawLine(x + 3 + k, y + 7, x + 9 + k, y + 1, kOn);
  }
}

// "Working" with dots, then a cell sweeping back and forth: [.*....]
inline void working(Adafruit_GFX& g, uint32_t now, int16_t y) {
  static const char* const dots[] = {"Working", "Working.", "Working..", "Working..."};
  text(g, dots[dotsPhase(now)], 0, y);
  const uint8_t cells = 6, lit = scannerPos(now, cells);
  for (uint8_t i = 0; i < cells; i++) {
    const int16_t cx = 70 + i * 10;
    if (i == lit) g.fillRect(cx, y, 8, 7, kOn);
    else          g.drawRect(cx + 3, y + 3, 2, 2, kOn);
  }
}

// Size-2 timer on the left, approval mode on the right, under a rule. Only
// screens with a timer have this row; the others use y 43-63 for text.
inline void timeRow(Adafruit_GFX& g, const Lines& v) {
  if (!v.time[0]) return;
  g.drawFastHLine(0, 43, g.width(), kOn);
  g.setTextSize(2);
  g.setCursor(0, 47);
  g.print(v.time);
  if (v.badge[0]) {
    g.setTextSize(1);
    g.setCursor((int16_t)(g.width() - (int16_t)strlen(v.badge) * 6), 51);
    g.print(v.badge);
  }
}

// Draws one Claude screen. Clears the canvas first.
inline void screen(Adafruit_GFX& g, const Lines& v, uint32_t now, bool wifiUp) {
  g.fillScreen(kOff);
  g.setTextColor(kOn);
  g.setTextWrap(false);   // a line that does not fit is cut, never wrapped over the next one
  if (v.warn && (now / 500) % 2) outlinedHeader(g, v.header);   // blinks twice a second
  else                           header(g, v.header, wifiUp);

  switch (v.screen) {
    case Screen::Waiting:
      centered(g, v.l1, 18, 1);
      centered(g, v.l2, 28, 1);
      g.drawFastHLine(0, 41, g.width(), kOn);
      centered(g, v.l3, 48, 1);
      break;
    case Screen::Idle:
      centered(g, v.big, 16, 2);
      centered(g, v.l1, 38, 1);
      centered(g, v.l2, 50, 1);
      break;
    case Screen::Running:
      text(g, v.l1, 0, 14);
      text(g, v.l2, 0, 24);
      working(g, now, 34);
      break;
    case Screen::Approval:
      warnTriangle(g, 0, 15);
      text(g, v.l1, 20, 18);
      text(g, v.l2, 0, 32);
      break;
    case Screen::Done:
      centered(g, v.big, 14, 2);
      tick(g, 0, 33);
      text(g, v.l1, 13, 33);
      break;
    case Screen::Failed:
    case Screen::Ended:
      centered(g, v.big, 14, 2);
      centered(g, v.l1, 33, 1);
      break;
    case Screen::Stale:
    case Screen::Closed:
      centered(g, v.big, 14, 2);
      centered(g, v.l1, 36, 1);
      centered(g, v.l2, 48, 1);
      break;
  }
  timeRow(g, v);
  g.setTextSize(1);
  g.setTextWrap(true);    // the clock pages share the display and expect the library default
}

}  // namespace draw
}  // namespace claude
