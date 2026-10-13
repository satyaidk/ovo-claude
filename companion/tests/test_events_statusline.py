"""Hook and status-line parsing: missing and malformed fields, no guessing."""
import pytest

from conftest import hook, statusline
from ovo_companion.events import HookPayloadError, parse_hook, tool_key
from ovo_companion.statusline import StatusLinePayloadError, parse_statusline, render_line, short_model_name


def test_common_fields_and_effort_object():
    ev = parse_hook(hook("PreToolUse", tool_name="Bash", tool_input={"command": "ls"}, tool_use_id="t1",
                         effort={"level": "xhigh"}, permission_mode="auto"))
    assert (ev.name, ev.session_id, ev.permission_mode, ev.effort) == ("PreToolUse", "sess-aaaa-1111", "auto", "xhigh")
    assert ev.tool_name == "Bash" and ev.tool_use_id == "t1" and ev.known


@pytest.mark.parametrize("effort", [None, {"level": "ultra"}, {"level": 3}, "high", {"lvl": "high"}])
def test_effort_is_never_guessed(effort):
    payload = hook("PreToolUse", model="claude-opus-5-5")
    if effort is not None:
        payload["effort"] = effort
    assert parse_hook(payload).effort is None


def test_model_only_from_session_start_and_model_switch():
    assert parse_hook(hook("SessionStart", source="startup", model="claude-opus-5-5")).model == "claude-opus-5-5"
    assert parse_hook(hook("SessionStart", source="clear")).model is None      # documented as optional
    assert parse_hook(hook("PostModelSwitch", from_model="a", to_model="claude-sonnet-5")).model == "claude-sonnet-5"
    assert parse_hook(hook("UserPromptSubmit", prompt="use claude-opus please")).model is None


def test_stop_background_work():
    assert parse_hook(hook("Stop")).background_running is None
    assert parse_hook(hook("Stop", background_tasks=[])).background_running is False
    assert parse_hook(hook("Stop", background_tasks=[{"id": "1", "status": "completed"}])).background_running is False
    assert parse_hook(hook("Stop", background_tasks=[{"id": "1", "status": "running"}])).background_running is True


def test_failure_and_interrupt_fields():
    assert parse_hook(hook("StopFailure", error="rate_limit")).error == "rate_limit"
    assert parse_hook(hook("PostToolUseFailure", is_interrupt=True)).is_interrupt is True
    assert parse_hook(hook("PostToolUseFailure", is_interrupt="yes")).is_interrupt is False
    assert parse_hook(hook("Notification", notification_type="permission_prompt")).notification_type == "permission_prompt"


@pytest.mark.parametrize("payload", [None, [], "Stop", {"hook_event_name": "Stop"}, {"session_id": "s"},
                                     {"session_id": "", "hook_event_name": "Stop"}])
def test_malformed_payloads_raise(payload):
    with pytest.raises(HookPayloadError):
        parse_hook(payload)


def test_unknown_event_is_parsed_but_marked_unknown():
    assert parse_hook(hook("SomeFutureEvent")).known is False


def test_prompt_text_is_not_kept():
    ev = parse_hook(hook("UserPromptSubmit", prompt="my secret plan"))
    assert "my secret plan" not in repr(ev)


def test_tool_key_links_permission_request_to_its_call():
    a = tool_key("Bash", {"command": "npm test", "description": "x"})
    b = tool_key("Bash", {"description": "x", "command": "npm test"})
    assert a == b and a != tool_key("Bash", {"command": "npm run build"})


def test_statusline_model_and_effort():
    info = parse_statusline(statusline(name="Opus", effort="max"))
    assert (info.model_name, info.model_id, info.effort, info.effort_reported) == ("Opus", "claude-opus-5-5", "max", True)


def test_statusline_missing_effort_means_unsupported():
    info = parse_statusline(statusline(effort=None))
    assert info.effort is None and info.effort_reported is False


def test_statusline_tolerates_odd_shapes():
    assert parse_statusline({"session_id": "s", "model": "claude-x"}).model_id == "claude-x"
    info = parse_statusline({"session_id": "s", "model": {"display_name": 5}, "effort": {"level": "ultra"}})
    assert info.model_name is None and info.effort is None


@pytest.mark.parametrize("payload", [None, [], {}, {"session_id": 3}])
def test_statusline_malformed(payload):
    with pytest.raises(StatusLinePayloadError):
        parse_statusline(payload)


@pytest.mark.parametrize("model_id, expected", [
    ("claude-opus-4-7", "Opus 4.7"),
    ("claude-sonnet-4-5-20250929", "Sonnet 4.5"),
    ("claude-opus-5-5[1m]", "Opus 5.5"),
    ("us.anthropic.claude-haiku-4-5-20251001-v1:0", "Haiku 4.5"),
    ("my-gateway-model", "my-gateway-model"),
    ("", ""),
    (None, ""),
])
def test_short_model_name_only_reformats(model_id, expected):
    assert short_model_name(model_id) == expected


def test_display_name_wins():
    assert short_model_name("claude-opus-4-7", "Opus") == "Opus"


def test_render_line():
    assert render_line("Opus", "high", "online") == "OVO ● | Opus | high"
    assert render_line("", None, "down") == "OVO ◌"
