"""Read model and effort from Claude Code's status-line input.

Claude Code runs the configured ``statusLine`` command with a JSON object on
stdin after each assistant message, on permission-mode changes, and when a
session starts or resumes (https://code.claude.com/docs/en/statusline). It
carries ``model.display_name`` (for example "Opus") and ``effort.level``, which
reflects mid-session ``/effort`` changes and is *absent* when the current
model has no effort setting. The hooks carry neither the display name nor,
outside tool calls, the effort, so the status line is the authority for both.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from .protocol import ascii_text, normalize_effort


class StatusLinePayloadError(ValueError):
    pass


@dataclass(frozen=True)
class StatusInfo:
    session_id: str
    model_id: Optional[str] = None
    model_name: Optional[str] = None
    effort: Optional[str] = None      # None: the model has no effort setting (or not reported)
    effort_reported: bool = False     # True when the payload had an "effort" object at all


def parse_statusline(payload: Any) -> StatusInfo:
    if not isinstance(payload, Mapping):
        raise StatusLinePayloadError("status-line payload must be a JSON object")
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise StatusLinePayloadError("status-line payload has no session_id")
    model = payload.get("model")
    model_id = model_name = None
    if isinstance(model, Mapping):
        model_id = model.get("id") if isinstance(model.get("id"), str) else None
        model_name = model.get("display_name") if isinstance(model.get("display_name"), str) else None
    elif isinstance(model, str):   # tolerate a bare string
        model_id = model
    effort_obj = payload.get("effort")
    effort = normalize_effort(effort_obj.get("level")) if isinstance(effort_obj, Mapping) else None
    return StatusInfo(session_id=session_id, model_id=model_id or None, model_name=model_name or None,
                      effort=effort, effort_reported=isinstance(effort_obj, Mapping))


_DATE = re.compile(r"^\d{8}$")


def short_model_name(model_id: Optional[str], display_name: Optional[str] = None) -> str:
    """A compact model label for the OLED.

    Prefers Claude Code's own display name. Without it, the model id is only
    reformatted ("claude-opus-4-7" -> "Opus 4.7"); nothing is looked up or
    guessed, and an id that does not follow that pattern is shown as is.
    """
    if display_name:
        return ascii_text(display_name, 24)
    if not model_id:
        return ""
    core = model_id.split("[", 1)[0].split(":", 1)[0]    # drop "[1m]" and Bedrock ":0" suffixes
    core = core.rsplit("/", 1)[-1]                       # gateway prefixes such as "vendor/claude-..."
    if "anthropic." in core:                             # Bedrock: "us.anthropic.claude-..."
        core = core.rsplit("anthropic.", 1)[-1]
    parts = [p for p in core.split("-") if p and not _DATE.match(p) and p not in ("claude", "v1", "latest")]
    family = next((p for p in parts if p.isalpha()), None)
    numbers = [p for p in parts if p.isdigit()]
    if family and numbers and all(p.isalpha() or p.isdigit() for p in parts):
        return ascii_text(f"{family.capitalize()} {'.'.join(numbers)}", 24)
    return ascii_text(model_id, 24)


def render_line(model: str, effort: Optional[str], link: str) -> str:
    """The status line printed when the user had none before.

    The dot shows the path to the device: filled = OVO is receiving, hollow =
    the companion runs but the device does not answer, dotted = the companion
    is not running.
    """
    dot = {"online": "●", "offline": "○"}.get(link, "◌")
    parts = [f"OVO {dot}"]
    if model:
        parts.append(model)
    if effort:
        parts.append(effort)
    return " | ".join(parts)
