# OVO companion

A small Python service that runs next to Claude Code and shows its activity on
the OVO desk device (the MochiEyes firmware's Claude Code face). It uses
Claude Code's supported integration points, hooks and the status line, and
never scrapes the terminal. Standard library only, Python 3.9+.

How it works, what each screen means and how it was tested:
[docs/CLAUDE_MONITOR.md](../docs/CLAUDE_MONITOR.md).

## Requirements

* Claude Code. Verified with **2.1.296**. Older versions get a conservative
  setup (command hooks, core events); see [Versions](#versions).
* Python 3.9 or newer on the same computer.
* The OVO device running this repository's firmware, on a network the computer
  can reach (TCP port 80). Find its address on the Claude face ("Waiting for
  the companion" shows the IP) or use `mochi.local`.

## Install the companion

```sh
cd companion
pip install -e .            # adds the `ovo-companion` command
```

Without installing, every command also works as
`python companion/ovo-companion.py <command>`.

## Check the device first

```sh
ovo-companion send-test --device 192.168.1.42 --state approval --activity "Run: npm"
```

Switch the device to the Claude face (hold the pad for 5 seconds twice from
the eyes). You should see APPROVAL NEEDED and hear two beeps, if sound and the
permission alert are on. After the stale timeout (15 s) it shows STALE,
because `send-test` sends one message and no heartbeat.

## Add the hooks to Claude Code

In the project you work on:

```sh
cd /path/to/your/project
ovo-companion install --dry-run   # shows what would change
ovo-companion install
```

This merges into `.claude/settings.local.json`, the per-project file Claude
Code keeps out of git:

* one hook handler for each of 14 events (13 HTTP hooks to
  `http://127.0.0.1:47823/ovo/v1/hook`, plus an async command hook for
  `SessionStart`, which does not accept HTTP hooks);
* a `statusLine` that reports the model and effort and then runs your previous
  status line, if you had one.

Everything else in the file is left as it was. The file is backed up first
(`settings.local.json.ovo-backup-<time>`), and running `install` again
updates in place without duplicating anything. Nothing is added to
`permissions` and no permission mode changes.

To monitor every project, install once for your user instead:

```sh
ovo-companion install --scope user       # ~/.claude/settings.json
```

Claude Code picks up hook changes automatically. Start a new session if the
status line does not appear.

## Run

```sh
ovo-companion run --device 192.168.1.42        # or --device mochi.local
```

Keep it running while you use Claude Code (a terminal tab, or a login task).
It listens on 127.0.0.1:47823 and sends the device an update whenever the
state changes and every 5 seconds. Stop it with Ctrl+C: the device shows
DISCONNECTED straight away.

```sh
ovo-companion status        # sessions, link to the device, counters
ovo-companion run -v        # log every request
```

## Remove

```sh
ovo-companion uninstall                  # or: uninstall --scope user
```

This removes only the OVO handlers and restores your previous status line. A
status line you changed yourself after installing is left alone. If the
installer created the settings file, the file is deleted. Backups are kept.

## Configuration

Flags win over environment variables, which win over the config file, which
wins over the defaults.

| Setting | Flag | Environment | Config key | Default |
| ------- | ---- | ----------- | ---------- | ------- |
| Device host or IP | `--device` | `OVO_DEVICE` | `device` | `mochi.local` |
| Device port | `--device-port` | `OVO_DEVICE_PORT` | `device_port` | 80 |
| Local port | `--port` | `OVO_PORT` | `listen_port` | 47823 |
| Heartbeat | `--heartbeat` | | `heartbeat_s` | 5 s |
| Request timeout | | | `timeout_s` | 2 s |
| Longest retry gap | | | `retry_max_s` | 30 s |
| Forget a silent session | | | `session_expiry_s` | 7200 s |
| Show ENDED for | | | `ended_linger_s` | 60 s |

Config file: `%APPDATA%\ovo-companion\config.json` (Windows) or
`~/.config/ovo-companion/config.json`; example in
[config.example.json](config.example.json). If you change the local port, pass
the same `--port` to `install`.

## Versions

`install` runs `claude --version`:

| Claude Code | Transport | Events |
| ----------- | --------- | ------ |
| 2.1.251 or newer | HTTP hooks (SessionStart: async command hook) | all 14 |
| older, or not detected | async command hooks (`ovo-companion hook`) | the 8 core events |

Override with `--transport http|command`, `--events core|all` or
`--claude-version 2.1.296`.

## Development

```sh
pip install -e ".[dev]"
python -m pytest            # from companion/
```

The tests use a scripted clock, simulated hook payloads in Claude Code's
documented shapes, a fake device HTTP server and temporary settings
directories. They never touch your real `~/.claude`.

| Module | Role |
| ------ | ---- |
| `events.py`, `statusline.py` | parse Claude Code input; never guess missing values |
| `activity.py` | tool calls -> short labels; sanitised approval text |
| `tracker.py` | sessions, states, approvals, ordering, timers, primary session |
| `protocol.py` | the device message; mirrors `firmware/.../ClaudeStatus.h` |
| `device.py` | HTTP to the device: timeouts, address cache, backoff |
| `daemon.py` | local receiver and sender thread |
| `forward.py` | the programs Claude Code runs (hook forwarder, status line) |
| `installer.py` | merge into and remove from Claude Code settings |
| `cli.py`, `config.py` | command line and configuration |
