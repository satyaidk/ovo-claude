"""The companion daemon: receive Claude Code events locally, push status to OVO.

Two threads do the work:

* the **receiver** (``ThreadingHTTPServer`` on 127.0.0.1) accepts hook
  payloads at ``/ovo/v1/hook`` and status-line payloads at
  ``/ovo/v1/statusline``. It updates the tracker and answers ``204 No
  Content`` straight away. An empty 2xx answer is how a Claude Code HTTP hook
  says "no decision", so the companion can never approve, deny or otherwise
  steer a permission request;
* the **sender** wakes when the displayed state changes, or every
  ``heartbeat_s`` seconds, and POSTs the current snapshot to the device. A
  failed send is retried with exponential backoff; only the newest state is
  ever sent, so nothing piles up while the device is unreachable.

Browser protection: the receiver refuses requests that carry an ``Origin``
header or a form/plain-text content type, which are what a web page can send
to 127.0.0.1 without a CORS preflight. Claude Code sends neither.
"""
from __future__ import annotations

import json
import logging
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional

from .config import LISTEN_HOST, Config
from .device import Backoff, DeviceClient, SendResult
from .protocol import Snapshot, build_payload, encode
from .tracker import Tracker

log = logging.getLogger("ovo")

HOOK_PATH = "/ovo/v1/hook"
STATUSLINE_PATH = "/ovo/v1/statusline"
STATUS_PATH = "/ovo/v1/status"
HEALTH_PATH = "/ovo/v1/health"
MAX_HOOK_BODY = 16 * 1024 * 1024     # Write tool input can carry a whole file
_BROWSER_TYPES = ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data")


class _QuietServer(ThreadingHTTPServer):
    """Claude Code closes idle keep-alive connections abruptly; that is normal."""

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            log.debug("connection from %s closed by the client", client_address)
            return
        super().handle_error(request, client_address)


class Companion:
    def __init__(self, config: Config, tracker: Optional[Tracker] = None,
                 client: Optional[DeviceClient] = None, clock: Callable[[], float] = time.monotonic):
        self.config = config
        self.clock = clock
        self.tracker = tracker or Tracker(clock, expiry_s=config.session_expiry_s,
                                          ended_linger_s=config.ended_linger_s)
        self.client = client or DeviceClient(config.device, config.device_port, config.timeout_s)
        self.cid = secrets.token_hex(4)
        self.seq = 0
        self.link = "unknown"            # unknown | online | offline
        self.last_error = ""
        self.last_sent: Optional[dict] = None
        self.sent_ok = 0
        self.sent_failed = 0
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._server: Optional[ThreadingHTTPServer] = None
        self._threads: list = []
        self._lock = threading.Lock()
        self._sent_key: tuple = ()

    # ------------------------------------------------------------- lifecycle --

    def start(self) -> int:
        """Starts both threads. Returns the port actually bound (0 picks one)."""
        handler = _make_handler(self)
        self._server = _QuietServer((LISTEN_HOST, self.config.listen_port), handler)
        self._server.daemon_threads = True
        port = self._server.server_address[1]
        for target, name in ((self._server.serve_forever, "ovo-receiver"), (self._sender_loop, "ovo-sender")):
            t = threading.Thread(target=target, name=name, daemon=True)
            t.start()
            self._threads.append(t)
        log.info("listening on http://%s:%d, device %s:%d", LISTEN_HOST, port,
                 self.config.device, self.config.device_port)
        return port

    def stop(self, goodbye: bool = True) -> None:
        self._stop.set()
        self._wake.set()
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        for t in self._threads:
            t.join(timeout=self.config.timeout_s + 1)
        if goodbye:
            # Tell the device we are going away, so it shows DISCONNECTED now
            # instead of RUNNING until the stale timeout.
            self.send_once(connected=False)

    def notify(self) -> None:
        self._wake.set()

    # ------------------------------------------------------------- receiving --

    def on_hook(self, payload: object) -> None:
        if self.tracker.handle_hook(payload):
            self.notify()

    def on_statusline(self, payload: object) -> None:
        if self.tracker.handle_statusline(payload):
            self.notify()

    # --------------------------------------------------------------- sending --

    def _next_payload(self, connected: bool = True) -> dict:
        with self._lock:
            self.seq += 1
            snap = self.tracker.snapshot() if connected else Snapshot(state="idle")
            self._sent_key = snap.content_key()
            return build_payload(snap, self.cid, self.seq, connected)

    def send_once(self, connected: bool = True) -> SendResult:
        payload = self._next_payload(connected)
        try:
            body = encode(payload)
        except ValueError as exc:            # cannot happen with the limits in protocol.py
            return SendResult(False, error=str(exc))
        result = self.client.send(body)
        self._record(result, payload)
        return result

    def _record(self, result: SendResult, payload: dict) -> None:
        if result.ok:
            self.sent_ok += 1
            self.last_sent = payload
            if self.link != "online":
                log.info("device online (%s)", self.config.device)
            self.link = "online"
            self.last_error = ""
        else:
            self.sent_failed += 1
            if self.link != "offline" or result.error != self.last_error:
                log.warning("device unreachable: %s", result.error)
            self.link = "offline"
            self.last_error = result.error

    def _sender_loop(self) -> None:
        backoff = Backoff(max_s=self.config.retry_max_s)
        while not self._stop.is_set():
            result = self.send_once()
            if self._stop.is_set():
                break
            if result.ok:
                backoff.success()
                wait = self.config.heartbeat_s
            else:
                wait = backoff.failure()
            last_key = self._sent_key     # what the device now shows
            # Sleep until the heartbeat is due or the state changes.
            deadline = self.clock() + wait
            while not self._stop.is_set():
                remaining = deadline - self.clock()
                if remaining <= 0:
                    break
                self._wake.wait(min(remaining, 1.0))
                if self._wake.is_set():
                    self._wake.clear()
                    if result.ok and self.tracker.snapshot().content_key() != last_key:
                        break               # a change: send now
                    # While the device is unreachable, changes wait for the
                    # backoff; the newest state goes out on the next try.

    def describe(self) -> dict:
        return {
            "device": f"{self.config.device}:{self.config.device_port}",
            "link": self.link,
            "last_error": self.last_error,
            "sent_ok": self.sent_ok,
            "sent_failed": self.sent_failed,
            "last_sent": self.last_sent,
            "tracker": self.tracker.describe(),
        }


def _make_handler(companion: Companion):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ovo-companion"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):   # requests are logged at debug level only
            log.debug("%s - %s", self.address_string(), fmt % args)

        def _reply(self, code: int, obj: Optional[dict] = None) -> None:
            body = b"" if obj is None else json.dumps(obj).encode()
            self.send_response(code)
            if obj is not None:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _from_browser(self) -> bool:
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            return bool(self.headers.get("Origin")) or ctype in _BROWSER_TYPES

        def do_GET(self):
            if self._from_browser():
                return self._reply(403, {"ok": False, "error": "browser requests are not accepted"})
            if self.path == HEALTH_PATH:
                return self._reply(200, {"ok": True})
            if self.path == STATUS_PATH:
                return self._reply(200, companion.describe())
            return self._reply(404, {"ok": False, "error": "unknown path"})

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0 or length > MAX_HOOK_BODY:
                self.close_connection = True
                return self._reply(413, {"ok": False, "error": "body too large"})
            # Read the body before any reply: closing a socket with unread data
            # makes Windows reset the connection before the client sees the answer.
            raw = self.rfile.read(length) if length else b""
            if self.path not in (HOOK_PATH, STATUSLINE_PATH):
                return self._reply(404, {"ok": False, "error": "unknown path"})
            if self._from_browser():
                return self._reply(403, {"ok": False, "error": "browser requests are not accepted"})
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else None
            except (UnicodeDecodeError, ValueError):
                payload = None
            # Always "no decision": malformed input is counted by the tracker,
            # never answered with an error that Claude Code would surface.
            try:
                if self.path == HOOK_PATH:
                    companion.on_hook(payload)
                else:
                    companion.on_statusline(payload)
            except Exception:                 # never break Claude Code because of a bug here
                log.exception("failed to process %s", self.path)
            if self.path == STATUSLINE_PATH:  # not a hook: the status line shows the link
                return self._reply(200, {"link": companion.link})
            return self._reply(204)

    return Handler


def run_forever(config: Config) -> int:
    companion = Companion(config)
    companion.start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        log.info("stopping")
    finally:
        companion.stop()
    return 0
