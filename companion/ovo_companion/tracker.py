"""Session tracking: from a stream of hook events to one status for the OLED.

Claude Code can run several sessions at once (terminals, IDE panels), each
sending its own hook events. The tracker keeps one :class:`Session` per
``session_id`` and decides which one the 128x64 screen shows.

States per session (the device draws one screen for each):

    idle       session open, no prompt being worked on
    running    a prompt is being worked on (UserPromptSubmit .. Stop)
    approval   Claude Code is waiting for a permission decision
    turn_done  Claude stopped responding, but the evidence does not show the
               whole task is finished: background work is still running, the
               turn was interrupted, or this Claude Code version does not
               report background work
    task_done  Claude stopped AND Claude Code reported no running background
               work, so nothing more happens until you write again
    failed     the turn ended with an API error (StopFailure)
    ended      the session ended (SessionEnd); shown briefly, then dropped

Ordering and duplicates. HTTP hooks arrive in order, but command hooks run
asynchronously and can overtake each other, and the same handler installed
twice could deliver an event twice. So:

* an identical payload seen again within ``dedupe_s`` is dropped;
* a PreToolUse for a tool call that already finished is stale and dropped;
* a PostToolUse for a call that started before the turn ended does not
  reopen the turn;
* after SessionEnd, late events for that session are dropped (a new
  SessionStart revives it, as ``--resume`` keeps the id).

Approvals. PermissionRequest and the ``permission_prompt`` Notification both
announce the same prompt; the first one opens it and the second is merged. A
prompt closes when the call it belongs to finishes (matched by tool name and
input, since PermissionRequest has no tool_use_id), or when the turn moves on
(a new prompt, Stop, StopFailure, SessionEnd). Plain notifications such as
``idle_prompt`` never open one.

Primary session (deterministic):

1. a session waiting for approval; the one that has waited longest wins;
2. otherwise the current primary, if it is still running;
3. otherwise the running session with the most recent event;
4. otherwise the session with the most recent event.

Ties break on ``session_id``. Limitation: only the primary is drawn; the
header shows how many sessions exist ("x2").

Time comes from an injected monotonic clock, so wall-clock changes never move
a timer and tests can script any timeline.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from .activity import failure_label, permission_action, tool_activity
from .events import HookEvent, HookPayloadError, parse_hook, tool_key
from .protocol import Snapshot, normalize_permission
from .statusline import StatusInfo, parse_statusline, short_model_name

DONE_STATES = ("turn_done", "task_done", "failed", "ended")

# A permission prompt announced only by a Notification (no PermissionRequest
# details yet). Replaced as soon as the PermissionRequest arrives.
_NOTIFICATION_KEY = "notification"

_SESSION_START_LABELS = {
    "startup": "Starting session",
    "resume": "Session resumed",
    "clear": "Session cleared",
    "compact": "Context compacted",
    "fork": "Session forked",
}


@dataclass
class Session:
    session_id: str
    started_at: float
    last_event_at: float
    state: str = "idle"
    activity: str = ""
    event: str = ""
    model_id: Optional[str] = None
    model_name: Optional[str] = None
    effort: Optional[str] = None
    permission_mode: Optional[str] = None
    task_started_at: Optional[float] = None
    task_ended_at: Optional[float] = None
    ended_at: Optional[float] = None
    turn: int = 0
    # Open permission prompts: tool key -> (opened at, action label). Ordered.
    approvals: "OrderedDict[str, tuple]" = field(default_factory=OrderedDict)
    finished_tools: "OrderedDict[str, None]" = field(default_factory=OrderedDict)

    def elapsed(self, now: float) -> float:
        if self.task_started_at is None:
            return 0.0
        end = self.task_ended_at if self.task_ended_at is not None else now
        return max(0.0, end - self.task_started_at)

    def session_elapsed(self, now: float) -> float:
        end = self.ended_at if self.ended_at is not None else now
        return max(0.0, end - self.started_at)


def _remember(od: OrderedDict, key: Any, value: Any = None, cap: int = 256) -> None:
    od[key] = value
    od.move_to_end(key)
    while len(od) > cap:
        od.popitem(last=False)


class Tracker:
    def __init__(self, clock: Callable[[], float] = time.monotonic, *, dedupe_s: float = 10.0,
                 expiry_s: float = 2 * 3600.0, ended_linger_s: float = 60.0):
        self._clock = clock
        self._lock = threading.RLock()
        self._sessions: Dict[str, Session] = {}
        self._ended: "OrderedDict[str, float]" = OrderedDict()    # tombstones
        self._recent: "OrderedDict[str, float]" = OrderedDict()   # fingerprint -> seen at
        self._primary: Optional[str] = None
        self.dedupe_s = dedupe_s
        self.expiry_s = expiry_s
        self.ended_linger_s = ended_linger_s
        self.stats = {"events": 0, "duplicates": 0, "stale": 0, "ignored": 0, "invalid": 0}

    # ---------------------------------------------------------------- input --

    def handle_hook(self, payload: Any) -> bool:
        """Applies one hook payload. Returns True if the primary snapshot changed."""
        try:
            ev = parse_hook(payload)
        except HookPayloadError:
            with self._lock:
                self.stats["invalid"] += 1
            return False
        return self.apply(ev)

    def handle_statusline(self, payload: Any) -> bool:
        try:
            info = parse_statusline(payload)
        except ValueError:
            with self._lock:
                self.stats["invalid"] += 1
            return False
        return self.apply_status(info)

    def apply(self, ev: HookEvent) -> bool:
        with self._lock:
            before = self._snapshot_locked().content_key()
            now = self._clock()
            self._prune_locked(now)
            self.stats["events"] += 1
            if self._is_duplicate(ev, now):
                self.stats["duplicates"] += 1
                return False
            if not ev.known:
                self.stats["ignored"] += 1
                return False
            if ev.session_id in self._ended and ev.name != "SessionStart":
                self.stats["stale"] += 1
                return False
            s = self._session(ev.session_id, now)
            if self._dispatch(s, ev, now):
                s.last_event_at = now
                s.event = ev.name
                if ev.permission_mode:
                    s.permission_mode = ev.permission_mode
                if ev.effort:                 # absent in many events: keep what we knew
                    s.effort = ev.effort
            return self._snapshot_locked().content_key() != before

    def apply_status(self, info: StatusInfo) -> bool:
        with self._lock:
            before = self._snapshot_locked().content_key()
            now = self._clock()
            if info.session_id in self._ended:
                return False
            s = self._session(info.session_id, now)
            if info.model_id or info.model_name:
                s.model_id = info.model_id or s.model_id
                s.model_name = info.model_name
            # The status line reflects the live setting: no effort object means
            # the current model has none, so clear it rather than keep an old one.
            s.effort = info.effort if info.effort_reported else None
            return self._snapshot_locked().content_key() != before

    # ------------------------------------------------------------ handlers --

    def _dispatch(self, s: Session, ev: HookEvent, now: float) -> bool:
        """Applies the event to the session. False means it was stale."""
        applied = self._handle(s, ev, now)
        if applied:
            self._sync_approval(s)
        return applied

    def _handle(self, s: Session, ev: HookEvent, now: float) -> bool:
        name = ev.name
        if name == "SessionStart":
            self._ended.pop(s.session_id, None)
            s.ended_at = None
            if ev.model:
                s.model_id, s.model_name = ev.model, None
            # Never reset a session that is working: "compact" fires mid-task
            # (auto-compaction), and SessionStart runs as an async command hook,
            # so it can arrive after the first prompt's events.
            if s.state in ("running", "approval"):
                if ev.source == "compact":
                    s.activity = _SESSION_START_LABELS["compact"]
                return True
            s.state = "idle"
            s.approvals.clear()
            s.activity = _SESSION_START_LABELS.get(ev.source or "startup", "Session started")
            return True

        if name == "UserPromptSubmit":
            s.turn += 1
            s.state = "running"
            s.activity = "Thinking..."
            s.task_started_at, s.task_ended_at = now, None
            s.approvals.clear()
            return True

        if name == "PreToolUse":
            if ev.tool_use_id and ev.tool_use_id in s.finished_tools:
                self.stats["stale"] += 1
                return False                    # overtaken by its own PostToolUse
            if ev.agent_id and s.state in DONE_STATES:
                return True                     # a background subagent; the turn stays finished
            self._ensure_running(s, now)
            s.activity = tool_activity(ev.tool_name, ev.tool_input)
            return True

        if name in ("PostToolUse", "PostToolUseFailure"):
            if ev.tool_use_id:
                _remember(s.finished_tools, ev.tool_use_id)
            if s.state in DONE_STATES:
                if not ev.agent_id:
                    self.stats["stale"] += 1
                return False                    # late result from a turn that already ended
            s.approvals.pop(ev.key, None)
            s.approvals.pop(_NOTIFICATION_KEY, None)
            if name == "PostToolUseFailure" and ev.is_interrupt and not ev.agent_id:
                self._finish(s, now, "turn_done", "Interrupted")
                return True
            self._ensure_running(s, now)
            if not s.approvals:
                s.state = "running"
            s.activity = "Tool failed" if name == "PostToolUseFailure" else "Thinking..."
            return True

        if name == "PermissionRequest":
            s.approvals.pop(_NOTIFICATION_KEY, None)    # the same prompt, now with details
            self._open_approval(s, now, ev.key, permission_action(ev.tool_name, ev.tool_input))
            return True

        if name == "PermissionDenied":
            s.approvals.pop(ev.key, None)
            if s.state not in DONE_STATES:
                self._ensure_running(s, now)
                if not s.approvals:
                    s.state = "running"
                s.activity = "Action denied"
            return True

        if name == "Notification":
            kind = ev.notification_type
            if kind == "permission_prompt":
                if not s.approvals:             # PermissionRequest may already have opened it
                    self._open_approval(s, now, _NOTIFICATION_KEY, "Permission request")
                return True
            if kind == "idle_prompt":
                # Claude has been waiting for your input for a while. If we still
                # think it is running, the turn ended without a Stop (Esc).
                if s.state == "running" and not s.approvals:
                    self._finish(s, now, "turn_done", "Waiting for you")
                return True
            if kind in ("elicitation_dialog", "elicitation_url_dialog", "agent_needs_input"):
                if s.state == "running":
                    s.activity = "Input needed"
                return True
            return True                         # other notifications change nothing

        if name == "Stop":
            if ev.background_running is False:
                self._finish(s, now, "task_done", "Task completed")
            elif ev.background_running:
                self._finish(s, now, "turn_done", "Background work")
            else:
                self._finish(s, now, "turn_done", "Turn finished")
            return True

        if name == "StopFailure":
            self._finish(s, now, "failed", failure_label(ev.error))
            return True

        if name == "SubagentStart":
            if s.state == "running":
                s.activity = "Running subagent"
            return True

        if name == "SubagentStop":
            if s.state == "running":
                s.activity = "Thinking..."
            return True

        if name == "SessionEnd":
            s.approvals.clear()
            s.state = "ended"
            s.activity = "Session ended"
            if s.task_started_at is not None and s.task_ended_at is None:
                s.task_ended_at = now
            s.ended_at = now
            _remember(self._ended, s.session_id, now, cap=512)
            return True

        if name == "PostModelSwitch":
            if ev.model:
                s.model_id, s.model_name = ev.model, None   # the status line refreshes the name
            return True

        return False

    @staticmethod
    def _sync_approval(s: Session) -> None:
        """An open prompt outranks whatever else the session is doing."""
        if s.approvals:
            s.state = "approval"
            s.activity = next(iter(s.approvals.values()))[1]
        elif s.state == "approval":
            s.state = "running"
            s.activity = "Thinking..."

    def _ensure_running(self, s: Session, now: float) -> None:
        """Hooks may be installed mid-task: start the task timer at first sight."""
        if s.state in ("idle",) + DONE_STATES:
            s.state = "running"
            if s.task_started_at is None or s.task_ended_at is not None:
                s.task_started_at, s.task_ended_at = now, None

    def _open_approval(self, s: Session, now: float, key: str, action: str) -> None:
        if s.task_started_at is None or s.task_ended_at is not None:
            s.task_started_at, s.task_ended_at = now, None
        if key not in s.approvals:
            s.approvals[key] = (now, action)

    def _finish(self, s: Session, now: float, state: str, activity: str) -> None:
        s.approvals.clear()
        s.state = state
        s.activity = activity
        if s.task_started_at is not None and s.task_ended_at is None:
            s.task_ended_at = now

    # ------------------------------------------------------------- helpers --

    def _session(self, session_id: str, now: float) -> Session:
        s = self._sessions.get(session_id)
        if s is None:
            s = Session(session_id=session_id, started_at=now, last_event_at=now)
            self._sessions[session_id] = s
        return s

    def _is_duplicate(self, ev: HookEvent, now: float) -> bool:
        while self._recent:
            fp, seen = next(iter(self._recent.items()))
            if now - seen <= self.dedupe_s and len(self._recent) <= 1024:
                break
            self._recent.popitem(last=False)
        if ev.fingerprint in self._recent:
            return True
        self._recent[ev.fingerprint] = now
        return False

    def _prune_locked(self, now: float) -> None:
        for sid, s in list(self._sessions.items()):
            if s.state == "ended" and s.ended_at is not None and now - s.ended_at > self.ended_linger_s:
                del self._sessions[sid]
            elif now - s.last_event_at > self.expiry_s:
                del self._sessions[sid]          # Claude Code exited without SessionEnd
        if self._primary not in self._sessions:
            self._primary = None

    def _pick_primary(self) -> Optional[Session]:
        sessions = list(self._sessions.values())
        if not sessions:
            return None
        waiting = [s for s in sessions if s.approvals]
        if waiting:
            return min(waiting, key=lambda s: (min(t for t, _ in s.approvals.values()), s.session_id))
        current = self._sessions.get(self._primary) if self._primary else None
        if current is not None and current.state == "running":
            return current
        running = [s for s in sessions if s.state == "running"]
        pool = running or sessions
        return max(pool, key=lambda s: (s.last_event_at, s.session_id))

    # --------------------------------------------------------------- output --

    def snapshot(self) -> Snapshot:
        with self._lock:
            self._prune_locked(self._clock())
            return self._snapshot_locked()

    def _snapshot_locked(self) -> Snapshot:
        now = self._clock()
        s = self._pick_primary()
        self._primary = s.session_id if s else None
        if s is None:
            return Snapshot(state="idle", sessions=0)
        return Snapshot(
            state=s.state,
            session_id=s.session_id[:8],
            sessions=len(self._sessions),
            event=s.event,
            model=short_model_name(s.model_id, s.model_name),
            effort=s.effort or "",
            activity=s.activity,
            permission_mode=normalize_permission(s.permission_mode),
            approval_required=bool(s.approvals) or s.state == "approval",
            elapsed_seconds=int(s.elapsed(now)),
            session_seconds=int(s.session_elapsed(now)),
        )

    def describe(self) -> Dict[str, Any]:
        """Debug view for ``GET /ovo/v1/status``. Contains no prompt text."""
        with self._lock:
            now = self._clock()
            return {
                "primary": self._primary,
                "sessions": [
                    {"session_id": s.session_id, "state": s.state, "activity": s.activity, "event": s.event,
                     "model": short_model_name(s.model_id, s.model_name), "effort": s.effort,
                     "permission_mode": s.permission_mode, "approvals": len(s.approvals),
                     "elapsed_seconds": int(s.elapsed(now)), "idle_seconds": int(now - s.last_event_at)}
                    for s in sorted(self._sessions.values(), key=lambda x: x.session_id)
                ],
                "stats": dict(self.stats),
            }


__all__ = ["Tracker", "Session", "DONE_STATES", "tool_key"]
