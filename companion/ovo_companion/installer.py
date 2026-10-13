"""Add the OVO hooks and status line to Claude Code settings, and take them out.

Rules this module keeps:

* **Merge, never replace.** Only two things are touched: one OVO handler per
  hook event (in its own matcher group) and the ``statusLine`` key. Every
  other hook, permission, environment variable and setting is left exactly
  as it was, including key order.
* **Back up first.** The settings file is copied to
  ``<name>.ovo-backup-<UTC time>`` before every write.
* **Idempotent.** Installing again updates the OVO handlers in place; it
  never adds a second copy, and the remembered original status line is kept.
* **Precise removal.** Uninstall deletes only handlers that point at the OVO
  receiver (or run ``ovo_companion ... hook``), drops matcher groups and
  event lists only if that left them empty, and restores the original status
  line, unless you changed it yourself after installing.
* **No security changes.** Nothing is added to ``permissions``, no mode is
  changed, and the hooks answer "no decision" (see daemon.py).

Where things go:

* scope ``local`` (default): ``<project>/.claude/settings.local.json``, which
  Claude Code treats as personal and keeps out of git;
* scope ``user``: ``~/.claude/settings.json``, for every project.

The install record (the original status line, the transport and so on) is
kept outside the project, under the companion's config directory, so the
repository gains no new files.
"""
from __future__ import annotations

import copy
import datetime as _dt
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from .config import DEFAULT_PORT, LISTEN_HOST, state_dir

HOOK_PATH = "/ovo/v1/hook"

# Events present in every Claude Code 2.x release the companion was written against.
CORE_EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse",
               "Notification", "Stop", "SubagentStop", "SessionEnd")
# Newer events. All are documented for the version this was verified with
# (2.1.296); PostModelSwitch, the newest of them, needs 2.1.251.
EXTENDED_EVENTS = ("PostToolUseFailure", "PermissionRequest", "PermissionDenied",
                   "StopFailure", "SubagentStart", "PostModelSwitch")
EXTENDED_MIN_VERSION = (2, 1, 251)
VERIFIED_VERSION = (2, 1, 296)
# SessionStart accepts only command (and mcp_tool) handlers, not http. Found by
# running Claude Code 2.1.296 against the daemon, then confirmed in the docs.
COMMAND_ONLY_EVENTS = ("SessionStart",)

_OUR_COMMAND = re.compile(r"ovo[-_]companion(?:\.py)?\b.*\b(hook|statusline)\b")


class InstallError(RuntimeError):
    pass


# ------------------------------------------------------------------ paths --

def settings_path(scope: str, project_dir: pathlib.Path, home: Optional[pathlib.Path] = None) -> pathlib.Path:
    home = home or pathlib.Path.home()
    if scope == "local":
        return pathlib.Path(project_dir).resolve() / ".claude" / "settings.local.json"
    if scope == "user":
        return home / ".claude" / "settings.json"
    raise InstallError(f"unknown scope {scope!r} (use local or user)")


def inherited_settings(scope: str, project_dir: pathlib.Path, home: Optional[pathlib.Path] = None) -> List[pathlib.Path]:
    """Lower-precedence files whose statusLine applies if the target has none."""
    home = home or pathlib.Path.home()
    if scope == "local":
        return [pathlib.Path(project_dir).resolve() / ".claude" / "settings.json", home / ".claude" / "settings.json"]
    return []


def manifest_path(target: pathlib.Path, base: Optional[pathlib.Path] = None) -> pathlib.Path:
    digest = hashlib.sha1(str(target.resolve()).lower().encode("utf-8")).hexdigest()[:16]
    return (base or state_dir()) / "installs" / f"{digest}.json"


# ------------------------------------------------------- version detection --

def parse_version(text: str) -> Optional[Tuple[int, int, int]]:
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(x) for x in m.groups()) if m else None  # type: ignore[return-value]


def detect_claude_version(run: Callable[..., Any] = subprocess.run) -> Optional[Tuple[int, int, int]]:
    exe = shutil.which("claude")
    if not exe:
        return None
    try:
        proc = run([exe, "--version"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_version(proc.stdout or "")


def choose(version: Optional[Tuple[int, int, int]], transport: str = "auto", events: str = "auto") -> Tuple[str, Tuple[str, ...]]:
    """Picks the transport and event list for a Claude Code version.

    HTTP hooks and the newer events are used when the version is known to have
    them. An unknown or older version gets command hooks and the core events,
    which every 2.x release understands.
    """
    modern = version is not None and version >= EXTENDED_MIN_VERSION
    if transport == "auto":
        transport = "http" if modern else "command"
    if transport not in ("http", "command"):
        raise InstallError("transport must be auto, http or command")
    if events == "auto":
        chosen = CORE_EVENTS + EXTENDED_EVENTS if modern else CORE_EVENTS
    elif events == "all":
        chosen = CORE_EVENTS + EXTENDED_EVENTS
    elif events == "core":
        chosen = CORE_EVENTS
    else:
        raise InstallError("events must be auto, core or all")
    return transport, chosen


# ------------------------------------------------------------- commands --

def _q(path: str) -> str:
    """Forward slashes (Git Bash eats backslashes); quotes only when needed."""
    path = path.replace("\\", "/")
    return f'"{path}"' if re.search(r"[\s'\"&;()|<>$`]", path) else path


def companion_command(subcommand: str, extra: str = "", python: Optional[str] = None) -> str:
    """The shell command Claude Code runs for the hook or the status line."""
    py = _q(python or sys.executable)
    launcher = pathlib.Path(__file__).resolve().parent.parent / "ovo-companion.py"
    head = f"{py} {_q(str(launcher))}" if launcher.exists() else f"{py} -m ovo_companion"
    return f"{head} {subcommand}{(' ' + extra) if extra else ''}"


def hook_handler(transport: str, port: int, python: Optional[str] = None, event: str = "") -> Dict[str, Any]:
    if transport == "http" and event not in COMMAND_ONLY_EVENTS:
        # Empty 2xx answers mean "no decision"; the 2 s timeout bounds any delay.
        return {"type": "http", "url": f"http://{LISTEN_HOST}:{port}{HOOK_PATH}", "timeout": 2}
    # async: Claude Code does not wait for Python to start. The forwarder prints
    # nothing, so it adds nothing to Claude's context (SessionStart stdout would).
    return {"type": "command", "command": companion_command("hook", f"--port {port}", python),
            "async": True, "timeout": 10}


def is_ovo_handler(handler: Any) -> bool:
    if not isinstance(handler, dict):
        return False
    if handler.get("type") == "http" and isinstance(handler.get("url"), str):
        try:
            u = urlsplit(handler["url"])
        except ValueError:
            return False
        return u.hostname in ("127.0.0.1", "localhost") and u.path == HOOK_PATH
    if handler.get("type") == "command" and isinstance(handler.get("command"), str):
        m = _OUR_COMMAND.search(handler["command"])
        return bool(m and m.group(1) == "hook")
    return False


def is_ovo_status_line(value: Any) -> bool:
    if not isinstance(value, dict) or not isinstance(value.get("command"), str):
        return False
    m = _OUR_COMMAND.search(value["command"])
    return bool(m and m.group(1) == "statusline")


# --------------------------------------------------------- pure transforms --

def _check_shape(settings: Any) -> None:
    if not isinstance(settings, dict):
        raise InstallError("settings file must contain a JSON object")
    hooks = settings.get("hooks")
    if hooks is None:
        return
    if not isinstance(hooks, dict):
        raise InstallError('"hooks" must be an object; fix the settings file first')
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            raise InstallError(f'"hooks.{event}" must be a list; fix the settings file first')


def remove_ovo_hooks(settings: Dict[str, Any]) -> int:
    """Removes OVO handlers in place. Returns how many were removed."""
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return 0
    removed = 0
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        kept_groups = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                kept_groups.append(group)
                continue
            handlers = group["hooks"]
            kept = [h for h in handlers if not is_ovo_handler(h)]
            removed += len(handlers) - len(kept)
            if kept or not handlers or len(kept) == len(handlers):
                group = dict(group, hooks=kept) if len(kept) != len(handlers) else group
                kept_groups.append(group)
            # else: the group held only OVO handlers; drop it
        if len(kept_groups) != len(groups):
            if kept_groups:
                hooks[event] = kept_groups
            else:
                del hooks[event]
        else:
            hooks[event] = kept_groups
    return removed


def plan_install(settings: Dict[str, Any], *, transport: str, events: Tuple[str, ...], port: int,
                 status_command: str, previous: Optional[Dict[str, Any]],
                 inherited_status_line: Optional[Dict[str, Any]], python: Optional[str] = None
                 ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Returns (new settings, install record). Does not touch the disk.

    ``previous`` is the record of an earlier install into the same file, so a
    reinstall keeps the original status line instead of recording its own.
    """
    _check_shape(settings)
    new = copy.deepcopy(settings)
    had_hooks_key = "hooks" in settings if previous is None else previous.get("had_hooks_key", "hooks" in settings)
    remove_ovo_hooks(new)
    hooks = new.setdefault("hooks", {})
    for event in events:
        hooks.setdefault(event, []).append({"hooks": [hook_handler(transport, port, python, event)]})

    current = settings.get("statusLine")
    if previous is not None and is_ovo_status_line(current):
        original = previous.get("original_status_line")
        had_status_line = previous.get("had_status_line", False)
        chain = previous.get("chain_status_line")
    else:
        original = copy.deepcopy(current) if current is not None else None
        had_status_line = "statusLine" in settings
        chain = original if original is not None else inherited_status_line
        if chain is not None and is_ovo_status_line(chain):
            chain = None
    status_line: Dict[str, Any] = {"type": "command", "command": status_command}
    if isinstance(chain, dict):
        for key in ("padding", "refreshInterval", "hideVimModeIndicator"):
            if key in chain:
                status_line[key] = chain[key]
    new["statusLine"] = status_line

    record = {
        "tool": "ovo-companion",
        "record_version": 1,
        "transport": transport,
        "port": port,
        "events": list(events),
        "had_hooks_key": had_hooks_key,
        "had_status_line": had_status_line,
        "original_status_line": original,
        "chain_status_line": copy.deepcopy(chain) if isinstance(chain, dict) else None,
        "status_line_command": status_command,
    }
    return new, record


def plan_uninstall(settings: Dict[str, Any], record: Optional[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[str]]:
    """Returns (new settings, notes). Does not touch the disk."""
    _check_shape(settings)
    new = copy.deepcopy(settings)
    notes: List[str] = []
    removed = remove_ovo_hooks(new)
    notes.append(f"removed {removed} OVO hook handler(s)")
    if isinstance(new.get("hooks"), dict) and not new["hooks"]:
        if record is None or not record.get("had_hooks_key", False):
            del new["hooks"]

    current = new.get("statusLine")
    if is_ovo_status_line(current):
        if record and record.get("had_status_line"):
            new["statusLine"] = copy.deepcopy(record.get("original_status_line"))
            notes.append("restored your previous status line")
        else:
            del new["statusLine"]
            notes.append("removed the OVO status line")
    elif current is not None:
        notes.append("left your status line alone (it was changed after installing)")
    return new, notes


# ------------------------------------------------------------------- disk --

def _read_json(path: pathlib.Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise InstallError(f"{path} is not valid JSON ({exc}); nothing was changed") from exc
    if not isinstance(data, dict):
        raise InstallError(f"{path} must contain a JSON object; nothing was changed")
    return data


def _backup(path: pathlib.Path, now: Optional[_dt.datetime] = None) -> Optional[pathlib.Path]:
    if not path.exists():
        return None
    stamp = (now or _dt.datetime.now(_dt.timezone.utc)).strftime("%Y%m%dT%H%M%S%fZ")
    dest = path.with_name(f"{path.name}.ovo-backup-{stamp}")
    shutil.copy2(path, dest)
    return dest


def _write_json(path: pathlib.Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".ovo-tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)   # atomic: a crash never leaves a half-written settings file


@dataclass
class Report:
    settings_file: pathlib.Path
    record_file: pathlib.Path
    backup: Optional[pathlib.Path] = None
    changed: bool = False
    transport: str = ""
    events: Tuple[str, ...] = ()
    notes: List[str] = field(default_factory=list)


def install(scope: str = "local", project_dir: pathlib.Path = pathlib.Path("."), *, transport: str = "auto",
            events: str = "auto", port: int = DEFAULT_PORT, version: Optional[Tuple[int, int, int]] = None,
            dry_run: bool = False, home: Optional[pathlib.Path] = None, state: Optional[pathlib.Path] = None,
            python: Optional[str] = None) -> Report:
    target = settings_path(scope, project_dir, home)
    record_file = manifest_path(target, state)
    chosen_transport, chosen_events = choose(version, transport, events)
    settings = _read_json(target)
    previous = _read_json(record_file) or None

    inherited = None
    for p in inherited_settings(scope, project_dir, home):
        sl = _read_json(p).get("statusLine")
        if isinstance(sl, dict):
            inherited = sl
            break

    status_cmd = companion_command("statusline", f"--port {port} --manifest {_q(str(record_file))}", python)
    new, record = plan_install(settings, transport=chosen_transport, events=chosen_events, port=port,
                               status_command=status_cmd, previous=previous,
                               inherited_status_line=inherited, python=python)
    record.update({"scope": scope, "settings_file": str(target),
                   "created_file": previous.get("created_file", False) if previous else not target.exists(),
                   "installed_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")})
    report = Report(target, record_file, transport=chosen_transport, events=chosen_events)
    if version is None:
        report.notes.append("Claude Code version unknown: using command hooks and core events only")
    elif version > VERIFIED_VERSION:
        report.notes.append("Claude Code is newer than the version this was verified with "
                            f"({'.'.join(map(str, VERIFIED_VERSION))})")
    report.changed = new != settings
    if dry_run:
        report.notes.append("dry run: nothing written")
        return report
    if report.changed:
        report.backup = _backup(target)
        _write_json(target, new)
    _write_json(record_file, record)
    return report


def uninstall(scope: str = "local", project_dir: pathlib.Path = pathlib.Path("."), *, dry_run: bool = False,
              home: Optional[pathlib.Path] = None, state: Optional[pathlib.Path] = None) -> Report:
    target = settings_path(scope, project_dir, home)
    record_file = manifest_path(target, state)
    record = _read_json(record_file) or None
    settings = _read_json(target)
    new, notes = plan_uninstall(settings, record)
    report = Report(target, record_file, notes=notes)
    if record is None:
        report.notes.append("no install record found; removed OVO entries by pattern only")
    report.changed = new != settings
    if dry_run:
        report.notes.append("dry run: nothing written")
        return report
    if report.changed:
        report.backup = _backup(target)
        if not new and record and record.get("created_file"):
            target.unlink()           # the installer created this file; leave no empty file behind
            report.notes.append(f"deleted {target.name}, which the installer had created")
        else:
            _write_json(target, new)
    if record_file.exists():
        record_file.unlink()
    return report
