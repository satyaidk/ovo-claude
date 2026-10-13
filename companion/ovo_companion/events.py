"""Parse Claude Code hook payloads into :class:`HookEvent`.

Claude Code sends every hook a JSON object on stdin (command hooks) or as the
POST body (HTTP hooks). Common fields include ``session_id``,
``hook_event_name``, ``permission_mode`` and, inside a tool-use context,
``effort.level``; each event adds its own fields. See
https://code.claude.com/docs/en/hooks.

The parser is deliberately forgiving about *missing* fields, because older
Claude Code versions send fewer of them and some are documented as optional
(``model`` on SessionStart, ``effort`` when the model has no effort setting).
It never invents a value: an absent effort stays ``None``.

Only the fields the tracker needs are kept. The prompt text, the assistant's
last message and tool output are read by nobody and dropped here.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from .protocol import normalize_effort

# Every event the companion understands. Others are accepted and ignored, so a
# newer Claude Code with more events does not break anything.
KNOWN_EVENTS = frozenset({
    "SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure",
    "PermissionRequest", "PermissionDenied", "Notification", "Stop", "StopFailure",
    "SubagentStart", "SubagentStop", "SessionEnd", "PostModelSwitch",
})


class HookPayloadError(ValueError):
    """The payload is not a hook event (not an object, or no session/event)."""


def _str(d: Mapping, key: str) -> Optional[str]:
    v = d.get(key)
    return v if isinstance(v, str) and v else None


def tool_key(tool_name: Optional[str], tool_input: Any) -> str:
    """Identifies one tool call by what it does.

    PermissionRequest carries no tool_use_id, but it has the same tool_name and
    tool_input as the PreToolUse and PostToolUse of that call, so this key
    links a permission prompt to the call that resolves it.
    """
    try:
        blob = json.dumps(tool_input, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        blob = repr(tool_input)
    return hashlib.sha1(f"{tool_name}\x00{blob}".encode("utf-8", "replace")).hexdigest()[:16]


@dataclass(frozen=True)
class HookEvent:
    name: str
    session_id: str
    permission_mode: Optional[str] = None
    effort: Optional[str] = None            # only when the payload carried a documented level
    model: Optional[str] = None             # SessionStart.model or PostModelSwitch.to_model
    tool_name: Optional[str] = None
    tool_input: Mapping = field(default_factory=dict)
    tool_use_id: Optional[str] = None
    notification_type: Optional[str] = None
    source: Optional[str] = None            # SessionStart: startup, resume, clear, compact, fork
    reason: Optional[str] = None            # SessionEnd reason
    error: Optional[str] = None             # StopFailure error type
    is_interrupt: bool = False              # PostToolUseFailure
    background_running: Optional[bool] = None   # Stop: None when Claude Code did not report it
    agent_id: Optional[str] = None          # set inside a subagent
    fingerprint: str = ""                   # identical payloads share it (duplicate detection)

    @property
    def known(self) -> bool:
        return self.name in KNOWN_EVENTS

    @property
    def key(self) -> str:
        return tool_key(self.tool_name, self.tool_input)


def _background_running(payload: Mapping) -> Optional[bool]:
    tasks = payload.get("background_tasks")
    if not isinstance(tasks, list):
        return None
    for task in tasks:
        status = task.get("status") if isinstance(task, Mapping) else None
        if status in (None, "running", "pending", "in_progress"):
            return True
    return False


def parse_hook(payload: Any) -> HookEvent:
    if not isinstance(payload, Mapping):
        raise HookPayloadError("hook payload must be a JSON object")
    name = _str(payload, "hook_event_name")
    session_id = _str(payload, "session_id")
    if not name or not session_id:
        raise HookPayloadError("hook payload needs hook_event_name and session_id")

    effort_obj = payload.get("effort")
    effort = normalize_effort(effort_obj.get("level")) if isinstance(effort_obj, Mapping) else None

    model = None
    if name == "SessionStart":
        model = _str(payload, "model")
    elif name == "PostModelSwitch":
        model = _str(payload, "to_model")

    tool_input = payload.get("tool_input")
    fingerprint = hashlib.sha1(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8", "replace")).hexdigest()

    return HookEvent(
        name=name,
        session_id=session_id,
        permission_mode=_str(payload, "permission_mode"),
        effort=effort,
        model=model,
        tool_name=_str(payload, "tool_name"),
        tool_input=tool_input if isinstance(tool_input, Mapping) else {},
        tool_use_id=_str(payload, "tool_use_id"),
        notification_type=_str(payload, "notification_type"),
        source=_str(payload, "source"),
        reason=_str(payload, "reason"),
        error=_str(payload, "error"),
        is_interrupt=payload.get("is_interrupt") is True,
        background_running=_background_running(payload) if name == "Stop" else None,
        agent_id=_str(payload, "agent_id"),
        fingerprint=fingerprint,
    )
