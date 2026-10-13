# ADR 0008: Monitor Claude Code through a local companion and supported hooks

- Status: Accepted
- Date: 2026-10-13

## Context

The device should show what Claude Code is doing on the computer: model,
effort, the current kind of action, elapsed time, the approval mode, and
whether a permission prompt is waiting. Constraints:

* Use supported Claude Code interfaces, not terminal scraping, which breaks
  with every UI change and would read prompts and output.
* Never influence authorization: the device is a notification display.
* Send nothing private to a device on the LAN that has no authentication.
* Several sessions can run at once.
* Claude Code's event set changes between versions.

Claude Code offers hooks (lifecycle and tool events, JSON on stdin for
command hooks, or as the POST body for HTTP hooks) and a status-line command
(JSON on stdin with the model's display name and the live effort level).

## Decision

A Python companion on the computer sits between Claude Code and the device.

* **Hooks.** Fourteen events are installed. Thirteen are HTTP hooks to a
  daemon on 127.0.0.1; HTTP hooks need no process start per event and arrive
  in order. `SessionStart` accepts only command hooks, so it runs a tiny async
  forwarder. Hook replies are always empty `204`, which Claude Code treats as
  "no decision", so the companion cannot approve or deny anything.
* **Status line.** It supplies the model's display name and the effort, which
  hooks only partly report. The user's existing status line is recorded and
  chained, so installing never removes it.
* **The companion owns the semantics.** Event-to-state mapping, sessions,
  approval matching, ordering and timers live in `tracker.py`, which is
  unit-tested with simulated event streams. The device receives a small,
  validated summary (ADR 0009).
* **Conservative versions.** The installer reads `claude --version`. Versions
  known to have HTTP hooks and the newer events get them; unknown or older
  versions get async command hooks and the eight core events.
* **Safe settings changes.** Merge rather than replace, back up first,
  idempotent install, and an uninstall that removes only what it added. The
  install record lives outside the project.

## Consequences

* Works with any terminal, IDE panel or the desktop app, because it uses
  Claude Code's own events.
* The companion must be running. When it is not, HTTP hooks fail as
  non-blocking errors and the device shows STALE or Waiting.
* Some states are inferred from the available evidence and documented as such:
  "task done" means `Stop` reported no running background work; an Esc
  interrupt ends a turn only when `idle_prompt` arrives.
* A real run against Claude Code 2.1.296 was part of the verification. It
  caught that `SessionStart` rejects HTTP hooks, which reading the docs had
  missed.

## Alternatives considered

* **Scraping the terminal or the transcript file.** Unsupported, fragile,
  and reads private content.
* **Hooks posting straight to the device.** No place for session tracking,
  heartbeats or sanitising; the device's address would sit in every project's
  settings; and a sleeping device would add a timeout to every tool call.
* **Command hooks for every event.** One Python start per tool call, and the
  async ones can overtake each other. Kept as the fallback for older versions.
* **The device polling the computer.** Needs a server reachable from the LAN
  on the computer, and slower updates.
