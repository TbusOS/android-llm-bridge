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
process scan (name contains "adb", is not adb itself) whose hits show up in
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

Keep the process running (it auto-reconnects with backoff if the link drops).
On Windows run it via `run-agent.bat` in a terminal or point Task Scheduler
at the `.bat` for start-at-boot — set `ALB_AGENT_NO_PAUSE=1` in the task's
environment so an exit never waits for a keypress (interactively the script
pauses on exit to keep the window readable). A background service wrapper
can come later.

### Status page

The agent serves a localhost-only status page on `http://127.0.0.1:8731`. It
shows the connection state, active channels, enumerated devices, and the last
error — so you can tell whether the dial-home reached the hub *without* logging
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

## Security

- The agent only ever proxies an **allowlisted** local target
  (`127.0.0.1:5037`); it re-checks every request and refuses anything else, so
  it can never be turned into an open proxy on your LAN.
- Traffic rides the same authenticated `wss://` connection as the hub API.
- Configure a token (`--token` / `ALB_AGENT_TOKEN`) for any non-loopback hub.
