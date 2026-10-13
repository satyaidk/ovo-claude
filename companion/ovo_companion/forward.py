"""Clients that Claude Code runs: the hook forwarder and the status line.

Both read Claude Code's JSON from stdin and pass it to the local daemon with a
short timeout. They must never slow Claude Code down or make it show errors,
so every failure is swallowed: if the daemon is not running, the forwarder
exits 0 silently and the status line still prints.

Both also start the daemon when it is not running (autostart.py). SessionStart
is the hook that runs this forwarder, so a new session brings the companion
up; the status line runs after every assistant message, which covers sessions
that were already open.

The status line has a second job: the user may already have one. The
installer records it, and :func:`statusline_main` runs it with the same input
and prints its output, so installing OVO never takes a status line away.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from typing import BinaryIO, Callable, Optional, Sequence, TextIO

from . import autostart
from .config import LISTEN_HOST
from .statusline import parse_statusline, render_line, short_model_name

MAX_STDIN = 16 * 1024 * 1024


def _post(port: int, path: str, body: bytes, timeout_s: float) -> Optional[bytes]:
    """POSTs to the daemon. Returns the reply body, or None if it is not running."""
    req = urllib.request.Request(f"http://{LISTEN_HOST}:{port}{path}", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return resp.read(4096) if 200 <= resp.status < 300 else None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _emit(out: Optional[TextIO], text: str) -> None:
    """Writes UTF-8 even when the console code page could not encode the dot."""
    if not text.endswith("\n"):
        text += "\n"
    if out is not None:
        out.write(text)
        return
    sys.stdout.buffer.write(text.encode("utf-8"))
    sys.stdout.flush()


def hook_main(port: int, stdin: Optional[BinaryIO] = None, timeout_s: float = 1.0,
              ensure: Callable[..., bool] = autostart.ensure_running) -> int:
    """Command-hook transport: forward one hook payload. Always exits 0, prints nothing.

    If the daemon is down, start it, wait up to 3 s, and deliver the event
    once more. The hook runs asynchronously, so Claude Code does not wait.
    """
    stream = stdin or sys.stdin.buffer
    try:
        body = stream.read(MAX_STDIN + 1)
    except OSError:
        return 0
    if body and len(body) <= MAX_STDIN:
        if _post(port, "/ovo/v1/hook", body, timeout_s) is None and ensure(port, wait_s=3.0):
            _post(port, "/ovo/v1/hook", body, timeout_s)
    return 0


def _shell_argv(command: str) -> Sequence[str]:
    """Run a status-line command the way Claude Code would.

    Claude Code uses ``sh -c`` on macOS and Linux, and on Windows Git Bash when
    it is installed, otherwise PowerShell.
    """
    if os.name != "nt":
        return ["/bin/sh", "-c", command]
    bash = os.environ.get("CLAUDE_CODE_GIT_BASH_PATH") or shutil.which("bash")
    if bash:
        return [bash, "-c", command]
    return ["powershell", "-NoProfile", "-Command", command]


def _chained(manifest_path: Optional[str]) -> Optional[str]:
    if not manifest_path:
        return None
    try:
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, ValueError):
        return None
    chain = manifest.get("chain_status_line") if isinstance(manifest, dict) else None
    cmd = chain.get("command") if isinstance(chain, dict) else None
    return cmd if isinstance(cmd, str) and cmd.strip() else None


def statusline_main(port: int, manifest_path: Optional[str] = None, stdin: Optional[BinaryIO] = None,
                    stdout: Optional[TextIO] = None, timeout_s: float = 0.3,
                    ensure: Callable[..., bool] = autostart.ensure_running) -> int:
    stream = stdin or sys.stdin.buffer
    try:
        body = stream.read(MAX_STDIN + 1)
    except OSError:
        body = b""
    reply = _post(port, "/ovo/v1/statusline", body, timeout_s) if body and len(body) <= MAX_STDIN else None
    if reply is None:
        ensure(port, wait_s=0.0)          # start it for next time; the status line never waits
    try:
        link = json.loads(reply.decode("utf-8")).get("link", "unknown") if reply else "down"
    except (ValueError, AttributeError, UnicodeDecodeError):
        link = "unknown"

    chained = _chained(manifest_path)
    if chained:
        try:
            proc = subprocess.run(_shell_argv(chained), input=body, capture_output=True, timeout=10)
            text = proc.stdout.decode("utf-8", "replace").replace("\r\n", "\n")
            if text:
                _emit(stdout, text)
        except (OSError, subprocess.SubprocessError):
            pass
        return 0

    try:
        info = parse_statusline(json.loads(body.decode("utf-8")))
        model, effort = short_model_name(info.model_id, info.model_name), info.effort
    except (ValueError, UnicodeDecodeError):
        model, effort = "", None
    _emit(stdout, render_line(model, effort, link))
    return 0


__all__ = ["hook_main", "statusline_main"]
