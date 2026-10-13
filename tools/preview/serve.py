#!/usr/bin/env python3
"""Preview the controller page on a PC, without a device.

Serves web/index.html and imitates the firmware's JSON API (same routes and
fields as MochiEyes.ino), so you can work on the page with a fast edit-reload
loop. The live mirror plays frames rendered by the real eye engine
(tools/preview/frames.cpp); without them it shows a still test pattern.

Usage (from the repository root):
    python tools/preview/serve.py [--port 8000] [--frames build/frames.bin]
Then open http://127.0.0.1:8000
"""
import argparse
import json
import pathlib
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

ROOT = pathlib.Path(__file__).resolve().parents[2]
FRAME = 1024

STATE = {
    "owner": "Kitty", "mode": "eyes", "screen": "clock",
    "eye": {"mood": "calm", "expr": "neutral", "state": "eye contact", "react": "none",
            "sleeping": False, "drowsy": False, "demo": False},
    "time": {"synced": True, "hhmm": "", "date": ""},
    "wifi": {"ap": False, "ssid": "Home 2.4G", "ip": "192.168.1.42", "rssi": -58, "up": True, "host": "mochi.local"},
    "oled": {"ok": True, "sda": 20, "scl": 21, "addr": 60},
    "weather": {"ok": True, "city": "Bengaluru", "temp": 24.6, "unit": "C", "word": "Part cloudy", "hum": 71, "min": 20, "max": 29},
    "coins": [{"tag": "BTC", "ok": True, "usd": "64210", "chg": 1.4},
              {"tag": "ETH", "ok": True, "usd": "3120", "chg": -0.8},
              {"tag": "SOL", "ok": True, "usd": "148.22", "chg": 3.1}],
    "fetching": False,
    "note": {"title": "Notes", "text": "Tap the pad to change pages.\n\nOpen http://mochi.local\nto write here."},
    "settings": {"sound": True, "autosleep": True, "start": "eyes",
                 "cc_perm": True, "cc_done": True, "cc_fail": True, "cc_lost": False, "cc_stale": 15},
    "claude": {"link": "live", "screen": "running", "state": "running", "model": "Opus", "effort": "high",
               "activity": "Running tests", "permission_mode": "default", "permission_label": "MANUAL",
               "approval": False, "elapsed": 763, "session_elapsed": 2405, "sessions": 1, "session": "3f2a9c1e",
               "event": "PreToolUse", "age_ms": 1200, "accepted": 0, "rejected": 0},
    "sys": {"uptime": 0, "heap": 171000, "minHeap": 148000, "fps": 35.2, "fetchStackFree": 5200},
}
CLAUDE_STATES = ("idle", "running", "approval", "turn_done", "task_done", "failed", "ended")
PERMISSION_LABELS = {"default": "MANUAL", "plan": "PLAN", "acceptEdits": "EDITS", "auto": "AUTO",
                     "dontAsk": "DONTASK", "bypassPermissions": "BYPASS", "other": "OTHER", "": ""}
STARTED = time.time()
FRAMES: list = []


def test_pattern() -> bytes:
    fb = bytearray(FRAME)
    for x in range(128):
        for y in (0, 63):
            fb[(y >> 3) * 128 + x] |= 1 << (y & 7)
    return bytes(fb)


def run_command(cmd: str) -> tuple:
    parts = cmd.split()
    if not parts:
        return False, "?"
    verb, arg = parts[0].lower(), (parts[1] if len(parts) > 1 else "")
    e = STATE["eye"]
    if verb == "mood":
        e["mood"] = arg
    elif verb == "expr":
        e["expr"] = arg
    elif verb == "sleep":
        e["sleeping"] = True
    elif verb == "wake":
        e["sleeping"] = False
    elif verb == "demo":
        e["demo"] = arg != "off"
    elif verb == "mode":
        order = ("eyes", "clock", "claude")
        if arg and arg not in order:
            return False, "err mode: eyes|clock|claude"
        STATE["mode"] = arg or order[(order.index(STATE["mode"]) + 1) % 3]
    elif verb == "gesture":
        e["react"] = {"1": "glance", "2": "happy", "3": "grumpy", "4": "dizzy", "pet": "petting"}.get(arg, "none")
        STATE["mode"] = "eyes"
    elif verb == "lookn":
        e["state"] = "manual"
    elif verb == "auto":
        e["state"] = "eye contact"
    return True, "ok " + cmd


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj).encode())

    def log_message(self, fmt, *args):  # keep the console quiet
        if not self.path.startswith(("/api/frame", "/api/state")):
            super().log_message(fmt, *args)

    def do_GET(self):
        if self.path == "/":
            self._send(200, (ROOT / "web" / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif self.path.startswith("/api/state"):
            now = time.localtime()
            STATE["time"]["hhmm"] = time.strftime("%H:%M", now)
            STATE["time"]["date"] = time.strftime("%a %d %b", now)
            STATE["sys"]["uptime"] = int(time.time() - STARTED) + 3 * 3600
            self._json(STATE)
        elif self.path.startswith("/api/frame"):
            i = int((time.time() - STARTED) * 20) % len(FRAMES)
            self._send(200, FRAMES[i], "application/octet-stream")
        else:
            self._json({"ok": False, "error": "unknown endpoint"}, 404)

    def _claude_status(self, raw: bytes):
        """A light imitation of the firmware's /api/claude/status (see ClaudeStatus.h for the real rules)."""
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self._json({"ok": False, "error": "expected Content-Type: application/json"}, 415)
        if len(raw) > 1024:
            return self._json({"ok": False, "error": "body larger than 1024 bytes"}, 413)
        try:
            msg = json.loads(raw)
        except ValueError:
            return self._json({"ok": False, "error": "body: invalid JSON"}, 400)
        if not isinstance(msg, dict) or msg.get("v") != 1 or msg.get("state") not in CLAUDE_STATES:
            return self._json({"ok": False, "error": "body: needs v=1, seq, cid and a known state"}, 400)
        c = STATE["claude"]
        mode = msg.get("permission_mode", "")
        c.update({"link": "live" if msg.get("connected", True) else "closed", "state": msg["state"],
                  "model": msg.get("model", ""), "effort": msg.get("effort", ""), "activity": msg.get("activity", ""),
                  "permission_mode": mode, "permission_label": PERMISSION_LABELS.get(mode, "OTHER"),
                  "approval": bool(msg.get("approval_required")) or msg["state"] == "approval",
                  "elapsed": msg.get("elapsed_seconds", 0), "session_elapsed": msg.get("session_seconds", 0),
                  "sessions": msg.get("sessions", 0), "session": msg.get("session_id", ""), "event": msg.get("event", ""),
                  "age_ms": 0, "accepted": c["accepted"] + 1})
        return self._json({"ok": True, "reply": "ok claude"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        if self.path == "/api/claude/status":
            return self._claude_status(raw)
        form = {k: v[0] for k, v in parse_qs(raw.decode()).items()}
        route = self.path
        if route == "/api/eye":
            ok, reply = run_command(form.get("cmd", ""))
            return self._json({"ok": ok, "reply": reply}, 200 if ok else 400)
        if route == "/api/mode":
            order = ("eyes", "clock", "claude")
            m = form.get("m", "toggle")
            STATE["mode"] = m if m in order else order[(order.index(STATE["mode"]) + 1) % 3]
        elif route == "/api/screen":
            STATE["screen"], STATE["mode"] = form.get("s", "clock"), "clock"
        elif route == "/api/note":
            STATE["note"] = {"title": form.get("title") or "Notes", "text": form.get("text", "")}
            STATE["screen"], STATE["mode"] = "notes", "clock"
        elif route == "/api/settings":
            s = STATE["settings"]
            for k in ("sound", "autosleep", "cc_perm", "cc_done", "cc_fail", "cc_lost"):
                if k in form:
                    s[k] = form[k] == "1"
            if "start" in form:
                s["start"] = form["start"] if form["start"] in ("eyes", "clock", "claude") else "eyes"
            if "cc_stale" in form:
                if not form["cc_stale"].isdigit() or not 5 <= int(form["cc_stale"]) <= 600:
                    return self._json({"ok": False, "error": "cc_stale: 5..600 seconds"}, 400)
                s["cc_stale"] = int(form["cc_stale"])
        elif route not in ("/api/refresh", "/api/wifi"):
            return self._json({"ok": False, "error": "unknown endpoint"}, 404)
        self._json({"ok": True, "reply": "ok"})


def main():
    ap = argparse.ArgumentParser(description="Preview the MochiEyes web page without a device.")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--frames", default=str(ROOT / "build" / "frames.bin"))
    args = ap.parse_args()
    p = pathlib.Path(args.frames)
    if p.exists():
        data = p.read_bytes()
        FRAMES.extend(data[i:i + FRAME] for i in range(0, len(data) - FRAME + 1, FRAME))
    if not FRAMES:
        print(f"no frames at {p}; showing a test pattern (see tools/preview/frames.cpp)")
        FRAMES.append(test_pattern())
    print(f"MochiEyes preview on http://127.0.0.1:{args.port}  ({len(FRAMES)} frames)")
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
