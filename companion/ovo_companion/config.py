"""Configuration: defaults, then a JSON file, then environment, then flags.

The device address is never assumed beyond the firmware's own default mDNS
name (``MDNS_NAME`` "mochi" in MochiEyes.ino -> ``mochi.local``). Set
``device`` to an IP address if mDNS does not resolve on your network.

The local receiver always binds to 127.0.0.1: hook payloads contain prompts
and tool input, which must not be reachable from the network.
"""
from __future__ import annotations

import json
import os
import pathlib
from dataclasses import asdict, dataclass, fields
from typing import Any, Mapping, Optional

LISTEN_HOST = "127.0.0.1"
DEFAULT_PORT = 47823


@dataclass
class Config:
    device: str = "mochi.local"        # host name or IP address of the OVO device
    device_port: int = 80
    listen_port: int = DEFAULT_PORT    # local receiver for hooks and the status line
    heartbeat_s: float = 5.0           # resend the state this often (device marks STALE after 15 s)
    timeout_s: float = 2.0             # per HTTP request to the device
    retry_max_s: float = 30.0          # longest wait between retries while the device is unreachable
    session_expiry_s: float = 7200.0   # forget a session with no events for this long
    ended_linger_s: float = 60.0       # keep showing ENDED this long

    def validate(self) -> "Config":
        if not isinstance(self.device, str) or not self.device.strip():
            raise ValueError("device must be a host name or IP address")
        for name in ("device_port", "listen_port"):
            v = getattr(self, name)
            if not isinstance(v, int) or not 1 <= v <= 65535:
                raise ValueError(f"{name} must be a port number (1-65535)")
        if not 1.0 <= float(self.heartbeat_s) <= 60.0:
            raise ValueError("heartbeat_s must be between 1 and 60 seconds")
        if not 0.2 <= float(self.timeout_s) <= 30.0:
            raise ValueError("timeout_s must be between 0.2 and 30 seconds")
        if float(self.retry_max_s) < 1.0:
            raise ValueError("retry_max_s must be at least 1 second")
        return self


def default_config_path() -> pathlib.Path:
    if os.name == "nt" and os.environ.get("APPDATA"):
        return pathlib.Path(os.environ["APPDATA"]) / "ovo-companion" / "config.json"
    base = os.environ.get("XDG_CONFIG_HOME") or str(pathlib.Path.home() / ".config")
    return pathlib.Path(base) / "ovo-companion" / "config.json"


def state_dir() -> pathlib.Path:
    """Where install manifests live (outside any project repository)."""
    return default_config_path().parent


_ENV = {"OVO_DEVICE": "device", "OVO_DEVICE_PORT": "device_port", "OVO_PORT": "listen_port"}


def _coerce(name: str, value: Any) -> Any:
    kind = {f.name: f.type for f in fields(Config)}[name]
    if kind in (int, "int"):
        if isinstance(value, bool):
            raise ValueError(f"{name} must be a number")
        return int(value)
    if kind in (float, "float"):
        return float(value)
    return str(value)


def load(path: Optional[pathlib.Path] = None, env: Optional[Mapping[str, str]] = None,
         overrides: Optional[Mapping[str, Any]] = None) -> Config:
    cfg = Config()
    known = {f.name for f in fields(Config)}
    file = path or default_config_path()
    if file.exists():
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ValueError(f"{file}: invalid JSON ({exc})") from exc
        if not isinstance(data, dict):
            raise ValueError(f"{file}: expected a JSON object")
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"{file}: unknown keys {sorted(unknown)}")
        for k, v in data.items():
            setattr(cfg, k, _coerce(k, v))
    env = os.environ if env is None else env
    for var, name in _ENV.items():
        if env.get(var):
            setattr(cfg, name, _coerce(name, env[var]))
    for k, v in (overrides or {}).items():
        if v is not None:
            setattr(cfg, k, _coerce(k, v))
    return cfg.validate()


def as_dict(cfg: Config) -> dict:
    return asdict(cfg)
