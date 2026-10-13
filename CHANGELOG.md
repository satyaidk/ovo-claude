# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Claude Code activity monitor**, a third face. It shows the model and
  effort, a short activity label with an animated "Working" indicator, the
  task timer and the approval mode (MANUAL, AUTO, PLAN, EDITS, DONTASK,
  BYPASS), plus distinct screens for approval needed, turn done, task done,
  failed, session ended, ready, stale and disconnected. See
  `docs/CLAUDE_MONITOR.md`.
- `companion/`: the OVO companion, a Python service (standard library only)
  that receives Claude Code hooks and status-line data locally, tracks
  sessions, and sends the device a sanitised status on every change and
  every 5 seconds. Includes `install` and `uninstall` commands that merge into
  Claude Code settings with a backup, run idempotently, and keep and chain an
  existing status line. 154 tests.
- `POST /api/claude/status`: validated JSON (1 KB, read into a fixed buffer),
  ordered by sequence number, with stale-link detection on the device.
- Buzzer alerts for permission requests, completion, failure and (optionally)
  connection loss, each switchable. They fire on state transitions and wait
  for the current chirp to finish.
- Web controller: a third face button, a Claude Code panel with live status,
  alert switches and the stale timeout, and Claude as a start-up face.
- `src/app/AppModes.h` and `src/claude/` (Arduino-free apart from ArduinoJson
  and Adafruit GFX), with `tests/host/test_claude.cpp` (350 checks).
- `tools/preview/claude_screens.cpp`: draws the Claude screens on a PC with
  the real Adafruit GFX (through `tools/preview/arduino_shim/`), checks every
  line fits, and feeds `render_png.py` for the docs images. CI runs it.
- ADRs 0008 to 0010.

### Changed

- Holding the pad for 5 seconds now cycles eyes -> clock -> Claude -> eyes.
  `mode` without an argument and `/api/mode` with `m=toggle` move to the next
  face; `mode` with an unknown face name now returns an error instead of
  toggling.
- The hold progress bar on the Claude face starts at 0.9 s, as on the clock.
- `tools/embed_web.py` normalises line endings, so a Windows (CRLF) checkout
  embeds and checks the same page as CI.

## [1.0.0] - 2026-10-11

First release of the merged firmware. It combines the info terminal
(`clock.ino`) and the eyes-only `MochiEyes.ino` into one program.

### Added

- Two faces on one OLED: the Mochi eyes and the clock pages. Holding the touch
  pad for 5 seconds switches between them, with a progress bar on screen and a
  shoji-door transition.
- Touch reactions for the eyes: 1 tap glances at the pad, 2 taps smile,
  3 taps get grumpy, 4 or more taps get dizzy (eyes orbit in opposite
  directions), and holding the pad is petting.
- Auto-sleep: drowsy after 2 minutes without interaction, asleep after 5;
  any tap wakes the eyes.
- Clock gestures: 2 taps go back a page, 3 taps return to the clock face.
- Redesigned web controller in an indigo-dyed Japanese style, with light and
  dark themes, an ensō gaze pad, and a live mirror of the OLED.
- JSON web API (`/api/state`, `/api/eye`, `/api/mode`, and more), documented in
  `docs/API.md`.
- Settings saved on the device: sound, auto-sleep, and which face to show after
  a restart.
- `src/touch/TouchGestures.h`, an Arduino-free tap and hold recognizer with
  48 host-test checks.
- Tools: `tools/embed_web.py` (gzips the page into the firmware),
  `tools/preview/` (preview the page with eye frames rendered on a PC).
- CI that compiles the firmware, runs host tests, and checks the embedded page.

### Changed

- Everything that is flashed now lives in one folder, `firmware/MochiEyes/`;
  docs, tests and tools sit outside it. The two original sketches were removed
  after merging.
- Weather and price downloads run in a FreeRTOS task, so the eyes keep moving
  during HTTPS requests.
- The eyes now render into the Adafruit SSD1306 buffer instead of U8g2, so one
  display library serves both faces.
- The local hostname is now `mochi.local` (was `terminal.local`).
- Partition scheme is now **Minimal SPIFFS (1.9MB APP with OTA)**. The merged
  firmware does not fit the default 1.2 MB app partition.
- The I2C pin search no longer probes the touch and buzzer pins.

### Security

- Wi-Fi credentials moved from the sketch into the git-ignored `secrets.h`
  (template: `secrets.example.h`).

[Unreleased]: https://github.com/satyaidk/MochiEyes/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/satyaidk/MochiEyes/releases/tag/v1.0.0
