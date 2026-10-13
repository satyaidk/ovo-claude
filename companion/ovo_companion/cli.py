"""Command line for the OVO companion.

    ovo-companion run        start the daemon (keep it running while you use Claude Code)
    ovo-companion install    add the hooks and status line to Claude Code settings
    ovo-companion uninstall  take them out again
    ovo-companion status     what the running daemon sees
    ovo-companion send-test  draw a sample status on the device, without Claude Code

``hook`` and ``statusline`` are run by Claude Code itself, not by you.
"""
from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
import urllib.error
import urllib.request
from typing import List, Optional

from . import __version__, config as config_mod
from .config import DEFAULT_PORT, LISTEN_HOST


def _cmd_run(args: argparse.Namespace) -> int:
    from .daemon import run_forever

    fmt = "%(asctime)s %(levelname)s %(message)s"
    level = logging.DEBUG if args.verbose else logging.INFO
    if args.log_file:
        # Started in the background (autostart.py): no console, so log to a small rotating file.
        from logging.handlers import RotatingFileHandler
        pathlib.Path(args.log_file).parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(args.log_file, maxBytes=256 * 1024, backupCount=1, encoding="utf-8")
        logging.basicConfig(level=level, format=fmt, datefmt="%Y-%m-%d %H:%M:%S", handlers=[handler])
    else:
        logging.basicConfig(level=level, format=fmt, datefmt="%H:%M:%S")
    cfg = config_mod.load(pathlib.Path(args.config) if args.config else None, overrides={
        "device": args.device, "device_port": args.device_port, "listen_port": args.port,
        "heartbeat_s": args.heartbeat})
    return run_forever(cfg)


def _cmd_hook(args: argparse.Namespace) -> int:
    from .forward import hook_main
    return hook_main(args.port)


def _cmd_statusline(args: argparse.Namespace) -> int:
    from .forward import statusline_main
    return statusline_main(args.port, args.manifest)


def _print_report(rep, verb: str) -> None:
    print(f"{verb}: {rep.settings_file}")
    if rep.transport:
        print(f"  transport: {rep.transport} hooks, {len(rep.events)} events: {', '.join(rep.events)}")
    print(f"  changed:   {'yes' if rep.changed else 'no (already up to date)'}")
    if rep.backup:
        print(f"  backup:    {rep.backup}")
    print(f"  record:    {rep.record_file}")
    for note in rep.notes:
        print(f"  note:      {note}")


def _cmd_install(args: argparse.Namespace) -> int:
    from . import installer

    version = installer.parse_version(args.claude_version) if args.claude_version else installer.detect_claude_version()
    try:
        rep = installer.install(args.scope, pathlib.Path(args.project), transport=args.transport,
                                events=args.events, port=args.port, version=version, dry_run=args.dry_run)
    except installer.InstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"Claude Code version: {'.'.join(map(str, version)) if version else 'not detected'}")
    _print_report(rep, "installed into" if not args.dry_run else "would install into")
    if args.dry_run:
        return 0
    if args.device or args.device_port:
        try:
            path = config_mod.save({"device": args.device, "device_port": args.device_port})
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"  device:    {config_mod.load(path).device} (saved in {path})")
    else:
        print(f"  device:    {config_mod.load().device} (change with: ovo-companion install --device <IP>)")
    print("The companion now starts by itself: with the next Claude Code session, or after the next")
    print("reply in a session that is already open. To run it in a terminal instead: ovo-companion run")
    return 0


def _cmd_uninstall(args: argparse.Namespace) -> int:
    from . import installer
    try:
        rep = installer.uninstall(args.scope, pathlib.Path(args.project), dry_run=args.dry_run)
    except installer.InstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    _print_report(rep, "removed from" if not args.dry_run else "would remove from")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    url = f"http://{LISTEN_HOST}:{args.port}/ovo/v1/status"
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"companion is not running on port {args.port} ({exc})", file=sys.stderr)
        return 1
    print(json.dumps(data, indent=2))
    return 0


def _cmd_send_test(args: argparse.Namespace) -> int:
    import secrets
    from .device import DeviceClient
    from .protocol import Snapshot, build_payload, encode

    cfg = config_mod.load(pathlib.Path(args.config) if args.config else None,
                          overrides={"device": args.device, "device_port": args.device_port})
    snap = Snapshot(state=args.state, session_id="test0001", sessions=1, event="SendTest", model=args.model,
                    effort=args.effort, activity=args.activity, permission_mode=args.mode,
                    approval_required=args.state == "approval", elapsed_seconds=args.elapsed,
                    session_seconds=args.elapsed)
    body = encode(build_payload(snap, "test" + secrets.token_hex(2), 1))
    result = DeviceClient(cfg.device, cfg.device_port, cfg.timeout_s).send(body)
    if result.ok:
        print(f"ok: {cfg.device} replied '{result.reply}'. The device shows it in Claude mode "
              "(hold the pad 5 s twice from the eyes). It turns STALE after the stale timeout.")
        return 0
    print(f"failed: {result.error}", file=sys.stderr)
    return 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ovo-companion", description="Show Claude Code activity on the OVO desk device.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="start the companion daemon")
    r.add_argument("--device", help="device host name or IP (default mochi.local)")
    r.add_argument("--device-port", type=int)
    r.add_argument("--port", type=int, help=f"local receiver port (default {DEFAULT_PORT})")
    r.add_argument("--heartbeat", type=float, help="seconds between status resends (default 5)")
    r.add_argument("--config", help="JSON config file (default: %s)" % config_mod.default_config_path())
    r.add_argument("-v", "--verbose", action="store_true")
    r.add_argument("--log-file", help="log here instead of the console (used by auto-start)")
    r.set_defaults(func=_cmd_run)

    for name, helptext in (("install", "add hooks and status line to Claude Code settings"),
                           ("uninstall", "remove them again")):
        i = sub.add_parser(name, help=helptext)
        i.add_argument("--scope", choices=("local", "user"), default="local",
                       help="local: <project>/.claude/settings.local.json (default); user: ~/.claude/settings.json")
        i.add_argument("--project", default=".", help="project directory for --scope local (default: current)")
        i.add_argument("--dry-run", action="store_true", help="show what would change, write nothing")
        if name == "install":
            i.add_argument("--transport", choices=("auto", "http", "command"), default="auto")
            i.add_argument("--events", choices=("auto", "core", "all"), default="auto")
            i.add_argument("--port", type=int, default=DEFAULT_PORT)
            i.add_argument("--claude-version", help="skip detection, e.g. 2.1.296")
            i.add_argument("--device", help="device host name or IP, saved for the auto-started companion")
            i.add_argument("--device-port", type=int, help="device HTTP port (default 80)")
            i.set_defaults(func=_cmd_install)
        else:
            i.set_defaults(func=_cmd_uninstall)

    s = sub.add_parser("status", help="show what the running daemon sees")
    s.add_argument("--port", type=int, default=DEFAULT_PORT)
    s.set_defaults(func=_cmd_status)

    t = sub.add_parser("send-test", help="send one sample status to the device")
    t.add_argument("--device")
    t.add_argument("--device-port", type=int)
    t.add_argument("--config")
    t.add_argument("--state", default="running", choices=("idle", "running", "approval", "turn_done",
                                                           "task_done", "failed", "ended"))
    t.add_argument("--model", default="Opus")
    t.add_argument("--effort", default="high", choices=("", "low", "medium", "high", "xhigh", "max"))
    t.add_argument("--activity", default="Running tests")
    t.add_argument("--mode", default="default", help="permission mode, e.g. default, plan, auto")
    t.add_argument("--elapsed", type=int, default=763)
    t.set_defaults(func=_cmd_send_test)

    h = sub.add_parser("hook", help=argparse.SUPPRESS)
    h.add_argument("--port", type=int, default=DEFAULT_PORT)
    h.set_defaults(func=_cmd_hook)

    sl = sub.add_parser("statusline", help=argparse.SUPPRESS)
    sl.add_argument("--port", type=int, default=DEFAULT_PORT)
    sl.add_argument("--manifest")
    sl.set_defaults(func=_cmd_statusline)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ValueError as exc:     # configuration errors
        print(f"error: {exc}", file=sys.stderr)
        return 2
