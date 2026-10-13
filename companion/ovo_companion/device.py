"""HTTP client for the device's ``POST /api/claude/status``.

Design points:

* Every request has a timeout, so a device that is off or asleep costs at
  most ``timeout_s`` per attempt and never hangs the sender thread.
* ``mochi.local`` is resolved once and the address cached; the cache is
  dropped on any failure, so a DHCP change is picked up on the next try.
  (mDNS lookups on some systems take a second or more; doing one per
  heartbeat would make the heartbeat late.)
* :class:`Backoff` spaces retries 0.5 s, 1 s, 2 s ... up to ``max_s`` and
  resets after a success. The daemon always sends the *latest* state, so
  nothing queues up while the device is unreachable.
"""
from __future__ import annotations

import http.client
import json
import socket
import time
from dataclasses import dataclass
from typing import Callable, Optional, Tuple


@dataclass
class SendResult:
    ok: bool
    status: int = 0
    reply: str = ""
    error: str = ""


class Backoff:
    def __init__(self, first_s: float = 0.5, max_s: float = 30.0, factor: float = 2.0):
        self.first_s, self.max_s, self.factor = first_s, max_s, factor
        self._next = first_s
        self.failures = 0

    def failure(self) -> float:
        """Records a failure and returns how long to wait before the next try."""
        wait = min(self._next, self.max_s)
        self._next = min(self.max_s, self._next * self.factor)
        self.failures += 1
        return wait

    def success(self) -> None:
        self._next = self.first_s
        self.failures = 0


Resolver = Callable[[str, int], str]


def default_resolver(host: str, port: int) -> str:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    # Prefer IPv4: the device's web server listens on IPv4.
    infos.sort(key=lambda i: 0 if i[0] == socket.AF_INET else 1)
    return infos[0][4][0]


class DeviceClient:
    PATH = "/api/claude/status"

    def __init__(self, host: str, port: int = 80, timeout_s: float = 2.0,
                 resolver: Resolver = default_resolver, cache_s: float = 300.0,
                 clock: Callable[[], float] = time.monotonic):
        self.host, self.port, self.timeout_s = host, port, timeout_s
        self._resolver, self._cache_s, self._clock = resolver, cache_s, clock
        self._addr: Optional[Tuple[str, float]] = None

    def _address(self) -> str:
        now = self._clock()
        if self._addr and now - self._addr[1] < self._cache_s:
            return self._addr[0]
        addr = self._resolver(self.host, self.port)
        self._addr = (addr, now)
        return addr

    def forget_address(self) -> None:
        self._addr = None

    def send(self, body: bytes) -> SendResult:
        try:
            addr = self._address()
        except OSError as exc:
            return SendResult(False, error=f"cannot resolve {self.host}: {exc}")
        conn = http.client.HTTPConnection(addr, self.port, timeout=self.timeout_s)
        try:
            conn.request("POST", self.PATH, body=body, headers={
                "Host": self.host,
                "Content-Type": "application/json",
                "Connection": "close",
            })
            resp = conn.getresponse()
            raw = resp.read(4096).decode("utf-8", "replace")
        except (OSError, http.client.HTTPException) as exc:
            self.forget_address()
            return SendResult(False, error=f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__)
        finally:
            conn.close()
        try:
            reply = json.loads(raw)
        except ValueError:
            reply = {}
        text = reply.get("reply") or reply.get("error") or raw[:120]
        if resp.status == 200 and reply.get("ok") is True:
            return SendResult(True, resp.status, str(text))
        if resp.status == 404:
            text = "the firmware has no /api/claude/status (flash the Claude-mode firmware)"
        return SendResult(False, resp.status, error=f"HTTP {resp.status}: {text}")
