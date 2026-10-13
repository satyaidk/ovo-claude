"""Installing and removing must never destroy unrelated configuration."""
import copy
import json

import pytest

from ovo_companion import installer
from ovo_companion.installer import (
    CORE_EVENTS, EXTENDED_EVENTS, InstallError, install, is_ovo_handler, is_ovo_status_line, uninstall,
)

V = (2, 1, 296)

USER_SETTINGS = {
    "model": "opus",
    "permissions": {"allow": ["Bash(npm test)"], "deny": ["Read(./.env)"], "defaultMode": "default"},
    "env": {"FOO": "bar"},
    "hooks": {
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "~/check.sh", "timeout": 5}]}],
        "Stop": [{"hooks": [{"type": "command", "command": "notify-send done"}]}],
    },
    "statusLine": {"type": "command", "command": "~/.claude/statusline.sh", "padding": 1},
}


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "proj"
    (p / ".claude").mkdir(parents=True)
    return p


def env(isolated_home, tmp_path):
    return {"home": isolated_home, "state": tmp_path / "state"}


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def ovo_handlers(settings):
    return [(event, h) for event, groups in settings.get("hooks", {}).items()
            for g in groups for h in g.get("hooks", []) if is_ovo_handler(h)]


def test_fresh_install(project, isolated_home, tmp_path):
    rep = install("local", project, version=V, **env(isolated_home, tmp_path))
    target = project / ".claude" / "settings.local.json"
    assert rep.settings_file == target.resolve() and rep.backup is None and rep.changed
    s = read(target)
    assert sorted(e for e, _ in ovo_handlers(s)) == sorted(CORE_EVENTS + EXTENDED_EVENTS)
    handlers = dict(ovo_handlers(s))
    assert handlers["Stop"] == {"type": "http", "url": "http://127.0.0.1:47823/ovo/v1/hook", "timeout": 2}
    # SessionStart does not accept http handlers (verified against Claude Code 2.1.296).
    assert handlers["SessionStart"]["type"] == "command" and handlers["SessionStart"]["async"] is True
    assert "hook --port 47823" in handlers["SessionStart"]["command"]
    assert is_ovo_status_line(s["statusLine"])
    assert "permissions" not in s
    assert rep.record_file.exists()


def test_merge_preserves_everything_else(project, isolated_home, tmp_path):
    target = project / ".claude" / "settings.local.json"
    target.write_text(json.dumps(USER_SETTINGS, indent=4), encoding="utf-8")
    original_text = target.read_text(encoding="utf-8")
    rep = install("local", project, version=V, **env(isolated_home, tmp_path))
    s = read(target)
    assert rep.backup and rep.backup.read_text(encoding="utf-8") == original_text
    for key in ("model", "permissions", "env"):
        assert s[key] == USER_SETTINGS[key]
    assert USER_SETTINGS["hooks"]["PreToolUse"][0] in s["hooks"]["PreToolUse"]
    assert USER_SETTINGS["hooks"]["Stop"][0] in s["hooks"]["Stop"]
    assert s["statusLine"]["padding"] == 1          # layout of the previous status line kept
    record = read(rep.record_file)
    assert record["original_status_line"] == USER_SETTINGS["statusLine"]
    assert record["chain_status_line"]["command"] == "~/.claude/statusline.sh"


def test_install_is_idempotent(project, isolated_home, tmp_path):
    target = project / ".claude" / "settings.local.json"
    target.write_text(json.dumps(USER_SETTINGS), encoding="utf-8")
    e = env(isolated_home, tmp_path)
    install("local", project, version=V, **e)
    first = read(target)
    rep = install("local", project, version=V, **e)
    assert read(target) == first and not rep.changed
    assert len(ovo_handlers(first)) == len(CORE_EVENTS + EXTENDED_EVENTS)
    assert read(rep.record_file)["original_status_line"] == USER_SETTINGS["statusLine"]


def test_uninstall_restores_the_original_exactly(project, isolated_home, tmp_path):
    target = project / ".claude" / "settings.local.json"
    target.write_text(json.dumps(USER_SETTINGS), encoding="utf-8")
    e = env(isolated_home, tmp_path)
    install("local", project, version=V, **e)
    install("local", project, version=V, **e)
    rep = uninstall("local", project, **e)
    assert read(target) == USER_SETTINGS
    assert not rep.record_file.exists()
    assert any("restored your previous status line" in n for n in rep.notes)


def test_uninstall_of_a_fresh_install_leaves_no_trace(project, isolated_home, tmp_path):
    target = project / ".claude" / "settings.local.json"
    target.write_text(json.dumps({"permissions": {"allow": []}}), encoding="utf-8")
    e = env(isolated_home, tmp_path)
    install("local", project, version=V, **e)
    uninstall("local", project, **e)
    assert read(target) == {"permissions": {"allow": []}}


def test_changes_made_after_install_survive_uninstall(project, isolated_home, tmp_path):
    target = project / ".claude" / "settings.local.json"
    e = env(isolated_home, tmp_path)
    install("local", project, version=V, **e)
    s = read(target)
    s["statusLine"] = {"type": "command", "command": "my-new-line"}
    s["hooks"]["Stop"][0]["hooks"].append({"type": "command", "command": "beep"})   # shares our group
    s["hooks"]["PostToolUse"].append({"matcher": "Write", "hooks": [{"type": "command", "command": "fmt"}]})
    s["theme"] = "dark"
    target.write_text(json.dumps(s), encoding="utf-8")
    rep = uninstall("local", project, **e)
    after = read(target)
    assert after["statusLine"] == {"type": "command", "command": "my-new-line"}
    assert after["hooks"]["Stop"] == [{"hooks": [{"type": "command", "command": "beep"}]}]
    assert after["hooks"]["PostToolUse"] == [{"matcher": "Write", "hooks": [{"type": "command", "command": "fmt"}]}]
    assert after["theme"] == "dark" and not ovo_handlers(after)
    assert any("left your status line alone" in n for n in rep.notes)


def test_invalid_settings_are_not_touched(project, isolated_home, tmp_path):
    target = project / ".claude" / "settings.local.json"
    target.write_text('{"hooks": {', encoding="utf-8")
    with pytest.raises(InstallError):
        install("local", project, version=V, **env(isolated_home, tmp_path))
    assert target.read_text(encoding="utf-8") == '{"hooks": {'
    target.write_text('{"hooks": []}', encoding="utf-8")
    with pytest.raises(InstallError):
        install("local", project, version=V, **env(isolated_home, tmp_path))
    target.write_text('{"hooks": {"Stop": {}}}', encoding="utf-8")
    with pytest.raises(InstallError):
        install("local", project, version=V, **env(isolated_home, tmp_path))


def test_unknown_or_old_version_uses_the_conservative_set(project, isolated_home, tmp_path):
    for version in (None, (2, 0, 30)):
        rep = install("local", project, version=version, **env(isolated_home, tmp_path))
        s = read(rep.settings_file)
        events = sorted(e for e, _ in ovo_handlers(s))
        assert events == sorted(CORE_EVENTS)
        assert all(h["type"] == "command" and h["async"] is True for _, h in ovo_handlers(s))


def test_inherited_status_line_is_chained_not_copied(project, isolated_home, tmp_path):
    shared = project / ".claude" / "settings.json"
    shared.write_text(json.dumps({"statusLine": {"type": "command", "command": "team-line"}}), encoding="utf-8")
    e = env(isolated_home, tmp_path)
    rep = install("local", project, version=V, **e)
    assert read(rep.record_file)["chain_status_line"]["command"] == "team-line"
    assert read(shared) == {"statusLine": {"type": "command", "command": "team-line"}}   # untouched
    uninstall("local", project, **e)
    assert not rep.settings_file.exists()       # created by the installer, removed again
    assert read(shared) == {"statusLine": {"type": "command", "command": "team-line"}}


def test_user_scope(project, isolated_home, tmp_path):
    rep = install("user", project, version=V, **env(isolated_home, tmp_path))
    assert rep.settings_file == isolated_home / ".claude" / "settings.json"


def test_dry_run_writes_nothing(project, isolated_home, tmp_path):
    rep = install("local", project, version=V, dry_run=True, **env(isolated_home, tmp_path))
    assert rep.changed and not rep.settings_file.exists() and not rep.record_file.exists()


def test_never_grants_permissions_or_changes_modes(project, isolated_home, tmp_path):
    target = project / ".claude" / "settings.local.json"
    target.write_text(json.dumps(USER_SETTINGS), encoding="utf-8")
    install("local", project, version=V, **env(isolated_home, tmp_path))
    s = read(target)
    assert s["permissions"] == USER_SETTINGS["permissions"]
    text = json.dumps(s)
    assert "bypassPermissions" not in text and "dangerously" not in text.lower()


def test_foreign_handlers_are_not_mistaken_for_ours():
    assert not is_ovo_handler({"type": "http", "url": "http://127.0.0.1:9000/hooks"})
    assert not is_ovo_handler({"type": "http", "url": "http://example.com/ovo/v1/hook"})
    assert not is_ovo_handler({"type": "command", "command": "python my_hook.py"})
    assert not is_ovo_handler({"type": "command", "command": "python ovo-companion.py statusline"})
    assert is_ovo_handler({"type": "command", "command": "python /x/ovo-companion.py hook --port 1"})
    assert is_ovo_handler({"type": "http", "url": "http://localhost:47823/ovo/v1/hook"})
    assert not is_ovo_handler("string") and not is_ovo_status_line(None)


def test_commands_use_forward_slashes_and_quote_spaces():
    cmd = installer.companion_command("hook", python="C:\\Program Files\\Python\\python.exe")
    assert cmd.startswith('"C:/Program Files/Python/python.exe" ')
    assert "\\" not in cmd


def test_plan_functions_are_pure():
    original = copy.deepcopy(USER_SETTINGS)
    new, record = installer.plan_install(USER_SETTINGS, transport="http", events=CORE_EVENTS, port=1,
                                         status_command="python ovo-companion.py statusline", previous=None,
                                         inherited_status_line=None)
    assert USER_SETTINGS == original and new != original
    back, _ = installer.plan_uninstall(new, record)
    assert back == original


def test_version_parsing():
    assert installer.parse_version("2.1.296 (Claude Code)") == (2, 1, 296)
    assert installer.parse_version("garbage") is None
    assert installer.choose((2, 1, 296)) == ("http", CORE_EVENTS + EXTENDED_EVENTS)
    assert installer.choose((2, 1, 250)) == ("command", CORE_EVENTS)
