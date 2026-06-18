# Device agent (dial-home)

A small, headless agent that lets a Linux **alb hub** reach an Android device
that is physically attached to *this* machine (USB for adb, serial for UART).

It dials **out** to the hub over a single WebSocket — so there is **no inbound
port, no SSH, and no third-party terminal** to configure. The hub then exposes
the device locally (e.g. `127.0.0.1:5037` for adb) so the LLM (via MCP) and
humans (via the web UI) can debug it, all driven from the Linux side.

## Install

```
pip install -r requirements.txt
```

(Only dependency is `websockets`. Python 3.11+.)

## Run

```
python alb_agent.py --hub-url wss://<hub>/agent/connect --token <token>
```

- `--hub-url` — the hub's signaling endpoint (`/agent/connect`).
- `--token`   — must match `ALB_AGENT_TOKEN` configured on the hub. Omit only
  for a fully local, no-auth dev setup.
- `--name`    — a human-readable label shown in the hub's device list.
- `--agent-id`— a stable id (defaults to a random one each run).

Keep the process running (it auto-reconnects with backoff if the link drops).
On Windows it can be made a background service / tray app later; for now run it
in a terminal or via Task Scheduler.

## What it does (P0)

- Maintains the signaling connection (hello + heartbeat + auto-reconnect).
- On request from the hub, bridges the local **adb server** (`127.0.0.1:5037`)
  to the hub over a per-request data connection.
- Reports attached adb devices on request (`adb devices`).

Serial/UART channels, COM enumeration, and a status tray come in later phases.

## Security

- The agent only ever proxies an **allowlisted** local target
  (`127.0.0.1:5037`); it re-checks every request and refuses anything else, so
  it can never be turned into an open proxy on your LAN.
- Traffic rides the same authenticated `wss://` connection as the hub API.
- Configure a token (`--token` / `ALB_AGENT_TOKEN`) for any non-loopback hub.
