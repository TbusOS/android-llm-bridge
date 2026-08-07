# Dial-home real-device runbook

Drive an Android device that is physically attached to a **Windows host** from a
**Linux hub** — adb (shell / logcat / commands) and the UART console — with **no
inbound port, no SSH, and no third-party terminal** on the Windows side. The
agent dials *out* to the hub over a single `wss://`; the hub re-exposes the
device on its own loopback so the LLM (via MCP) and humans (via the web UI) debug
it, all driven from Linux.

This is the step-by-step to bring the whole chain up against real hardware and
confirm each hop. The code paths are covered by tests, but the end-to-end with a
real board has to be run by hand — that is what this page is for.

## Topology

```
   Linux hub (one host)                          Windows host (has the device)
 ┌───────────────────────────┐                 ┌────────────────────────────┐
 │ alb-api                    │                 │ alb_agent.py               │
 │  ├─ adb forwarder :5037 ───┼── wss (out) ◄───┼── dials the hub            │
 │  └─ serial forwarder :9001 │                 │   ├─ adb server :5037      │
 │                            │                 │   └─ COM port (UART)       │
 │ alb / alb-mcp (same host)  │                 └──────────────┬─────────────┘
 │  ├─ adb  → 127.0.0.1:5037  │                                │ USB + serial
 │  └─ serial→127.0.0.1:9001  │                       ┌────────┴────────┐
 └───────────────────────────┘                       │  target board   │
                                                      └─────────────────┘
```

The forwarders are real loopback listeners owned by the `alb-api` process
(ADR-051), so the separate `alb` CLI and `alb-mcp` processes reach the device by
plain `connect(127.0.0.1, …)` — nothing about alb's transports or MCP tools
changes for remote vs local.

## What you need

- **Linux hub**: alb installed (`alb-api`, `alb`, `alb-mcp` on PATH), reachable
  from the Windows host at some `wss://<hub>/agent/connect` (terminate TLS at
  your reverse proxy, or use `ws://` only on a trusted LAN).
- **Windows host**: Python 3.11+, the device on USB (for adb) and/or a serial
  port (for UART), and outbound network to the hub. `adb` on PATH if you want
  adb. The agent is `clients/windows-agent/` (`alb_agent.py` + `requirements.txt`).
- **A shared token** (any random string) so only your agent can dial in.

## 1. Hub (Linux)

The one-command path — fill a file, run a script:

```bash
cd deploy/hub
cp hub.env.example hub.env    # fill in ALB_AGENT_TOKEN (+ COM/baud for serial)
./run-hub.sh
```

`run-hub.sh` loads `hub.env` (which is gitignored — only the `.example` is
tracked) and starts `alb-api`, falling back to `uv run alb-api` inside a repo
checkout. Everything below is what the file configures, if you prefer to set
the environment by hand. The serial COM + baud are chosen on the **hub** (it
drives the agent); leave them unset to run adb-only.

| env var | purpose | example |
|---|---|---|
| `ALB_AGENT_TOKEN` | auth — must match the agent's `token` (agent.conf / `--token`) | `s3cret-xyz` |
| `ALB_AGENT_SERIAL_COM` | the COM port the agent should open (enables serial) | `COM27` |
| `ALB_AGENT_SERIAL_BAUD` | serial baud | `1500000` |
| `ALB_ADB_FORWARD_PORT` | override the adb forward port (default `5037`) | `15037` |
| `ALB_SERIAL_FORWARD_PORT` | override the serial forward port (default `9001`) | `19001` |

```bash
export ALB_AGENT_TOKEN='s3cret-xyz'
export ALB_AGENT_SERIAL_COM='COM27'      # omit for adb-only
export ALB_AGENT_SERIAL_BAUD='1500000'
alb-api
```

> **Important — the forward port must be free.** The adb forwarder binds
> `127.0.0.1:5037`, which is also the default adb-server port. If the hub runs
> its own adb server, the bind fails: the agent still connects (signaling +
> serial keep working) but adb stays unavailable and the log says so. Either
> stop the hub's adb server (`adb kill-server`) or set `ALB_ADB_FORWARD_PORT`
> to a free port. On the hub, the forwarder *is* the adb server alb talks to.

The `alb` CLI and `alb-mcp` need **no extra env** for the default ports: adb
defaults to `127.0.0.1:5037` and serial defaults to `127.0.0.1:9001`, which are
exactly the forwarders. Only if you overrode a forward port do you point alb at
it — adb via `ADB_SERVER_SOCKET=tcp:127.0.0.1:<port>`, serial via
`ALB_SERIAL_TCP=127.0.0.1:<port>`.

## 2. Agent (Windows host, where the device is)

Copy `clients/windows-agent/` onto the host, then:

1. Copy `agent.conf.example` to `agent.conf` and fill in `hub_url` + `token`
   (the same token as the hub's `hub.env`).
2. Double-click `run-agent.bat` — it installs the two dependencies on first
   run and starts the agent.

Or by hand, flags overriding anything in `agent.conf`:

```
pip install -r requirements.txt
python alb_agent.py --hub-url wss://<hub>/agent/connect --token s3cret-xyz --name bench-01
```

The agent auto-reconnects with backoff and serves a **local status page** on
`http://127.0.0.1:8731` (change with `--status-port`, disable with
`--status-port 0`). Open it first — it tells you whether the dial-home reached
the hub *before* you involve a board, even when the hub never saw the agent
(wrong token / hub unreachable show up here, with the last error).

## 3. Verify each hop

Walk down the chain; each hop has an unambiguous "good" signal.

1. **Agent reached the hub** — `http://127.0.0.1:8731` (on the Windows host)
   shows **connected**, with uptime climbing. Headless check:
   `curl -s http://127.0.0.1:8731/status.json`.
2. **Hub sees the agent** — on the hub (`alb-api` defaults to port `8765`):
   `curl -s http://127.0.0.1:8765/agent/status` shows the agent under `agents`,
   `forwarders.adb.bound: true` (and `serial.bound: true` if a COM is set), and
   the enumerated `adb_devices` / `com_ports`.
3. **Web Connection Center** — the web UI's Connections page lists the agent
   card with its devices, and the forwarder rows show *bound*.
4. **adb** — on the hub: `alb devices` lists the board; `alb shell getprop ro.product.model`
   returns it. (Both go `alb → 127.0.0.1:5037 → forwarder → agent → adb server`.)
5. **serial / UART** — `alb uart capture` (or the web UART console) shows live
   serial output; typing in the console reaches the board.
6. **MCP** — from Claude Code / Cursor / Codex pointed at `alb-mcp`:
   `alb_shell` / `alb_devices` / `alb_uart_send` operate the same device.
7. **File transfer** — in the web Files tab, drag a local file onto the
   Workspace pane (uploads to `devices/<serial>/`), then **Push** it to the
   board.

## 4. Troubleshooting

| symptom | layer | check / fix |
|---|---|---|
| status page shows **disconnected** + an error | agent → hub | read `last_error`: `1008` = bad token; connection refused / timeout = hub URL / TLS / firewall |
| status page never loads | agent not running / port taken | is `alb_agent.py` running? try `--status-port` |
| `/agent/status` shows no agents | hub | agent didn't register — recheck token match + hub URL path is `/agent/connect` |
| `forwarders.adb.bound: false` | hub | a local adb server holds `5037` — `adb kill-server` on the hub or set `ALB_ADB_FORWARD_PORT` (the hub log prints the bind error) |
| `alb devices` empty but agent connected | Windows adb | `adb devices` on the Windows host — authorize the USB prompt; the agent only proxies what its local adb sees. A wedged enumeration can be kicked remotely: `curl -X POST http://<hub>:8765/api/agent/adb/restart` (the agent restarts its local adb server and re-reports) |
| adb empty + `/agent/status` shows `adb_conflicts` | Windows adb takeover | a renamed vendor adb build holds the exclusive USB interface — `curl -X POST "http://<hub>:8765/api/agent/adb/restart?kill_conflicts=true"` terminates the suspects (agent-side heuristic) and restarts the server; expect it to recur whenever the vendor tool runs |
| serial silent | hub COM / agent | `ALB_AGENT_SERIAL_COM` set on the **hub**? COM exists on the Windows host? right baud? |
| `SERIAL_PORT_BUSY`, or the agent logs `cannot open COM…: Access is denied` | Windows host | some **other** program on the agent host holds the port (terminal emulator, vendor flashing tool) — close it. Concurrent alb clients are not the cause: they share one channel, and `/agent/status` → `forwarders.serial.readers` shows how many are attached |
| `serial shell` fails while a capture runs | — | fixed (issue #4). If it still happens, the hub is running a build older than the shared-channel fan-out — check `forwarders.serial.readers` exists in `/agent/status` |
| agent connects then drops in a loop | hub | older builds tore the session down on a forwarder bind failure — current builds keep it up and warn; if looping, it's the handshake (token/version) |

## Security

- The agent only ever proxies an **allowlisted** local target
  (`127.0.0.1:5037`) and re-checks every request, so it can't become an open
  proxy on the LAN.
- Each data channel is authenticated by a per-channel secret the hub mints and
  sends only to the owning agent (ADR-053) — a stray process with the shared
  token still can't claim a channel.
- Set a token for any non-loopback hub. The status page binds `127.0.0.1` only
  and never shows the token.
- The dial-back sends both the token and the per-channel secret as **headers**
  (`x-alb-token` / `x-alb-csecret`, see `docs/web-api.md`), not query params,
  so they never reach the access log. **If you ran a build from before this
  change, treat the token as exposed** — it was written in clear text to the
  hub's log on every channel open. Rotate it in all three places at once:
  the hub's env file, the agent's `agent.conf`, and any staging copy of the
  agent; then restart the hub and the agent.
