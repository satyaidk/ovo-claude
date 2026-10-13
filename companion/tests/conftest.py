"""Shared fixtures: a scriptable clock, simulated Claude Code payloads, and a
fake OVO device that records what it receives."""
from __future__ import annotations

import json
import pathlib
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


class FakeClock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture(autouse=True)
def no_autostart(monkeypatch):
    """Tests never launch real background daemons unless they opt in."""
    monkeypatch.setenv("OVO_NO_AUTOSTART", "1")


# ---- simulated hook payloads (shapes from code.claude.com/docs/en/hooks) ----

def hook(event: str, session: str = "sess-aaaa-1111", **extra) -> dict:
    payload = {
        "session_id": session,
        "transcript_path": "/home/user/.claude/projects/p/t.jsonl",
        "cwd": "/home/user/project",
        "permission_mode": "default",
        "hook_event_name": event,
    }
    if event == "UserPromptSubmit":   # each real prompt carries its own prompt_id
        payload["prompt_id"] = str(uuid.uuid4())
        payload.setdefault("prompt", "do the thing")
    payload.update(extra)
    return payload


def pre(tool: str, tool_input: dict, use_id: str, session: str = "sess-aaaa-1111", **extra) -> dict:
    return hook("PreToolUse", session, tool_name=tool, tool_input=tool_input, tool_use_id=use_id,
                effort={"level": "high"}, **extra)


def post(tool: str, tool_input: dict, use_id: str, session: str = "sess-aaaa-1111", **extra) -> dict:
    return hook("PostToolUse", session, tool_name=tool, tool_input=tool_input, tool_use_id=use_id,
                tool_response={"ok": True}, effort={"level": "high"}, **extra)


def permission(tool: str, tool_input: dict, session: str = "sess-aaaa-1111") -> dict:
    return hook("PermissionRequest", session, tool_name=tool, tool_input=tool_input,
                permission_suggestions=[{"type": "addRules", "behavior": "allow"}])


def statusline(session: str = "sess-aaaa-1111", name: str = "Opus", model_id: str = "claude-opus-5-5",
               effort: object = "high") -> dict:
    payload = {"session_id": session, "model": {"id": model_id, "display_name": name},
               "workspace": {"current_dir": "/p"}, "version": "2.1.296"}
    if effort is not None:
        payload["effort"] = {"level": effort}
    return payload


# ---- a fake device ----

class FakeDevice:
    """Answers POST /api/claude/status like the firmware and records bodies."""

    def __init__(self):
        self.received = []
        self.mode = "ok"            # ok | reject | slow | missing | drop
        self.delay = 0.0
        self.event = threading.Event()
        dev = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n)
                if dev.mode == "slow":
                    time.sleep(dev.delay)
                if dev.mode == "drop":
                    self.close_connection = True
                    return
                if dev.mode == "missing":
                    code, reply = 404, {"ok": False, "error": "unknown endpoint"}
                elif dev.mode == "reject":
                    code, reply = 400, {"ok": False, "error": "state: unknown value"}
                else:
                    dev.received.append(json.loads(body))
                    dev.event.set()
                    code, reply = 200, {"ok": True, "reply": "ok claude"}
                data = json.dumps(reply).encode()
                try:
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except OSError:
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def wait_for(self, predicate, timeout: float = 5.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for item in list(self.received):
                if predicate(item):
                    return item
            self.event.wait(0.05)
            self.event.clear()
        return None

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def device():
    d = FakeDevice()
    yield d
    d.close()


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Keeps tests away from the real ~/.claude and config directories."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: home))
    return home
