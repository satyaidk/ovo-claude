"""Session tracking with simulated Claude Code event streams."""
import json

from conftest import hook, permission, post, pre, statusline
from ovo_companion.tracker import Tracker

BASH_TEST = {"command": "pytest -q", "description": "Run tests"}
BASH_RM = {"command": "rm -rf build", "description": "Clean"}


def make(clock, **kw):
    return Tracker(clock, **kw)


def feed(t, *payloads):
    for p in payloads:
        t.handle_hook(p)
    return t.snapshot()


# ------------------------------------------------------------ lifecycle --

def test_full_turn(clock):
    t = make(clock)
    s = feed(t, hook("SessionStart", source="startup", model="claude-opus-5-5"))
    assert (s.state, s.activity, s.model) == ("idle", "Starting session", "Opus 5.5")
    s = feed(t, hook("UserPromptSubmit", prompt="fix the tests"))
    assert (s.state, s.activity) == ("running", "Thinking...")
    s = feed(t, pre("Bash", BASH_TEST, "t1"))
    assert (s.state, s.activity, s.effort) == ("running", "Running tests", "high")
    clock.advance(12)
    s = feed(t, post("Bash", BASH_TEST, "t1"))
    assert (s.state, s.activity, s.elapsed_seconds) == ("running", "Thinking...", 12)
    s = feed(t, hook("Stop", stop_hook_active=False, background_tasks=[], session_crons=[]))
    assert (s.state, s.activity) == ("task_done", "Task completed")


def test_turn_done_is_not_task_done(clock):
    t = make(clock)
    feed(t, hook("UserPromptSubmit"))
    s = feed(t, hook("Stop", background_tasks=[{"id": "b1", "type": "shell", "status": "running"}]))
    assert (s.state, s.activity) == ("turn_done", "Background work")
    feed(t, hook("UserPromptSubmit"))
    s = feed(t, hook("Stop"))           # older Claude Code: no background_tasks field
    assert (s.state, s.activity) == ("turn_done", "Turn finished")


def test_failure_and_session_end_are_distinct(clock):
    t = make(clock)
    feed(t, hook("UserPromptSubmit"))
    s = feed(t, hook("StopFailure", error="rate_limit", error_details="429"))
    assert (s.state, s.activity) == ("failed", "Rate limited")
    s = feed(t, hook("SessionEnd", reason="prompt_input_exit"))
    assert (s.state, s.activity) == ("ended", "Session ended")
    clock.advance(61)                   # ended_linger_s = 60
    s = t.snapshot()
    assert (s.state, s.sessions) == ("idle", 0)


def test_interrupt_and_idle_prompt_end_a_turn_without_stop(clock):
    t = make(clock)
    feed(t, hook("UserPromptSubmit"), pre("Bash", BASH_TEST, "t1"))
    s = feed(t, hook("PostToolUseFailure", tool_name="Bash", tool_input=BASH_TEST, tool_use_id="t1",
                     error="interrupted", is_interrupt=True))
    assert (s.state, s.activity) == ("turn_done", "Interrupted")
    feed(t, hook("UserPromptSubmit"))   # Esc while thinking: no hook at all, then...
    s = feed(t, hook("Notification", message="Claude is waiting for your input", notification_type="idle_prompt"))
    assert (s.state, s.activity) == ("turn_done", "Waiting for you")


def test_tool_failure_keeps_running(clock):
    t = make(clock)
    feed(t, hook("UserPromptSubmit"), pre("Bash", BASH_TEST, "t1"))
    s = feed(t, hook("PostToolUseFailure", tool_name="Bash", tool_input=BASH_TEST, tool_use_id="t1",
                     error="Exit code 1", is_interrupt=False))
    assert (s.state, s.activity) == ("running", "Tool failed")


# ------------------------------------------------------------ approvals --

def test_permission_request_and_resolution(clock):
    t = make(clock)
    feed(t, hook("UserPromptSubmit"), pre("Bash", BASH_RM, "t1"))
    s = feed(t, permission("Bash", BASH_RM))
    assert (s.state, s.approval_required, s.activity) == ("approval", True, "Run: rm")
    s = feed(t, hook("Notification", message="Claude needs your permission to use Bash",
                     notification_type="permission_prompt"))
    assert (s.state, s.activity) == ("approval", "Run: rm")     # same prompt, merged
    s = feed(t, post("Bash", BASH_RM, "t1"))
    assert (s.state, s.approval_required) == ("running", False)


def test_notification_first_then_permission_request_does_not_stick(clock):
    t = make(clock)
    feed(t, hook("UserPromptSubmit"))
    s = feed(t, hook("Notification", notification_type="permission_prompt", message="needs permission"))
    assert s.state == "approval" and s.activity == "Permission request"
    s = feed(t, permission("Bash", BASH_RM))
    assert s.activity == "Run: rm"
    s = feed(t, post("Bash", BASH_RM, "t9"))
    assert s.state == "running" and not s.approval_required


def test_parallel_tools_do_not_hide_an_open_prompt(clock):
    t = make(clock)
    feed(t, hook("UserPromptSubmit"), pre("Bash", BASH_RM, "t1"), permission("Bash", BASH_RM))
    s = feed(t, pre("Read", {"file_path": "/a/b.py"}, "t2"))
    assert (s.state, s.activity) == ("approval", "Run: rm")
    s = feed(t, post("Read", {"file_path": "/a/b.py"}, "t2"))
    assert (s.state, s.activity) == ("approval", "Run: rm")
    s = feed(t, post("Bash", BASH_RM, "t1"))
    assert s.state == "running"


def test_ordinary_notifications_are_not_permission_requests(clock):
    t = make(clock)
    feed(t, hook("UserPromptSubmit"))
    for kind in ("auth_success", "elicitation_dialog", "agent_completed", "something_new"):
        s = feed(t, hook("Notification", notification_type=kind, message=kind))
        assert s.state == "running" and not s.approval_required, kind
    assert t.snapshot().activity == "Input needed"


def test_denied_prompt_clears_on_next_turn(clock):
    t = make(clock)
    feed(t, hook("UserPromptSubmit"), permission("Bash", BASH_RM))
    s = feed(t, hook("UserPromptSubmit", prompt="no, do it differently"))
    assert (s.state, s.approval_required) == ("running", False)


def test_auto_mode_denial(clock):
    t = make(clock)
    feed(t, hook("UserPromptSubmit", permission_mode="auto"))
    s = feed(t, hook("PermissionDenied", permission_mode="auto", tool_name="Bash", tool_input=BASH_RM,
                     tool_use_id="t1", reason="[Irreversible Local Destruction]"))
    assert (s.state, s.activity, s.permission_mode) == ("running", "Action denied", "auto")


# ------------------------------------------------------- time and order --

def test_elapsed_uses_the_clock_not_message_arrival(clock):
    t = make(clock)
    feed(t, hook("SessionStart", source="startup"))
    clock.advance(30)
    feed(t, hook("UserPromptSubmit"))
    clock.advance(95)
    s = t.snapshot()                    # no event in between
    assert (s.elapsed_seconds, s.session_seconds) == (95, 125)
    feed(t, hook("Stop", background_tasks=[]))
    clock.advance(500)
    s = t.snapshot()
    assert s.elapsed_seconds == 95      # frozen at the end of the task
    assert s.session_seconds == 625     # the session is still open
    feed(t, hook("UserPromptSubmit", prompt="next"))
    clock.advance(3)
    assert t.snapshot().elapsed_seconds == 3


def test_duplicates_are_dropped(clock):
    t = make(clock)
    p = permission("Bash", BASH_RM)
    feed(t, hook("UserPromptSubmit"))
    assert t.handle_hook(p) is True
    assert t.handle_hook(p) is False
    assert t.stats["duplicates"] == 1
    clock.advance(11)                   # outside the 10 s window it counts again
    t.handle_hook(p)
    assert t.stats["duplicates"] == 1


def test_out_of_order_tool_events(clock):
    t = make(clock)
    feed(t, hook("UserPromptSubmit"))
    feed(t, post("Read", {"file_path": "a"}, "t1"))
    s = feed(t, pre("Read", {"file_path": "a"}, "t1"))     # overtaken: stale
    assert s.activity == "Thinking..."
    assert t.stats["stale"] == 1


def test_late_events_do_not_reopen_a_finished_turn(clock):
    t = make(clock)
    feed(t, hook("UserPromptSubmit"), pre("Bash", BASH_TEST, "t1"), hook("Stop", background_tasks=[]))
    s = feed(t, post("Bash", BASH_TEST, "t1"))
    assert s.state == "task_done"
    s = feed(t, pre("Read", {"file_path": "x"}, "t5", agent_id="agent-1", agent_type="Explore"))
    assert s.state == "task_done"       # background subagent work


def test_events_after_session_end_are_ignored_until_restart(clock):
    t = make(clock)
    feed(t, hook("UserPromptSubmit"), hook("SessionEnd", reason="other"))
    s = feed(t, pre("Bash", BASH_TEST, "late"))
    assert s.state == "ended"
    s = feed(t, hook("SessionStart", source="resume"))
    assert s.state == "idle"


def test_compaction_does_not_stop_a_running_task(clock):
    t = make(clock)
    feed(t, hook("UserPromptSubmit"))
    s = feed(t, hook("SessionStart", source="compact"))
    assert (s.state, s.activity) == ("running", "Context compacted")


def test_late_session_start_does_not_reset_a_running_session(clock):
    # SessionStart runs as an async command hook and can lose the race.
    t = make(clock)
    feed(t, hook("UserPromptSubmit"), pre("Bash", BASH_TEST, "t1"))
    s = feed(t, hook("SessionStart", source="startup", model="claude-opus-5-5"))
    assert (s.state, s.activity, s.model) == ("running", "Running tests", "Opus 5.5")


def test_silent_sessions_expire(clock):
    t = make(clock, expiry_s=600)
    feed(t, hook("UserPromptSubmit"))
    clock.advance(601)
    assert t.snapshot().sessions == 0


def test_malformed_and_unknown_events_change_nothing(clock):
    t = make(clock)
    before = t.snapshot()
    assert t.handle_hook({"nope": 1}) is False
    assert t.handle_hook("text") is False
    assert t.handle_hook(hook("BrandNewEvent")) is False
    assert t.snapshot() == before
    assert t.stats["invalid"] == 2 and t.stats["ignored"] == 1


# --------------------------------------------------- model, effort, mode --

def test_status_line_supplies_name_and_effort(clock):
    t = make(clock)
    feed(t, hook("SessionStart", source="startup", model="claude-opus-5-5"))
    t.handle_statusline(statusline(name="Opus", effort="xhigh"))
    s = t.snapshot()
    assert (s.model, s.effort) == ("Opus", "xhigh")
    t.handle_statusline(statusline(name="Haiku", model_id="claude-haiku-5-5", effort=None))
    s = t.snapshot()
    assert (s.model, s.effort) == ("Haiku", "")       # no effort setting: shown as none, not guessed


def test_effort_survives_events_without_it_and_follows_changes(clock):
    t = make(clock)
    feed(t, hook("UserPromptSubmit"), pre("Bash", BASH_TEST, "t1"))
    assert t.snapshot().effort == "high"
    feed(t, hook("Notification", notification_type="idle_prompt"))   # no effort field
    assert t.snapshot().effort == "high"
    feed(t, hook("PreToolUse", tool_name="Read", tool_input={}, tool_use_id="t2", effort={"level": "max"}))
    assert t.snapshot().effort == "max"


def test_model_switch_mid_session(clock):
    t = make(clock)
    feed(t, hook("SessionStart", source="startup", model="claude-opus-5-5"))
    t.handle_statusline(statusline(name="Opus"))
    s = feed(t, hook("PostModelSwitch", from_model="claude-opus-5-5", to_model="claude-sonnet-5-5", source="command"))
    assert s.model == "Sonnet 5.5"
    t.handle_statusline(statusline(name="Sonnet", model_id="claude-sonnet-5-5"))
    assert t.snapshot().model == "Sonnet"


def test_permission_mode_tracks_the_session(clock):
    t = make(clock)
    feed(t, hook("UserPromptSubmit", permission_mode="default"))
    assert t.snapshot().permission_mode == "default"
    feed(t, pre("Read", {}, "t1", permission_mode="plan"))
    assert t.snapshot().permission_mode == "plan"
    feed(t, pre("Read", {}, "t2", permission_mode="someFutureMode"))
    assert t.snapshot().permission_mode == "other"


def test_no_pending_prompt_does_not_mean_auto(clock):
    t = make(clock)
    s = feed(t, hook("UserPromptSubmit", permission_mode="default"), pre("Read", {}, "t1"),
             post("Read", {}, "t1"))
    assert s.permission_mode == "default"


# ------------------------------------------------------- many sessions --

def test_primary_session_is_deterministic(clock):
    t = make(clock)
    feed(t, hook("UserPromptSubmit", session="A"))
    clock.advance(1)
    feed(t, hook("UserPromptSubmit", session="B"))
    s = t.snapshot()
    assert (s.session_id, s.sessions) == ("A", 2)       # A was primary and is still running
    clock.advance(1)
    feed(t, hook("Stop", session="A", background_tasks=[]))
    assert t.snapshot().session_id == "B"               # the running one
    clock.advance(1)
    feed(t, permission("Bash", BASH_RM, session="A"))
    assert t.snapshot().session_id == "A"               # approval wins
    clock.advance(1)
    feed(t, permission("Bash", BASH_RM, session="B"))
    assert t.snapshot().session_id == "A"               # it has waited longer
    feed(t, post("Bash", BASH_RM, "x", session="A"))
    assert t.snapshot().session_id == "B"


def test_tie_breaks_on_session_id(clock):
    t = make(clock)
    feed(t, hook("SessionStart", session="b", source="startup"), hook("SessionStart", session="a", source="startup"))
    assert t.snapshot().session_id == "b"   # same time: highest id wins, every time
    t2 = make(clock)
    feed(t2, hook("SessionStart", session="a", source="startup"), hook("SessionStart", session="b", source="startup"))
    assert t2.snapshot().session_id == "b"


def test_nothing_private_reaches_the_snapshot(clock):
    t = make(clock)
    secret = "PROMPT-SECRET-123"
    feed(t, hook("UserPromptSubmit", prompt=secret),
         pre("Bash", {"command": f"echo {secret}"}, "t1"),
         hook("Stop", last_assistant_message=secret, background_tasks=[]))
    dumped = json.dumps(t.snapshot().__dict__) + json.dumps(t.describe())
    assert secret not in dumped
