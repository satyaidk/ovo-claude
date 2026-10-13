"""Device client against a fake device: success, rejection, timeouts, DNS."""
import time

from ovo_companion.device import Backoff, DeviceClient


def test_successful_send(device):
    c = DeviceClient("127.0.0.1", device.port, timeout_s=1.0)
    r = c.send(b'{"v":1,"seq":1,"cid":"c","state":"idle"}')
    assert r.ok and r.status == 200 and r.reply == "ok claude"
    assert device.received[0]["state"] == "idle"


def test_rejection_is_reported_not_retried_blindly(device):
    device.mode = "reject"
    r = DeviceClient("127.0.0.1", device.port).send(b"{}")
    assert not r.ok and r.status == 400 and "state: unknown value" in r.error


def test_old_firmware_without_the_endpoint(device):
    device.mode = "missing"
    r = DeviceClient("127.0.0.1", device.port).send(b"{}")
    assert not r.ok and "flash the Claude-mode firmware" in r.error


def test_timeout_is_bounded(device):
    device.mode, device.delay = "slow", 2.0
    c = DeviceClient("127.0.0.1", device.port, timeout_s=0.3)
    t0 = time.monotonic()
    r = c.send(b"{}")
    assert not r.ok and time.monotonic() - t0 < 1.5


def test_connection_refused(device):
    port = device.port
    device.close()
    r = DeviceClient("127.0.0.1", port, timeout_s=0.5).send(b"{}")
    assert not r.ok and r.error


def test_dropped_connection(device):
    device.mode = "drop"
    r = DeviceClient("127.0.0.1", device.port, timeout_s=0.5).send(b"{}")
    assert not r.ok


def test_address_is_cached_and_forgotten_after_failure(device):
    calls = []

    def resolver(host, port):
        calls.append(host)
        return "127.0.0.1"

    c = DeviceClient("mochi.local", device.port, resolver=resolver)
    assert c.send(b"{}").ok and c.send(b"{}").ok
    assert calls == ["mochi.local"]
    device.mode = "drop"
    c.send(b"{}")
    device.mode = "ok"
    assert c.send(b"{}").ok
    assert calls == ["mochi.local", "mochi.local"]


def test_unresolvable_host():
    def resolver(host, port):
        raise OSError("no such host")

    r = DeviceClient("nowhere.local", 80, resolver=resolver).send(b"{}")
    assert not r.ok and "cannot resolve nowhere.local" in r.error


def test_backoff_doubles_caps_and_resets():
    b = Backoff(first_s=0.5, max_s=4)
    assert [b.failure() for _ in range(6)] == [0.5, 1, 2, 4, 4, 4]
    b.success()
    assert b.failure() == 0.5
