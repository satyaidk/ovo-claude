"""Start the companion daemon on demand, so nobody has to remember to.

Claude Code runs the hook forwarder at SessionStart and the status-line
command after every assistant message. Both call :func:`ensure_running`: if
no daemon answers on 127.0.0.1, one is started as a detached background
process that outlives the hook, with its log in the companion's config
directory (``companion.log``). The device address comes from the config file,
which ``ovo-companion install --device ...`` writes.

Two guards keep this cheap and safe:

* start attempts are spaced at least ``START_GAP_S`` apart (a stamp file), so a
  daemon that cannot start, for example because of a bad config, is not
  respawned on every status-line refresh;
* the daemon binds its port exclusively (daemon.py), so when two sessions start
  it at the same moment the second copy exits at once.

Set ``OVO_NO_AUTOSTART=1`` to turn this off and run ``ovo-companion run`` yourself.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Callable, List, Optional, Sequence

from .config import LISTEN_HOST, state_dir

START_GAP_S = 20.0

# Windows process creation flags (from winbase.h).
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def alive(port: int, timeout_s: float = 0.3) -> bool:
    """True if a companion daemon answers on this port."""
    try:
        with urllib.request.urlopen(f"http://{LISTEN_HOST}:{port}/ovo/v1/health", timeout=timeout_s) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def log_path(base: Optional[pathlib.Path] = None) -> pathlib.Path:
    return (base or state_dir()) / "companion.log"


def launch_argv(port: int, base: Optional[pathlib.Path] = None, python: Optional[str] = None) -> List[str]:
    """The command that starts a background daemon (no console window on Windows)."""
    exe = python or sys.executable
    if os.name == "nt":
        windowless = pathlib.Path(exe).with_name("pythonw.exe")
        if windowless.exists():
            exe = str(windowless)
    launcher = pathlib.Path(__file__).resolve().parent.parent / "ovo-companion.py"
    head = [exe, str(launcher)] if launcher.exists() else [exe, "-m", "ovo_companion"]
    return head + ["run", "--port", str(port), "--log-file", str(log_path(base))]


def spawn(argv: Sequence[str], cwd: Optional[pathlib.Path] = None) -> Optional[subprocess.Popen]:
    """Starts argv detached from the caller. None if the OS refused."""
    kwargs = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                  close_fds=True, cwd=str(cwd) if cwd else None)
    if os.name == "nt":
        # Leave Claude Code's job object if allowed, so closing Claude Code
        # does not take the daemon with it; fall back if breakaway is refused.
        for flags in (_DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP | _CREATE_BREAKAWAY_FROM_JOB,
                      _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP):
            try:
                return subprocess.Popen(list(argv), creationflags=flags, **kwargs)
            except OSError:
                continue
        return None
    try:
        return subprocess.Popen(list(argv), start_new_session=True, **kwargs)
    except OSError:
        return None


def _recently_tried(stamp: pathlib.Path, now: float) -> bool:
    try:
        return now - float(stamp.read_text(encoding="utf-8").strip() or 0) < START_GAP_S
    except (OSError, ValueError):
        return False


def ensure_running(port: int, wait_s: float = 0.0, *, spawner: Callable[..., object] = spawn,
                   is_alive: Callable[[int], bool] = alive, clock: Callable[[], float] = time.time,
                   base: Optional[pathlib.Path] = None, env: Optional[dict] = None) -> bool:
    """Makes sure a daemon runs on ``port``. Returns True once one answers.

    With ``wait_s`` > 0 it waits that long for a freshly started daemon to come
    up; with 0 it starts one and returns at once (the status line must not wait).
    """
    if is_alive(port):
        return True
    env = os.environ if env is None else env
    if env.get("OVO_NO_AUTOSTART") in ("1", "true", "yes"):
        return False
    base = base or state_dir()
    stamp = base / "autostart.stamp"
    now = clock()
    if _recently_tried(stamp, now):
        return False
    try:
        base.mkdir(parents=True, exist_ok=True)
        stamp.write_text(repr(now), encoding="utf-8")
    except OSError:
        return False
    if not spawner(launch_argv(port, base), cwd=base):
        return False
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if is_alive(port):
            return True
        time.sleep(0.1)
    return False
