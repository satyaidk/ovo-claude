"""The daemon end to end: Claude Code events in, device messages out."""
import json
import time
import urllib.error
import urllib.request

import pytest

from conftest import hook, permission, pre, statusline
from ovo_companion.config import Config
from ovo_companion.daemon import Companion
from ovo_companion.device import DeviceClient


def post_json(port, path, payload, headers=None):
    data = json.dumps(payload).encode() if not isinstance(payload, bytes) else payload
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method="POST",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


@pytest.fixture
def companion(device):
    cfg = Config(device="127.0.0.1", device_port=device.port, listen_port=0, heartbeat_s=0.3,
                 timeout_s=0.5, retry_max_s=0.4)
    c = Companion(cfg, client=DeviceClient("127.0.0.1", device.port, timeout_s=0.5))
    c.port = c.start()
    yield c
    c.stop(goodbye=False)


def test_hooks_answer_no_content_so_they_never_decide_anything(companion):
    status, body = post_json(companion.port, "/ovo/v1/hook", permission("Bash", {"command": "rm -rf x"}))
    assert status == 204 and body == b""


def test_events_reach_the_device(companion, device):
    post_json(companion.port, "/ovo/v1/hook", hook("SessionStart", source="startup", model="claude-opus-5-5"))
    post_json(companion.port, "/ovo/v1/statusline", statusline(name="Opus", effort="xhigh"))
    post_json(companion.port, "/ovo/v1/hook", hook("UserPromptSubmit"))
    post_json(companion.port, "/ovo/v1/hook", pre("Bash", {"command": "pytest"}, "t1"))   # reports effort high
    msg = device.wait_for(lambda m: m["activity"] == "Running tests")
    assert msg is not None
    # The newest report wins: the PreToolUse payload said "high" after the status line said "xhigh".
    assert (msg["state"], msg["model"], msg["effort"], msg["permission_mode"]) == ("running", "Opus", "high", "default")
    assert msg["v"] == 1 and msg["cid"] == companion.cid and msg["connected"] is True

    post_json(companion.port, "/ovo/v1/hook", permission("Bash", {"command": "rm -rf build"}))
    msg = device.wait_for(lambda m: m["state"] == "approval")
    assert msg["approval_required"] is True and msg["activity"] == "Run: rm"


def test_heartbeat_repeats_state_with_rising_seq(companion, device):
    assert device.wait_for(lambda m: m["seq"] >= 3, timeout=5) is not None
    seqs = [m["seq"] for m in device.received]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


def test_reconnects_after_the_device_comes_back(companion, device):
    device.wait_for(lambda m: True)
    device.mode = "drop"
    deadline = time.monotonic() + 5
    while companion.link != "offline" and time.monotonic() < deadline:
        time.sleep(0.05)
    assert companion.link == "offline"
    device.mode = "ok"
    deadline = time.monotonic() + 5
    while companion.link != "online" and time.monotonic() < deadline:
        time.sleep(0.05)
    assert companion.link == "online"


def test_statusline_reply_reports_the_link(companion, device):
    device.wait_for(lambda m: True)
    status, body = post_json(companion.port, "/ovo/v1/statusline", statusline())
    assert status == 200 and json.loads(body)["link"] == "online"


def test_browser_requests_are_refused(companion):
    status, _ = post_json(companion.port, "/ovo/v1/hook", hook("Stop"), headers={"Origin": "http://evil.example"})
    assert status == 403
    req = urllib.request.Request(f"http://127.0.0.1:{companion.port}/ovo/v1/hook", data=b"{}", method="POST",
                                 headers={"Content-Type": "text/plain"})
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=3)
    assert exc.value.code == 403


def test_malformed_input_is_absorbed(companion):
    for body in (b"not json", b"[]", b'{"hook_event_name": "Stop"}'):
        status, _ = post_json(companion.port, "/ovo/v1/hook", body)
        assert status == 204
    assert companion.tracker.stats["invalid"] == 3
    status, _ = post_json(companion.port, "/elsewhere", {})
    assert status == 404


def test_status_endpoint(companion):
    with urllib.request.urlopen(f"http://127.0.0.1:{companion.port}/ovo/v1/status", timeout=3) as resp:
        data = json.loads(resp.read())
    assert {"device", "link", "tracker", "sent_ok"} <= set(data)


def test_goodbye_marks_the_device_disconnected(device):
    cfg = Config(device="127.0.0.1", device_port=device.port, listen_port=0, heartbeat_s=5, timeout_s=0.5)
    c = Companion(cfg, client=DeviceClient("127.0.0.1", device.port, timeout_s=0.5))
    c.start()
    device.wait_for(lambda m: True)
    c.stop(goodbye=True)
    assert device.received[-1]["connected"] is False
