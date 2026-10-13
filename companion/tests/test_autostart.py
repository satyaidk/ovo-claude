"""Auto-start: the hooks bring the daemon up, once, and only one daemon runs."""
import io
import json
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ovo_companion import autostart, config
from ovo_companion.config import Config
from ovo_companion.daemon import Companion, port_in_use, run_forever
from ovo_companion.device import DeviceClient
from ovo_companion.forward import hook_main, statusline_main


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Spawns:
    def __init__(self, ok=True):
        self.calls, self.ok = [], ok

    def __call__(self, argv, cwd=None):
        self.calls.append(list(argv))
        return object() if self.ok else None


def test_already_running_means_no_spawn(tmp_path):
    sp = Spawns()
    assert autostart.ensure_running(1, spawner=sp, is_alive=lambda p: True, base=tmp_path, env={})
    assert sp.calls == []


def test_starts_a_daemon_with_port_and_log_file(tmp_path):
    sp = Spawns()
    assert autostart.ensure_running(47999, spawner=sp, is_alive=lambda p: False, base=tmp_path, env={}) is False
    argv = sp.calls[0]
    assert argv[-5:] == ["run", "--port", "47999", "--log-file", str(tmp_path / "companion.log")]


def test_waits_until_the_new_daemon_answers(tmp_path):
    state = {"up": False}
    sp = Spawns()

    def spawner(argv, cwd=None):
        sp(argv, cwd)
        threading.Timer(0.3, lambda: state.update(up=True)).start()
        return object()

    assert autostart.ensure_running(1, wait_s=2.0, spawner=spawner, is_alive=lambda p: state["up"],
                                    base=tmp_path, env={})


def test_attempts_are_spaced_out(tmp_path):
    sp, now = Spawns(), [1000.0]
    kw = dict(spawner=sp, is_alive=lambda p: False, base=tmp_path, env={}, clock=lambda: now[0])
    autostart.ensure_running(1, **kw)
    now[0] += 5
    autostart.ensure_running(1, **kw)          # a status-line refresh 5 s later
    assert len(sp.calls) == 1
    now[0] += autostart.START_GAP_S
    autostart.ensure_running(1, **kw)
    assert len(sp.calls) == 2


def test_can_be_switched_off(tmp_path):
    sp = Spawns()
    assert not autostart.ensure_running(1, spawner=sp, is_alive=lambda p: False, base=tmp_path,
                                        env={"OVO_NO_AUTOSTART": "1"})
    assert sp.calls == []


def test_refused_spawn_reports_false(tmp_path):
    assert not autostart.ensure_running(1, wait_s=1.0, spawner=Spawns(ok=False), is_alive=lambda p: False,
                                        base=tmp_path, env={})


def test_windowless_python_on_windows(tmp_path):
    argv = autostart.launch_argv(1, tmp_path)
    if sys.platform == "win32" and (autostart.pathlib.Path(sys.executable).with_name("pythonw.exe")).exists():
        assert argv[0].lower().endswith("pythonw.exe")


def test_hook_is_delivered_after_starting_the_daemon():
    """The first SessionStart finds no daemon: start it, then deliver the event."""
    port, got = free_port(), []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            got.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(204)
            self.end_headers()

    servers = []

    def ensure(p, wait_s=0.0):
        srv = ThreadingHTTPServer(("127.0.0.1", p), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return True

    payload = {"hook_event_name": "SessionStart", "session_id": "s", "source": "startup"}
    assert hook_main(port, stdin=io.BytesIO(json.dumps(payload).encode()), ensure=ensure) == 0
    for s in servers:
        s.shutdown()
        s.server_close()
    assert got == [payload]


def test_statusline_starts_the_daemon_without_waiting():
    calls = []
    out = io.StringIO()
    statusline_main(free_port(), stdin=io.BytesIO(b'{"session_id":"s"}'), stdout=out,
                    ensure=lambda p, wait_s: calls.append(wait_s) or False)
    assert calls == [0.0] and out.getvalue().startswith("OVO")


def test_only_one_daemon_per_port(device):
    cfg = Config(device="127.0.0.1", device_port=device.port, listen_port=free_port(), heartbeat_s=5, timeout_s=0.5)
    first = Companion(cfg, client=DeviceClient("127.0.0.1", device.port))
    first.start()
    try:
        second = Companion(cfg, client=DeviceClient("127.0.0.1", device.port))
        with pytest.raises(OSError) as exc:
            second.start()
        assert port_in_use(exc.value)
        assert run_forever(cfg) == 0          # a second copy exits quietly
    finally:
        first.stop(goodbye=False)


def test_install_device_is_saved(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"heartbeat_s": 3}))
    config.save({"device": "192.168.1.62", "device_port": None}, path)
    assert json.loads(path.read_text()) == {"heartbeat_s": 3, "device": "192.168.1.62"}
    with pytest.raises(ValueError):
        config.save({"device": ""}, path)
    assert json.loads(path.read_text())["device"] == "192.168.1.62"


def test_real_background_start(tmp_path, monkeypatch, isolated_home):
    """Spawns a real detached daemon and checks it answers, then stops it.

    The child inherits the isolated config directory, whose config points the
    daemon at an unused local port, so it never contacts a real device.
    """
    monkeypatch.delenv("OVO_NO_AUTOSTART", raising=False)
    config.save({"device": "127.0.0.1", "device_port": free_port()})
    port, procs = free_port(), []

    def spawner(argv, cwd=None):
        p = autostart.spawn(argv, cwd)
        procs.append(p)
        return p

    try:
        assert autostart.ensure_running(port, wait_s=15.0, spawner=spawner, base=tmp_path, env={})
        assert autostart.alive(port)
    finally:
        for p in procs:
            if p is not None:
                p.kill()
                p.wait(timeout=10)
    deadline = time.monotonic() + 5
    while autostart.alive(port) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not autostart.alive(port)
