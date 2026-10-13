# Connect Claude Code to your OVO: step by step

This guide connects Claude Code on your computer to the OVO desk device, so
the OLED shows what Claude Code is doing. It takes about ten minutes. Each
step says what to do and what you should see before you go on.

For how the feature works inside, see [CLAUDE_MONITOR.md](CLAUDE_MONITOR.md).

## How the pieces connect

```
 Your computer                                         OVO device
┌──────────────────────────────────────────┐          ┌──────────────┐
│ Claude Code ──hooks──> OVO companion ──Wi-Fi──────> │ OLED + buzzer │
│ (any terminal or      (small Python program,        │ Claude face   │
│  project)              starts by itself)            └──────────────┘
└──────────────────────────────────────────┘
```

* **Claude Code** reports each event (a prompt, a tool call, a permission
  request, the end of a reply) to the companion through *hooks*, a feature
  built into Claude Code.
* **The companion** turns those events into a short status and sends it to
  the device over your Wi-Fi.
* **The device** draws the status on the Claude face and beeps for permission
  requests and finished tasks.

Nothing is read from your screen, and the device never receives your prompts,
your files or Claude's replies.

## What you need

| Item | Check |
| ---- | ----- |
| The OVO device, flashed with this repository's firmware and on your Wi-Fi | [README: Getting started](../README.md#getting-started) |
| Claude Code on your computer | `claude --version` prints a version (2.1.296 was used for this guide) |
| Python 3.9 or newer | `python --version` |
| This repository on your computer | the folder that contains `companion/` and `firmware/` |
| Computer and device on the same Wi-Fi network | |

All commands below are typed in a terminal **opened in the repository
folder** (the one that contains `companion/`). On Windows, PowerShell, Command
Prompt and Git Bash all work.

---

## Step 1. Find the device's address

The device needs an address the computer can reach. Try the name first: open
**`http://mochi.local`** in a browser on the computer.

**You should see** the Mochi controller page. Your address is then
**`mochi.local`**.

From a terminal instead, use `curl.exe http://mochi.local/api/state` on
Windows, or `curl` elsewhere. It prints a line of text starting with
`{"owner":`.

If the name does not work, use the IP address instead. To find it, use any of
these:

* power-cycle the device: the IP is shown at the bottom of the eyes for 8
  seconds after it joins Wi-Fi;
* switch to the Claude face (step 2): before anything is connected it shows
  the IP at the bottom of the "Waiting for the companion" screen;
* look in your router's list of connected devices for `mochi`.

Write the address down. This guide uses `192.168.1.62` as the example: replace
it with yours everywhere.

## Step 2. Show the Claude face on the device

Hold the touch pad for **5 seconds**, let go, and hold it for 5 seconds again.
The faces go in the order eyes, clock, Claude.

You can also open `http://<your address>` in a browser and press **Claude** under the
screen preview.

**You should see** a white bar with `CLAUDE CODE` at the top. Below it is
"Waiting for the companion on your PC" if nothing has connected yet, or STALE
or OFFLINE if something connected earlier. All three are fine at this point.

## Step 3. Test the screen and the buzzer (without Claude Code)

This sends one sample status straight to the device:

```sh
python companion/ovo-companion.py send-test --device 192.168.1.62 --state approval --activity "Run: npm"
```

**You should see** in the terminal: `ok: 192.168.1.62 replied 'ok claude'`.
**On the device:** `APPROVAL NEEDED` with a blinking header and a warning
triangle, and **two short beeps**. After 15 seconds it changes to `STALE`,
because a test sends only one message. That is expected.

If you have already done step 4 before, the companion may be running. Then
the sample stays only until the companion's next update, at most 5 seconds,
and the OLED goes back to your real Claude Code status.

| If you see | Do this |
| ---------- | ------- |
| `failed: cannot resolve ...` | The address is wrong or the name does not resolve: use the IP from step 1 |
| `failed: ... timed out` or `refused` | The device is off, on another network, or still starting. Check that `http://<address>` opens in a browser |
| `the firmware has no /api/claude/status` | The device runs older firmware: flash the firmware from this repository |
| No beeps | Sound is off: open `http://<address>`, turn on **Sound** under Settings, and **Approval needed** under Claude Code |

Do not continue until this step works. The next steps rely on it.

## Step 4. Connect Claude Code

This adds the hooks to Claude Code's settings and saves the device's address:

```sh
python companion/ovo-companion.py install --scope user --device 192.168.1.62
```

`--scope user` connects **every** Claude Code session on this computer, in
any project. To connect only one project, see
[One project only](#one-project-only).

**You should see** something like:

```
Claude Code version: 2.1.296
installed into: C:\Users\you\.claude\settings.json
  transport: http hooks, 14 events: SessionStart, UserPromptSubmit, ...
  changed:   yes
  backup:    C:\Users\you\.claude\settings.json.ovo-backup-20261013T174503Z
  device:    192.168.1.62 (saved in C:\Users\you\AppData\Roaming\ovo-companion\config.json)
The companion now starts by itself: ...
```

What this changed:

* added 14 hooks and a status line to Claude Code's settings file. Everything
  else in that file stays as it was, and the original is kept as the `backup`
  file shown;
* saved the device address for the companion.

What it did **not** change: your permissions, your permission mode, or
anything in your projects. The companion can never approve or deny anything
for you.

Running the same command again is safe. Use it again whenever the device's
address changes.

## Step 5. Start Claude Code and watch the OLED

Open a **new** terminal (anywhere) and start Claude Code:

```sh
claude
```

Give it a small task that uses a tool, for example:
`list the files in this folder and tell me which is the largest`.

**You should see on the device**, within a second or two of each event:

| When | The OLED shows |
| ---- | -------------- |
| Claude Code opens | `READY`, your model, "Waiting for a prompt" |
| You send the prompt | Running: model and effort (for example `Opus 5.5 \| XHIGH`), `Thinking...`, an animated `Working` bar, a timer and the approval mode (`MANUAL`, `AUTO`, ...) |
| Claude uses a tool | the activity changes: `Reading files`, `Searching code`, `Editing code`, `Running tests`, `Running command` |
| Claude asks your permission | `APPROVAL NEEDED`, what it wants to do (`Run: npm`), two beeps. Approve or deny in Claude Code, not on the device |
| Claude finishes | `TASK DONE` (or `TURN DONE` if background work is still running) and a completion tone |

**You should see in Claude Code**, at the bottom of the terminal, a status
line such as `OVO ● | Opus 5.5 | xhigh`. The dot tells you the connection:

| Dot | Meaning |
| --- | ------- |
| `●` filled | Everything works: the device is receiving |
| `○` hollow | The companion runs, but the device does not answer (address, Wi-Fi, device off) |
| `◌` dotted | The companion is not running yet. It starts by itself after the next reply |

If you had your own status line before step 4, you keep seeing yours instead.
The OVO line only appears when there was none.

You do not need to start the companion yourself. The first Claude Code
session starts it in the background, and it keeps running after you close
Claude Code. A session that was already open before step 4 picks it up after
its next reply.

## Step 6. Check it any time

```sh
python companion/ovo-companion.py status
```

It shows `"link": "online"` when the device is receiving, plus each Claude
Code session the companion knows about. The companion's log is
`companion.log` in the same folder as the saved config (step 4 prints that
folder).

On the device, tap the pad once on the Claude face for a details page: link
state, session, raw permission mode, last event, time since the last update,
and which alerts are on. Tap again to go back.

---

## Settings you may want

Open `http://<your address>` in a browser and scroll to **Claude Code**:

| Setting | What it does |
| ------- | ------------ |
| Approval needed | two beeps when Claude Code asks for permission (on) |
| Done | a tone when a task or turn finishes (on) |
| Failed | a warning when a turn ends with an error (on) |
| Connection lost | one warning when the companion stops reporting (off) |
| Mark as stale after | how long without news before the OLED says STALE (15 s) |

**Sound** under Settings mutes everything. **Show after a restart → Claude**
makes the device start on the Claude face.

## One project only

Instead of step 4, run this inside the project folder:

```sh
python <path to this repository>/companion/ovo-companion.py install --device 192.168.1.62
```

This writes to that project's `.claude/settings.local.json`, which Claude Code
keeps out of git. Only sessions started in that project are shown.

## Turn it off or remove it

| You want to | Run |
| ----------- | --- |
| Remove it completely (every project) | `python companion/ovo-companion.py uninstall --scope user` |
| Remove it from one project | `python companion/ovo-companion.py uninstall` inside that project |
| Stop the background companion | end the `pythonw.exe` (Windows) or `python` process running `ovo-companion.py run`, or restart the computer |
| Keep the hooks but never auto-start | set the environment variable `OVO_NO_AUTOSTART=1` and run `python companion/ovo-companion.py run` yourself when you want it |

`uninstall` removes only what `install` added and puts back the status line
you had before, if any.

## Troubleshooting

| What you see | Why | What to do |
| ------------ | --- | ---------- |
| OLED: `Waiting for the companion` | Nothing has connected since the device started | Do step 5. If the status line shows `◌`, send one more prompt (the companion starts after a reply) |
| OLED: `STALE`, "Last: ..." | Updates stopped: the computer slept, Wi-Fi dropped, or the companion stopped | Usually fixes itself on the next Claude Code reply. Run `status` (step 6) to check |
| OLED: `OFFLINE` / `DISCONNECTED` | The companion was stopped normally | It starts again with the next session or reply |
| Status line shows `○` | The device does not answer | Repeat step 3. If the address changed, repeat step 4 with the new one |
| Nothing changes on the OLED, status line shows `●` | The device is on another face | Hold the pad 5 s until the Claude face appears. Status updates arrive on any face, but only the Claude face shows them |
| No status line in Claude Code | Claude Code was open before step 4, or you use an editor extension | Start a new `claude` session in a terminal. The OLED works in editors too |
| The model shows `Model --` | No status line ran yet (for example `claude -p` scripts) | Normal in scripts. Interactive sessions show the model |
| Effort is missing | Your current model has no effort setting | Normal |
| `python` not found (Windows) | Python is installed as `py` | Use `py companion/ovo-companion.py ...` |
| You moved the repository folder | The hooks point at the old folder | Run step 4 again from the new folder |

Still stuck? Run the companion in a terminal to watch every event:

```sh
python companion/ovo-companion.py run --device 192.168.1.62 -v
```

If a background companion is already running, this one says so and exits.
End the background one first (see [Turn it off](#turn-it-off-or-remove-it)).
