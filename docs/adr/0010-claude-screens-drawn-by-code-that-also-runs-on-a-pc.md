# ADR 0010: Claude screens are drawn by code that also runs on a PC

- Status: Accepted
- Date: 2026-10-13

## Context

The Claude face has ten screens of text on a 128x64 panel with a 6x8 font.
The OLED was not available while building it. Text that is one character too
long wraps onto the next line and garbles the screen, and two elements can
overlap without any compiler noticing. The project's documentation standard
asks for real images of the product, and the eye engine already produces
its GIF from the firmware's own code (`tools/preview/frames.cpp`).

## Decision

* The words of each screen come from `src/claude/ClaudeView.h` (testable,
  no graphics).
* The drawing lives in `src/claude/ClaudeDraw.h` and takes any
  `Adafruit_GFX&`: the SSD1306 on the device, a `GFXcanvas1` on a PC.
* `tools/preview/arduino_shim/` supplies the handful of Arduino definitions
  the real Adafruit GFX 1.12.6 source needs, so the PC build uses the same
  library and font as the firmware.
* `tools/preview/claude_screens.cpp` renders a scripted session and one frame
  per screen into the OLED frame format, and fails if any line is wider than
  the panel by the library's own `getTextBounds`. CI builds and runs it.
  `tools/preview/render_png.py` turns the frames into the docs images.

## Consequences

* The screenshots in the docs are the firmware's pixels, not mock-ups.
* Rendering found a real defect before any hardware existed: the mode label
  was drawn over the Ready screen's text. A host test now covers that rule.
* The PC build depends on the Adafruit GFX source (fetched in CI at the
  pinned version) and a small shim that must track what the library uses.

## Alternatives considered

* **Mock-ups drawn by hand.** Fast, but they would show what was intended,
  not what the code draws.
* **A Python re-implementation of the layout.** Two copies of the layout that
  can drift apart.
* **Wait for hardware photos.** Still worth adding, but they cannot be
  checked automatically in CI.
