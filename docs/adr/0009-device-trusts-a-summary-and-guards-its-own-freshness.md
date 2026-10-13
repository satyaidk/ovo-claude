# ADR 0009: The device draws a validated summary and guards its own freshness

- Status: Accepted
- Date: 2026-10-13

## Context

The firmware receives Claude Code status over Wi-Fi (ADR 0008). Messages can
be lost, delayed or reordered; the companion can stop or crash; the computer
can sleep. A screen that says RUNNING with a ticking timer after the computer
went to sleep would be wrong, and a 49-day `millis()` wrap must not break
anything. Alerts must sound once per event, not once per heartbeat.
Everything runs in the loop task next to the eyes, so nothing may block.

## Decision

* **Narrow contract.** `POST /api/claude/status` takes one JSON object with a
  version, a sequence number, a companion run id, a state and a few short
  fields (`src/claude/ClaudeStatus.h`). Types, lengths and enumerations are
  checked; a bad message is rejected whole. The body goes into a fixed 1 KB
  buffer through WebServer's raw-body callback, so an oversized request is
  refused without allocating memory.
* **Ordering.** Within one companion run (`cid`), an older `seq` is ignored
  and the same `seq` only refreshes the link. A new `cid` starts over.
* **Freshness on the device.** `ClaudeMonitor` marks the link stale after
  `cc_stale` seconds (default 15, three missed 5-second heartbeats) and
  closed when the companion says goodbye. Stale and closed screens never show
  old data as live.
* **Local timer.** Elapsed time is anchored to `millis()` at receipt and keeps
  counting between messages while the task runs. It stops when the task ends
  or the link is not live.
* **Alerts from transitions.** Permission on entering approval (debounced 4
  s), completion and failure only from a running state of the same session,
  link-lost once per loss. Sounds queue behind the current melody.
* **Testable.** `ClaudeStatus.h`, `ClaudeMonitor.h` and `ClaudeView.h` are
  Arduino-free (apart from ArduinoJson) and take time as a parameter, like
  `TouchGestures.h` (ADR 0005). `tests/host/test_claude.cpp` covers ordering,
  staleness, the wrap, alerts and every screen with missing and maximal
  fields.

## Consequences

* All Claude state is touched only by the loop task (the web handler and the
  renderer), so no lock is needed.
* The device keeps working, and degrades visibly, with any companion that
  speaks version 1, including a `curl` command.
* Stricter than the other endpoints: a single unknown `state` or `effort`
  value rejects the message. Unknown *keys* are ignored for forward
  compatibility.

## Alternatives considered

* **Let the device interpret raw hook events.** Larger payloads (tool input
  can contain whole files), private data on the LAN, and logic that is hard to
  test on the device.
* **Trust the companion's timer only.** The time would jump every few seconds
  and freeze silently when the companion dies.
* **Allocate the body with the default handler.** WebServer would allocate
  whatever Content-Length a client claims.
