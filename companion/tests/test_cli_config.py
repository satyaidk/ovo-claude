"""Command line and configuration."""
import json

import pytest

from ovo_companion import cli, config


def test_config_layers(tmp_path):
    f = tmp_path / "c.json"
    f.write_text(json.dumps({"device": "192.168.1.50", "heartbeat_s": 3}))
    cfg = config.load(f, env={"OVO_DEVICE_PORT": "8080"}, overrides={"listen_port": 5000, "device": None})
    assert (cfg.device, cfg.device_port, cfg.listen_port, cfg.heartbeat_s) == ("192.168.1.50", 8080, 5000, 3.0)


@pytest.mark.parametrize("data", [{"device": ""}, {"listen_port": 70000}, {"heartbeat_s": 0.1}, {"bogus": 1}])
def test_bad_config_is_rejected(tmp_path, data):
    f = tmp_path / "c.json"
    f.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        config.load(f, env={})


def test_cli_install_and_uninstall_round_trip(tmp_path, isolated_home, capsys):
    project = tmp_path / "p"
    project.mkdir()
    assert cli.main(["install", "--project", str(project), "--claude-version", "2.1.296"]) == 0
    out = capsys.readouterr().out
    assert "http hooks, 14 events" in out
    target = project / ".claude" / "settings.local.json"
    assert "statusLine" in json.loads(target.read_text())
    assert cli.main(["uninstall", "--project", str(project)]) == 0
    assert not target.exists()          # the installer created it, so it is removed again


def test_cli_reports_invalid_settings(tmp_path, isolated_home, capsys):
    project = tmp_path / "p"
    (project / ".claude").mkdir(parents=True)
    (project / ".claude" / "settings.local.json").write_text("{oops")
    assert cli.main(["install", "--project", str(project), "--claude-version", "2.1.296"]) == 2
    assert "not valid JSON" in capsys.readouterr().err


def test_send_test_reaches_the_device(device, capsys, isolated_home):
    assert cli.main(["send-test", "--device", "127.0.0.1", "--device-port", str(device.port),
                     "--state", "approval", "--activity", "Run: npm"]) == 0
    msg = device.received[-1]
    assert msg["state"] == "approval" and msg["approval_required"] is True and msg["activity"] == "Run: npm"


def test_status_without_daemon(capsys):
    assert cli.main(["status", "--port", "1"]) == 1
