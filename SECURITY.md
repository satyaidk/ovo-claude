# Security policy

MochiEyes is a hobby device for a home network. It is not hardened for
untrusted networks, and this page explains what that means in practice.

## Supported versions

Only the latest release on `main` receives fixes.

| Version | Supported |
| ------- | --------- |
| 1.0.x   | Yes       |
| < 1.0   | No        |

## Reporting a vulnerability

Please do not open a public issue for a security problem. Use GitHub's
private vulnerability reporting instead (repository **Security** tab,
**Report a vulnerability**). Include the firmware version or commit, what an
attacker could do, and steps to reproduce.

You can expect an acknowledgement within 7 days. Fixes are released as a new
patch version and credited in the changelog unless you ask otherwise.

## Known limitations (by design)

These are documented trade-offs, not bugs. Reports that show a concrete
impact beyond what is described here are still welcome.

- **The web controller has no login.** Anyone on the same Wi-Fi network can
  open `http://mochi.local`, change the eyes, write the note, or replace the
  saved Wi-Fi credentials (which restarts the device). Keep the device on a
  network you trust.
- **The fallback hotspot uses a shared password** (`AP_PASS` in `secrets.h`,
  default `12345678`). Change it before using the device anywhere public.
- **HTTPS requests do not verify certificates** (`WiFiClientSecure::setInsecure()`).
  The device only reads public, non-sensitive data (weather and coin prices);
  a network attacker could feed it false numbers but cannot obtain secrets.
- **Wi-Fi credentials are stored unencrypted** in the ESP32's NVS flash.
  Anyone with physical access and a USB cable can read them.
- **The location lookup uses plain HTTP** (`ip-api.com`), so the approximate
  city is visible to the network.
- **The Claude Code status endpoint has no authentication**, like the rest of
  the API. Anyone on the network can `POST /api/claude/status` and make the
  display show a fake state or play an alert. It cannot reach Claude Code: the
  device only displays what it is sent.

## Claude Code monitor

The OVO companion (`companion/`) runs on the computer next to Claude Code.

- It is **read-only with respect to authorization**. Hook replies are always
  empty, which Claude Code treats as "no decision", so it can never approve,
  deny or bypass a permission request. The installer never changes
  `permissions` or the permission mode.
- It **sends no private content** to the device: no prompts, assistant text,
  file contents, paths or command arguments, and no credentials. A command is
  shown as its program name, a file as its base name, a URL as its host.
- Its receiver **binds to 127.0.0.1** and refuses browser-style requests
  (an `Origin` header, or form and plain-text bodies).
- **No Anthropic API key or Claude credential** is used, stored or sent by the
  companion or the firmware.

Hook payloads that reach the companion contain your prompts and tool input.
They are processed in memory and are not logged or written to disk.

## Keeping your own secrets out of git

Credentials live in `secrets.h`, which `.gitignore` excludes. Only the
template `secrets.example.h` is committed. If you ever commit a real password
by accident, change the password: deleting the commit does not remove it from
clones or forks.
