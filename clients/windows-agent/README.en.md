# Device agent (dial-home)

A small, headless agent that lets a Linux **alb hub** reach an Android device
that is physically attached to *this* machine (USB for adb, serial for UART).

It dials **out** to the hub over a single WebSocket — so there is **no inbound
port, no SSH, and no third-party terminal** to configure. The hub then exposes
the device locally (e.g. `127.0.0.1:5037` for adb) so the LLM (via MCP) and
humans (via the web UI) can debug it, all driven from the Linux side.

## Run (config file + one click — the normal path)

1. Copy `agent.conf.example` to `agent.conf` (same folder) and fill in
   `hub_url` + `token` (+ optionally `name`, and `agent_id` for a stable
   identity across restarts).
2. Double-click `run-agent.bat`.

The script re-checks the environment on every run and prints each result:
Python 3.11+ (`python`, falling back to the `py -3` launcher), the two
dependencies (auto-installed when missing), `agent.conf` presence, adb on
PATH with its current device list (warn-only — serial works without it), and
the serial ports visible on this machine. Then it prints the status page URL
and starts the agent. On errors the window stays open so the reason is
readable. A stuck adb enumeration can be fixed from the hub side with
`POST /api/agent/adb/restart` — the agent restarts its local adb server and
re-reports devices.

The classic "driver fine, device enumerated, adb sees nothing" case is a
*renamed* adb build (shipped inside vendor PC tools) exclusively holding the
USB interface. The agent detects this: an empty device list triggers a fuzzy
process scan ("adb" as a standalone token in the name — `xxx_adb`,
`adb_server` — never a mid-word substring, so innocent bystanders don't get
flagged or killed) whose hits show up in
the startup check, on the status page (`adb conflicts`), and in
`/agent/status` (`adb_conflicts`). Clear it remotely with
`POST /api/agent/adb/restart?kill_conflicts=true` — the agent terminates the
suspects (its own heuristic decides what matches; the hub can never name a
process) before restarting the server. Default is false: killing a vendor
tool's adb mid-flash would interrupt it.

`agent.conf` holds your real token, so it is gitignored — only the
`.example` is tracked.

## Run (flags — override any config value)

```
pip install -r requirements.txt
python alb_agent.py --hub-url wss://<hub>/agent/connect --token <token>
```

Precedence: command line > `agent.conf` > built-in defaults.

- `--config`  — config file path (default: `agent.conf` next to the script).
- `--hub-url` — the hub's signaling endpoint (`/agent/connect`).
- `--token`   — must match `ALB_AGENT_TOKEN` configured on the hub. Omit only
  for a fully local, no-auth dev setup.
- `--name`    — a human-readable label shown in the hub's device list.
- `--agent-id`— a stable id (defaults to a random one each run).

- `--status-port` — local status page port on 127.0.0.1 (default `8731`; `0`
  disables). `--status-host` to change the bind host.

Ctrl-C once stops the agent gracefully; if it ever hangs, a second Ctrl-C
hard-kills the process (serial writes also carry a 2 s timeout, so a
flow-control-wedged port can no longer block shutdown).

Keep the process running (it auto-reconnects with backoff if the link drops).
On Windows run it via `run-agent.bat` in a terminal or point Task Scheduler
at the `.bat` for start-at-boot — set `ALB_AGENT_NO_PAUSE=1` in the task's
environment so an exit never waits for a keypress (interactively the script
pauses on exit to keep the window readable). A background service wrapper
can come later.

### Status page

The agent serves a localhost-only status page on `http://127.0.0.1:8731`. It
shows the hub's web-console URL (a clickable `web console` row, derived from
`hub_url`; the startup log prints the same line), the connection state,
active channels, enumerated devices, and the last error — so you can tell whether the dial-home reached the hub *without* logging
into the hub, even when the hub never saw the agent (wrong token / hub
unreachable). The token is never shown there; `GET /status.json` exposes the
same data for scripted checks.

## What it does

- Maintains the signaling connection (hello + heartbeat + auto-reconnect).
- On request from the hub, bridges the local **adb server** (`127.0.0.1:5037`)
  to the hub over a per-request data connection.
- Bridges a **UART / serial port** (the hub picks the COM + baud) to the hub,
  for boot-log capture and interactive console.
- Reports attached adb devices on request (`adb devices`).

To use serial, set the COM + baud on the **hub** (it drives the agent):

```
ALB_AGENT_SERIAL_COM=COM27 ALB_AGENT_SERIAL_BAUD=1500000 alb-api
```

- Enumerates attached serial ports on request (`serial.tools.list_ports`).

For the full end-to-end bring-up + verification + troubleshooting, see
`docs/dial-home-runbook.md`.

## Bench ops (the op table)

On connect (`hello`) the agent reports an **op table**: every operation this
host can run, with its parameter rules, whether it needs the device to be in
fastboot, whether it changes the device, whether it needs the single job
lock, and its timeout. The hub's CLI and web page drive the rows as given,
so a new op on the agent needs no hub code.

| op | what | needs a device in fastboot |
|---|---|---|
| `devices` / `getvar` / `oem` / `reboot` / `flash` | the existing fastboot ops, unchanged | yes |
| `erase` | `fastboot erase <partition>`; same allowlist as `flash` (`flash_partitions`) | yes |
| `set-active` | `fastboot set_active a` or `b` | yes |
| `boot` | `fastboot boot <image>`: runs from RAM, writes no partition | yes |
| `usb-list` | USB devices as Windows sees them: name, VID:PID, serial, driver, problem code, interface class ids — tells "cable not on this machine" from "driver missing" when `fastboot devices` is empty | no |
| `log-tail` | last N lines of a host log, optional substring filter. Sources are names only: `agent` (this agent's own log, when `log_file` is set), `setupapi` (Windows' driver-install log) and `update` (the last remote update, once there has been one) | no |
| `agent-update` | replace this agent's own files and restart it — see below | no |

Two rules do not change:

- The hub sends structured fields only — never a command line, never a
  path. argv is assembled here from a fixed template after every parameter
  is validated (partition shape + allowlist, enum values matched exactly,
  integers bounded).
- `usb-list` and `log-tail` read this host only and never take the job
  lock, so they answer during a running flash. fastboot ops run one at a
  time; a second one is refused at once, not queued.

Without a fastboot executable the table holds only `usb-list` / `log-tail` /
`agent-update`, and the hub tells callers this bench cannot flash. The status
page lists the table on its `ops` row.

## Updating the agent from the hub

Install by hand **once** (copy the folder, close the old agent window, run
`run-agent.bat`): an agent from before this feature cannot receive updates.
After that, from the hub side:

```bash
alb bench agent-update                      # program files only; agent.conf here is kept
alb bench agent-update --config agent.conf  # also replace agent.conf
```

The hub sends its own `clients/windows-agent/` folder. On this host:

1. The bundle is checked (size, sha256, flat file names only).
2. The new code runs `alb_agent.py --self-check` (compiles, imports, reads
   the config). A failure refuses the update; the old build keeps running.
3. An update helper starts and the old agent exits; its window closes.
4. The helper backs the current files up to `.alb-agent/backup/`, installs
   the new ones and starts the new build in a new window.
5. If the new build has not reached the hub within 60 s, the old files go
   back and the old build starts again; the hub reports
   `AGENT_UPDATE_ROLLED_BACK` and `alb bench log update` shows why.

Identical files restart nothing. `alb bench ops` shows the build this host
runs and says when the hub has a newer one.

### One instance per agent

Starting an agent that is already running stops the running one first (it
exits and its window closes); one in the middle of a flash is waited for.
"The same agent" means the same config file: for two boards on one host,
copy the whole folder and give the copy its own `agent_id` / `name`.

## Security

- The agent only ever proxies an **allowlisted** local target
  (`127.0.0.1:5037`); it re-checks every request and refuses anything else, so
  it can never be turned into an open proxy on your LAN.
- Traffic rides the same authenticated `wss://` connection as the hub API.
- Configure a token (`--token` / `ALB_AGENT_TOKEN`) for any non-loopback hub.
- `agent-update` lets whoever drives the hub replace this agent's code, i.e.
  run code on this host. That is accepted for a bench on a trusted network
  (ADR-060); the hub API has no authentication of its own.
