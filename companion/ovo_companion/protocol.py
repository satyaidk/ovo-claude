"""The JSON message the firmware accepts at ``POST /api/claude/status``.

This module mirrors ``firmware/MochiEyes/src/claude/ClaudeStatus.h``: the
same field names, length limits and allowed values. Everything sent to the
device passes through :func:`build_payload`, which is the single place where
text is cut to length and reduced to printable ASCII (the OLED font has no
other glyphs). Nothing here ever carries a prompt, a command line, file
contents or the assistant's text: only labels the companion produced itself.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

PROTOCOL_VERSION = 1
MAX_BODY = 1024

LIMITS = {"cid": 16, "session_id": 16, "event": 24, "model": 24, "activity": 32}

STATES = ("idle", "running", "approval", "turn_done", "task_done", "failed", "ended")

# Claude Code's documented permission_mode values. Anything else is sent as
# "other" so a future mode can never be mistaken for a known one.
PERMISSION_MODES = ("default", "plan", "acceptEdits", "auto", "dontAsk", "bypassPermissions")

# Claude Code's documented effort levels. There is no "ultra".
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")

# How the OLED labels each mode. Only "auto" (a classifier reviews each action
# instead of you) is AUTO; the others keep distinct labels because they mean
# different things: acceptEdits approves file edits only, bypassPermissions
# skips all checks, dontAsk denies anything not pre-approved.
PERMISSION_LABELS = {
    "default": "MANUAL",
    "plan": "PLAN",
    "acceptEdits": "EDITS",
    "auto": "AUTO",
    "dontAsk": "DONTASK",
    "bypassPermissions": "BYPASS",
    "other": "OTHER",
}

MAX_SECONDS = 30 * 24 * 3600


def ascii_text(value: object, limit: int) -> str:
    """Printable ASCII, at most ``limit`` characters; a cut string ends in '.'."""
    if value is None:
        return ""
    text = " ".join(str(value).split())  # newlines, tabs and runs of spaces become one space
    text = "".join(ch if " " <= ch <= "~" else "?" for ch in text)
    if len(text) > limit:
        text = text[: limit - 1] + "."
    return text


def ident(value: object, limit: int) -> str:
    """Letters, digits, '-' and '_' only (the firmware's rule for ids)."""
    if value is None:
        return ""
    text = "".join(ch for ch in str(value) if ch.isascii() and (ch.isalnum() or ch in "-_"))
    return text[:limit]


def normalize_permission(raw: Optional[str]) -> str:
    if not raw:
        return ""
    return raw if raw in PERMISSION_MODES else "other"


def normalize_effort(raw: object) -> Optional[str]:
    """A documented effort level, or None. Never guesses."""
    return raw if isinstance(raw, str) and raw in EFFORT_LEVELS else None


def clamp_seconds(value: float) -> int:
    return max(0, min(MAX_SECONDS, int(value)))


@dataclass(frozen=True)
class Snapshot:
    """What the OLED should show for the primary session."""

    state: str = "idle"
    session_id: str = ""
    sessions: int = 0
    event: str = ""
    model: str = ""
    effort: str = ""
    activity: str = ""
    permission_mode: str = ""
    approval_required: bool = False
    elapsed_seconds: int = 0
    session_seconds: int = 0

    def content_key(self) -> tuple:
        """Everything except the timers: a change here is worth sending now."""
        return (self.state, self.session_id, self.sessions, self.event, self.model, self.effort,
                self.activity, self.permission_mode, self.approval_required)


def build_payload(snap: Snapshot, cid: str, seq: int, connected: bool = True) -> dict:
    if snap.state not in STATES:
        raise ValueError(f"unknown state {snap.state!r}")
    payload = {
        "v": PROTOCOL_VERSION,
        "seq": int(seq) & 0xFFFFFFFF,
        "cid": ident(cid, LIMITS["cid"]),
        "connected": bool(connected),
        "state": snap.state,
        "session_id": ident(snap.session_id, LIMITS["session_id"]),
        "sessions": max(0, min(99, int(snap.sessions))),
        "event": ident(snap.event, LIMITS["event"]),
        "model": ascii_text(snap.model, LIMITS["model"]),
        "effort": normalize_effort(snap.effort) or "",
        "activity": ascii_text(snap.activity, LIMITS["activity"]),
        "permission_mode": normalize_permission(snap.permission_mode),
        "approval_required": bool(snap.approval_required or snap.state == "approval"),
        "elapsed_seconds": clamp_seconds(snap.elapsed_seconds),
        "session_seconds": clamp_seconds(snap.session_seconds),
    }
    if not payload["cid"]:
        raise ValueError("cid must contain letters or digits")
    return payload


def encode(payload: dict) -> bytes:
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    if len(body) > MAX_BODY:
        raise ValueError(f"payload is {len(body)} bytes, the device accepts {MAX_BODY}")
    return body
