"""OVO companion: shows what Claude Code is doing on the MochiEyes OLED.

The package runs on the computer where Claude Code runs. Claude Code hooks and
the status line report events to a small local daemon; the daemon keeps track
of sessions and sends a compact, sanitised status to the device over HTTP.

Layers, from the outside in:

    cli.py         command line: run, install, uninstall, status, send-test
    installer.py   merges the hooks and status line into Claude Code settings
    forward.py     tiny clients run *by* Claude Code (hook and status line)
    daemon.py      local HTTP receiver + device sender thread
    tracker.py     sessions, ordering, timers, primary-session choice
    events.py      hook payload -> HookEvent
    statusline.py  status-line payload -> model and effort
    activity.py    tool calls -> short display labels
    protocol.py    the JSON message the firmware accepts
    device.py      HTTP client for the device, with timeouts and backoff

Only the standard library is used, so the companion installs anywhere Python
3.9+ runs.
"""

__version__ = "1.0.0"
