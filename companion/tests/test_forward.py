"""The programs Claude Code runs must never fail or slow it down."""
import io
import json
import socket
import sys
import time

from conftest import statusline
from ovo_companion.forward import hook_main, statusline_main


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_hook_forwarder_is_silent_and_quick_without_a_daemon(capsys):
    t0 = time.monotonic()
    assert hook_main(free_port(), stdin=io.BytesIO(b'{"hook_event_name":"Stop"}')) == 0
    assert time.monotonic() - t0 < 2
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""


def test_statusline_without_daemon_still_prints():
    out = io.StringIO()
    statusline_main(free_port(), stdin=io.BytesIO(json.dumps(statusline(name="Opus", effort="high")).encode()),
                    stdout=out)
    assert out.getvalue() == "OVO ◌ | Opus | high\n"


def test_statusline_with_garbage_input():
    out = io.StringIO()
    statusline_main(free_port(), stdin=io.BytesIO(b"\x00not json"), stdout=out)
    assert out.getvalue() == "OVO ◌\n"


def test_statusline_chains_the_users_previous_command(tmp_path):
    py = sys.executable.replace("\\", "/")
    script = tmp_path / "mine.py"
    script.write_text("import sys, json\nd = json.load(sys.stdin)\nprint('mine:' + d['model']['display_name'])\n")
    record = tmp_path / "record.json"
    record.write_text(json.dumps({"chain_status_line": {"type": "command",
                                                        "command": f'"{py}" "{script.as_posix()}"'}}))
    out = io.StringIO()
    statusline_main(free_port(), str(record), stdin=io.BytesIO(json.dumps(statusline(name="Opus")).encode()),
                    stdout=out)
    assert out.getvalue() == "mine:Opus\n"


def test_missing_record_falls_back_to_own_line(tmp_path):
    out = io.StringIO()
    statusline_main(free_port(), str(tmp_path / "absent.json"),
                    stdin=io.BytesIO(json.dumps(statusline(name="Sonnet", effort=None)).encode()), stdout=out)
    assert out.getvalue() == "OVO ◌ | Sonnet\n"
