"""Standalone dial-home device agent.

Runs on the machine that physically holds the device (typically a Windows
host with the device on USB + serial). It dials OUT to the Linux hub over a
single WebSocket — no inbound port, no SSH, no third-party terminal — and lets
the hub reach the local adb server and the device's serial / UART port.

Design: see the hub's ADR-050/051/052. This file is intentionally standalone:
it depends only on the stdlib + `websockets` (+ `pyserial` for serial channels),
NOT the alb package, so it can be dropped onto a bare host. The wire constants
below mirror the hub's `alb.remote.protocol` and MUST stay in lockstep with it.

Usage (config file — the normal path):
    copy agent.conf.example agent.conf   # fill in hub_url + token
    run-agent.bat                        # or: python alb_agent.py

Usage (flags — override any config value):
    pip install -r requirements.txt   # websockets + pyserial
    python alb_agent.py --hub-url wss://<hub>/agent/connect --token <token>

Precedence: command line > agent.conf > built-in defaults. The config file
lives next to this script by default (`--config` points elsewhere).

The agent maintains the signaling connection (auto-reconnect with backoff) and,
on each `open_channel` from the hub, dials back a separate data connection and
bridges raw bytes to the requested local target.

A localhost-only status page (http://127.0.0.1:8731 by default, --status-port 0
to disable) shows connection state, active channels, enumerated devices, and the
last error — so the operator can diagnose a failed dial-home locally, without
logging into the hub. The token is never shown there.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import signal
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

try:
    import websockets
    from websockets.asyncio.client import connect as ws_connect
except ImportError:
    sys.exit("websockets not installed.  Run: pip install websockets")

_log = logging.getLogger("alb-agent")

PROTOCOL_VERSION = 1

# Must match the hub's allowlist (ADR-050 §6). The agent re-checks the target
# itself and NEVER trusts an arbitrary host:port pushed by the hub — otherwise
# it would become an open proxy on the local LAN.
ADB_TARGET = "127.0.0.1:5037"
ALLOWED_TCP_TARGETS = frozenset({ADB_TARGET})

HEARTBEAT_INTERVAL_S = 20.0
RECONNECT_BACKOFF_S = (1.0, 2.0, 5.0, 10.0)
_CHUNK = 65536
DEFAULT_STATUS_PORT = 8731

_shutdown = asyncio.Event()


# ── local status surface ─────────────────────────────────────────────
# A tiny localhost-only HTTP page so the operator on the device's host can see
# whether the agent reached the hub WITHOUT logging into the hub. Crucial when
# the dial-home FAILS: the hub never sees the agent, so the hub's Connection
# Center cannot explain "wrong token / hub unreachable" — this page can. The
# token is NEVER included in the snapshot.


@dataclass
class _ChannelInfo:
    kind: str  # "adb" | "serial"
    target: str  # "127.0.0.1:5037" | "COM27 @ 1500000"
    opened_monotonic: float


@dataclass
class AgentStatus:
    """Live agent state, read by the status HTTP server (separate thread) and
    written by the asyncio session/channel code — guarded by a lock."""

    hub_url: str = ""
    agent_id: str = ""
    name: str = ""
    started_monotonic: float = 0.0
    connected: bool = False
    connected_since_monotonic: float = 0.0
    reconnects: int = 0
    last_error: str = ""
    adb_devices: list[str] = field(default_factory=list)
    com_ports: list[dict[str, str]] = field(default_factory=list)
    _channels: dict[str, _ChannelInfo] = field(default_factory=dict)
    _channels_total: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def init(self, *, hub_url: str, agent_id: str, name: str) -> None:
        """Set the static identity fields. Call ONCE at startup — it does NOT
        reset the runtime counters (reconnects / channels_total), so reusing it
        mid-run would skew uptime vs those counters."""
        with self._lock:
            self.hub_url = hub_url
            self.agent_id = agent_id
            self.name = name
            self.started_monotonic = time.monotonic()

    def on_connected(self) -> None:
        with self._lock:
            self.connected = True
            self.connected_since_monotonic = time.monotonic()
            self.last_error = ""

    def on_disconnected(self, error: str = "") -> None:
        with self._lock:
            self.connected = False
            self.connected_since_monotonic = 0.0
            if error:
                self.last_error = error
            self._channels.clear()

    def on_reconnect_scheduled(self, error: str = "") -> None:
        with self._lock:
            self.reconnects += 1
            if error:
                self.last_error = error

    def channel_opened(self, cid: str, kind: str, target: str) -> None:
        with self._lock:
            self._channels[cid] = _ChannelInfo(kind, target, time.monotonic())
            self._channels_total += 1

    def channel_closed(self, cid: str) -> None:
        with self._lock:
            self._channels.pop(cid, None)

    def set_adb_devices(self, devices: list[str]) -> None:
        with self._lock:
            self.adb_devices = list(devices)

    def set_com_ports(self, ports: list[dict[str, str]]) -> None:
        with self._lock:
            self.com_ports = list(ports)

    def snapshot(self) -> dict[str, Any]:
        """JSON-safe view for /status.json + the HTML page. Never includes the
        token. Durations are seconds (ints) computed from monotonic clocks."""
        now = time.monotonic()
        with self._lock:
            uptime = int(now - self.started_monotonic) if self.started_monotonic else 0
            connected_for = (
                int(now - self.connected_since_monotonic)
                if self.connected and self.connected_since_monotonic
                else 0
            )
            channels = [
                {
                    "cid": cid[:8],
                    "kind": c.kind,
                    "target": c.target,
                    "open_for_s": int(now - c.opened_monotonic),
                }
                for cid, c in self._channels.items()
            ]
            return {
                "agent_id": self.agent_id,
                "name": self.name,
                "hub_url": self.hub_url,
                "connected": self.connected,
                "uptime_s": uptime,
                "connected_for_s": connected_for,
                "reconnects": self.reconnects,
                "last_error": self.last_error,
                "active_channels": channels,
                "channels_total": self._channels_total,
                "adb_devices": list(self.adb_devices),
                "com_ports": list(self.com_ports),
            }


_STATUS = AgentStatus()


def _render_status_html(snap: dict[str, Any]) -> str:
    """Render the status snapshot as a small self-contained, auto-refreshing
    HTML page. Brand-neutral, no external assets, no token."""

    def esc(s: object) -> str:
        return (
            str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    up = snap["connected"]
    dot = "#1f9d57" if up else "#c0392b"
    state = "connected" if up else "disconnected"
    rows = [
        ("hub", esc(snap["hub_url"])),
        ("agent", f"{esc(snap['name'])} · {esc(snap['agent_id'][:8])}"),
        ("uptime", f"{snap['uptime_s']}s"),
        ("connected for", f"{snap['connected_for_s']}s" if up else "—"),
        ("reconnects", str(snap["reconnects"])),
        ("last error", esc(snap["last_error"]) or "—"),
        ("adb devices", esc(", ".join(snap["adb_devices"])) or "—"),
        (
            "serial ports",
            esc(", ".join(p.get("port", "") for p in snap["com_ports"])) or "—",
        ),
        ("channels (total)", str(snap["channels_total"])),
    ]
    info = "\n".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in rows)
    if snap["active_channels"]:
        chan_rows = "\n".join(
            f"<tr><td>{esc(c['cid'])}</td><td>{esc(c['kind'])}</td>"
            f"<td>{esc(c['target'])}</td><td>{c['open_for_s']}s</td></tr>"
            for c in snap["active_channels"]
        )
        channels = (
            "<h2>Active channels</h2><table class=ch>"
            "<tr><th>cid</th><th>kind</th><th>target</th><th>open</th></tr>"
            f"{chan_rows}</table>"
        )
    else:
        channels = "<h2>Active channels</h2><p class=muted>none</p>"
    return f"""<!doctype html>
<html lang=en><head><meta charset=utf-8>
<meta http-equiv=refresh content=3>
<title>alb device agent — {state}</title>
<style>
 body{{font:14px/1.5 system-ui,sans-serif;margin:0;background:#faf9f5;color:#2b2a27}}
 .wrap{{max-width:680px;margin:0 auto;padding:24px}}
 h1{{font-size:18px;display:flex;align-items:center;gap:8px;margin:0 0 16px}}
 .dot{{width:11px;height:11px;border-radius:50%;background:{dot};display:inline-block}}
 table{{border-collapse:collapse;width:100%;background:#fff;border:1px solid #e8e3d9;border-radius:8px;overflow:hidden}}
 th,td{{text-align:left;padding:7px 12px;border-bottom:1px solid #f0ece3;font-variant-numeric:tabular-nums}}
 tr:last-child th,tr:last-child td{{border-bottom:0}}
 table.info th{{width:140px;color:#6b675f;font-weight:600}}
 table.ch th{{color:#6b675f;font-weight:600;background:#f7f5f0}}
 h2{{font-size:13px;text-transform:uppercase;letter-spacing:.05em;color:#6b675f;margin:20px 0 8px}}
 .muted{{color:#9b968c}}
 code{{font-family:ui-monospace,monospace}}
</style></head><body><div class=wrap>
<h1><span class=dot></span> alb device agent — {state}</h1>
<table class=info>{info}</table>
{channels}
</div></body></html>"""


class _StatusHandler(BaseHTTPRequestHandler):
    def log_message(self, *_a: Any) -> None:  # silence per-request stderr noise
        return

    def _send(self, code: int, ctype: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        # client closed mid-response (common with the 3s auto-refresh) — drop it.
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            self.wfile.write(body)

    def do_GET(self) -> None:  # stdlib BaseHTTPRequestHandler API name
        if self.path.startswith("/status.json"):
            body = json.dumps(_STATUS.snapshot()).encode("utf-8")
            self._send(200, "application/json", body)
        elif self.path == "/" or self.path.startswith("/?"):
            body = _render_status_html(_STATUS.snapshot()).encode("utf-8")
            self._send(200, "text/html; charset=utf-8", body)
        else:
            self._send(404, "text/plain; charset=utf-8", b"not found")


def _start_status_server(host: str, port: int) -> ThreadingHTTPServer | None:
    """Start the localhost status server in a daemon thread. Returns None (with
    a warning) if the bind fails — the agent must keep running regardless.

    We wait until serve_forever() is actually running before returning, because
    BaseServer.shutdown() deadlocks if called before serve_forever() has started
    (CPython docs). The worst path that exposes this — a bad token rejected at
    handshake — returns from _main_loop immediately and hits the shutdown."""
    try:
        httpd = ThreadingHTTPServer((host, port), _StatusHandler)
    # OverflowError: CPython raises it (not OSError) for a port outside
    # 0-65535 — reachable via --status-port, and this must not kill the agent.
    except (OSError, OverflowError) as e:
        _log.warning("status page disabled — cannot bind %s:%d (%s)", host, port, e)
        return None
    started = threading.Event()

    def _serve() -> None:
        started.set()
        httpd.serve_forever()

    threading.Thread(target=_serve, name="alb-agent-status", daemon=True).start()
    started.wait(timeout=2.0)  # serve_forever is (about to be) looping → shutdown is safe
    _log.info("status page on http://%s:%d", host, port)
    return httpd


class _HandshakeRejected(Exception):
    """The hub refused the handshake (bad token / version) — fatal, no retry."""


# ── wire helpers (mirror alb.remote.protocol) ────────────────────────


def _frame(verb: str, **fields: Any) -> str:
    return json.dumps({"v": PROTOCOL_VERSION, "verb": verb, **fields}, ensure_ascii=False)


def _hello(agent_id: str, name: str, token: str | None) -> str:
    return _frame(
        "hello",
        agent_id=agent_id,
        name=name,
        agent_version=PROTOCOL_VERSION,
        caps=["adb"],
        token=token,
    )


def _channel_url(hub_url: str, cid: str, token: str | None, csecret: str | None) -> str:
    """Derive wss://<host>/agent/channel?cid=...&token=...&csecret=... from the
    hub URL. The csecret is the hub-minted per-channel secret carried by the
    open_channel frame; the hub verifies it on dial-back (DEBT-084)."""
    parts = urlsplit(hub_url)
    query = {"cid": cid}
    if token:
        query["token"] = token
    if csecret:
        query["csecret"] = csecret
    return urlunsplit((parts.scheme, parts.netloc, "/agent/channel", urlencode(query), ""))


# ── adb device enumeration (best-effort) ─────────────────────────────


async def _adb_devices() -> list[str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "adb",
            "devices",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
    except (FileNotFoundError, OSError) as e:
        _log.warning("adb devices failed: %s", e)
        return []
    serials: list[str] = []
    for line in out.decode("utf-8", errors="replace").splitlines()[1:]:
        line = line.strip()
        if line and "\t" in line:
            serials.append(line.split("\t", 1)[0])
    return serials


# ── data channel: dial back + bridge raw bytes ───────────────────────


async def _handle_open_channel(hub_url: str, token: str | None, frame: dict[str, Any]) -> None:
    cid = str(frame.get("cid") or "")
    ctype = frame.get("channel_type")
    if not cid:
        return
    if ctype == "tcp":
        await _handle_tcp_channel(hub_url, token, frame)
    elif ctype == "serial":
        await _handle_serial_channel(hub_url, token, frame)
    else:
        _log.warning("unsupported channel type %r", ctype)


async def _handle_tcp_channel(hub_url: str, token: str | None, frame: dict[str, Any]) -> None:
    cid = str(frame.get("cid") or "")
    params = frame.get("params") or {}
    target = str(params.get("target") or "")
    # Re-check the target against our OWN allowlist — do not trust the hub.
    if target not in ALLOWED_TCP_TARGETS:
        _log.warning("rejected channel target %r (not allowlisted)", target)
        return

    csecret = str(frame.get("csecret") or "")
    host, _, port_s = target.partition(":")
    try:
        port = int(port_s)
    except ValueError:
        _log.warning("bad target port %r", target)
        return

    # DAEMON channel (ADR-052): a single attempt. No retry of the data channel.
    try:
        reader, writer = await asyncio.open_connection(host, port)
    except OSError as e:
        _log.warning("channel %s: cannot reach %s: %s", cid[:8], target, e)
        return

    url = _channel_url(hub_url, cid, token, csecret)
    try:
        async with ws_connect(url, max_size=None) as data_ws:
            _STATUS.channel_opened(cid, "adb", target)
            await _bridge(reader, writer, data_ws)
    except Exception as e:
        _log.warning("channel %s ended: %s", cid[:8], e)
    finally:
        _STATUS.channel_closed(cid)
        with contextlib.suppress(Exception):
            writer.close()
            await writer.wait_closed()


async def _handle_serial_channel(hub_url: str, token: str | None, frame: dict[str, Any]) -> None:
    cid = str(frame.get("cid") or "")
    csecret = str(frame.get("csecret") or "")
    params = frame.get("params") or {}
    com = str(params.get("com") or "")
    baud = int(params.get("baud") or 115200)
    if not com:
        _log.warning("serial channel %s: no COM specified", cid[:8])
        return
    try:
        import serial  # pyserial
    except ImportError:
        _log.error("pyserial not installed; cannot open serial channel (pip install pyserial)")
        return

    try:
        ser = serial.Serial(com, baud, timeout=0.05)
    except Exception as e:
        _log.warning("serial channel %s: cannot open %s @ %s: %s", cid[:8], com, baud, e)
        return
    _log.info("serial channel %s: opened %s @ %s", cid[:8], com, baud)

    url = _channel_url(hub_url, cid, token, csecret)
    try:
        async with ws_connect(url, max_size=None) as data_ws:
            _STATUS.channel_opened(cid, "serial", f"{com} @ {baud}")
            await _bridge_serial(ser, data_ws)
    except Exception as e:
        _log.warning("serial channel %s ended: %s", cid[:8], e)
    finally:
        _STATUS.channel_closed(cid)
        with contextlib.suppress(Exception):
            ser.close()


async def _bridge_serial(ser: Any, data_ws: Any) -> None:
    """Shuttle raw bytes between a (blocking) pyserial port and the data WS.

    pyserial is synchronous, so reads/writes run in a worker thread. UART is
    <200 KB/s, so the per-read thread hop is negligible."""

    async def com_to_ws() -> None:
        try:
            while not _shutdown.is_set():
                data = await asyncio.to_thread(ser.read, _CHUNK)
                if data:
                    await data_ws.send(data)
        except Exception:
            return

    async def ws_to_com() -> None:
        try:
            async for message in data_ws:
                if isinstance(message, str):
                    message = message.encode("utf-8", errors="replace")
                await asyncio.to_thread(ser.write, message)
        except Exception:
            return

    t1 = asyncio.create_task(com_to_ws())
    t2 = asyncio.create_task(ws_to_com())
    _done, pending = await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await t


async def _bridge(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, data_ws: Any) -> None:
    """Shuttle raw bytes between the local TCP socket and the data WS."""

    async def local_to_ws() -> None:
        try:
            while not _shutdown.is_set():
                data = await reader.read(_CHUNK)
                if not data:
                    return
                await data_ws.send(data)
        except Exception:
            return

    async def ws_to_local() -> None:
        try:
            async for message in data_ws:
                if isinstance(message, str):
                    message = message.encode("utf-8", errors="replace")
                writer.write(message)
                await writer.drain()
        except Exception:
            return

    t1 = asyncio.create_task(local_to_ws())
    t2 = asyncio.create_task(ws_to_local())
    _done, pending = await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await t


# ── signaling connection ─────────────────────────────────────────────


async def _heartbeat(ws: Any) -> None:
    try:
        while not _shutdown.is_set():
            await asyncio.sleep(HEARTBEAT_INTERVAL_S)
            await ws.send(_frame("heartbeat"))
    except Exception:
        return


async def _reply_adb_list(ws: Any) -> None:
    devices = await _adb_devices()
    _STATUS.set_adb_devices(devices)
    with contextlib.suppress(Exception):
        await ws.send(_frame("adb_list", devices=devices))


async def _restart_adb_and_report(ws: Any) -> None:
    """Restart the LOCAL adb server, then re-report devices. Runs here on the
    agent host by design — the hub must never kill an adb server remotely.
    Unsticks the common 'interface enumerated but adb server sees nothing'
    state without anyone walking over to this machine."""
    for args in (("adb", "kill-server"), ("adb", "start-server")):
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        except (FileNotFoundError, OSError) as e:
            _log.warning("adb restart: %r failed: %s", " ".join(args), e)
            break
    else:
        _log.info("local adb server restarted on hub request")
    await _reply_adb_list(ws)


def _enumerate_com() -> list[dict[str, str]]:
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    return [{"port": p.device, "desc": (p.description or "")} for p in list_ports.comports()]


async def _reply_com_list(ws: Any) -> None:
    ports = await asyncio.to_thread(_enumerate_com)
    _STATUS.set_com_ports(ports)
    with contextlib.suppress(Exception):
        await ws.send(_frame("com_list", ports=ports))


async def _run_session(args: argparse.Namespace) -> None:
    async with ws_connect(args.hub_url) as ws:
        await ws.send(_hello(args.agent_id, args.name, args.token))
        # expect hello_ok; a hub rejection (bad token / bad hello) closes the
        # WS with a policy/protocol code instead of replying — treat that as
        # fatal so a misconfigured agent doesn't reconnect forever.
        try:
            reply = await ws.recv()
        except websockets.exceptions.ConnectionClosed as e:
            if getattr(e, "code", None) in (1002, 1008):
                raise _HandshakeRejected(f"hub rejected handshake (code {e.code})") from e
            raise
        msg = json.loads(reply) if isinstance(reply, str) else {}
        if msg.get("verb") != "hello_ok":
            raise _HandshakeRejected(f"unexpected handshake reply: {msg!r}")
        _log.info("connected to %s as %s", args.hub_url, args.agent_id)
        _STATUS.on_connected()

        hb = asyncio.create_task(_heartbeat(ws))
        channels: set[asyncio.Task[None]] = set()
        try:
            async for raw in ws:
                if isinstance(raw, bytes):
                    continue
                try:
                    frame = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                verb = frame.get("verb")
                if verb == "open_channel":
                    task = asyncio.create_task(
                        _handle_open_channel(args.hub_url, args.token, frame)
                    )
                    channels.add(task)
                    task.add_done_callback(channels.discard)
                elif verb == "list_adb":
                    # subprocess enumeration must not block the reader loop
                    # (it would delay subsequent open_channel frames) — run it
                    # as its own task.
                    task = asyncio.create_task(_reply_adb_list(ws))
                    channels.add(task)
                    task.add_done_callback(channels.discard)
                elif verb == "restart_adb":
                    task = asyncio.create_task(_restart_adb_and_report(ws))
                    channels.add(task)
                    task.add_done_callback(channels.discard)
                elif verb == "list_com":
                    # pyserial enumeration is sync → run as its own task so it
                    # doesn't block the reader loop.
                    task = asyncio.create_task(_reply_com_list(ws))
                    channels.add(task)
                    task.add_done_callback(channels.discard)
        finally:
            _STATUS.on_disconnected()
            hb.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await hb
            for t in list(channels):
                t.cancel()
            for t in list(channels):
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t


async def _main_loop(args: argparse.Namespace) -> None:
    attempt = 0
    while not _shutdown.is_set():
        try:
            await _run_session(args)
            attempt = 0  # clean session end → reset backoff
        except _HandshakeRejected as e:
            _log.error("%s — not retrying (check --token / hub version)", e)
            return  # misconfig won't fix itself by reconnecting
        except (OSError, websockets.exceptions.WebSocketException, RuntimeError) as e:
            _log.warning("session ended: %s", e)
            _STATUS.on_disconnected(f"{type(e).__name__}: {e}")
        if _shutdown.is_set():
            break
        delay = RECONNECT_BACKOFF_S[min(attempt, len(RECONNECT_BACKOFF_S) - 1)]
        attempt += 1
        _STATUS.on_reconnect_scheduled()
        _log.info("reconnecting in %.0fs ...", delay)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(_shutdown.wait(), timeout=delay)


def _install_signal_handlers() -> None:
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, ValueError):
            loop.add_signal_handler(sig, _shutdown.set)


# ── config file (agent.conf) ─────────────────────────────────────────
# Minimal KEY=VALUE file next to this script, so a permanent bench setup is
# "fill the file, double-click run-agent.bat" — no flags to remember. The
# parser is deliberately hand-rolled (~30 lines, zero deps) and hardened for
# how the file is actually edited on Windows: Notepad's UTF-8 BOM, CRLF line
# endings, values that contain '=' or '#' (tokens), and copy-pasted quotes.

DEFAULT_CONFIG_NAME = "agent.conf"


def _port_number(value: str) -> int:
    port = int(value)  # ValueError propagates to the caller's error message
    if not 0 <= port <= 65535:
        raise ValueError("port out of range 0-65535")
    return port


# key → converter. argparse `type=` does NOT run on defaults injected via
# set_defaults(), so non-string values must be converted here.
_CONFIG_KEYS: dict[str, Any] = {
    "hub_url": str,
    "token": str,
    "name": str,
    "agent_id": str,
    "status_port": _port_number,
    "status_host": str,
    "log_file": str,
}


def _default_config_path() -> Path:
    """agent.conf next to this script — NOT the CWD, which is system32 when
    launched from Task Scheduler."""
    return Path(__file__).resolve().parent / DEFAULT_CONFIG_NAME


def _load_config(path: Path) -> dict[str, Any]:
    """Parse a KEY=VALUE config file into {argparse dest: converted value}.

    Rules (mirrored in agent.conf.example): one KEY=VALUE per line; comments
    on their own line only (a '#' inside a value is kept — tokens may contain
    it); only the first '=' splits key from value (values may contain '=');
    one pair of surrounding quotes is stripped; an empty value means "unset";
    an unknown key is a hard error, so a typo can't be silently ignored."""
    cfg: dict[str, Any] = {}
    # utf-8-sig: strip Notepad's BOM; splitlines(): swallow CRLF.
    text = path.read_text(encoding="utf-8-sig")
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        key = key.strip().lower()
        value = value.strip()
        if not sep or not key:
            sys.exit(f"{path}:{lineno}: expected KEY=VALUE, got: {raw.strip()!r}")
        if key not in _CONFIG_KEYS:
            valid = ", ".join(sorted(_CONFIG_KEYS))
            sys.exit(f"{path}:{lineno}: unknown key {key!r} (valid keys: {valid})")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if not value:
            continue
        try:
            cfg[key] = _CONFIG_KEYS[key](value)
        except ValueError:
            sys.exit(f"{path}:{lineno}: bad value for {key}: {value!r}")
    return cfg


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Two-pass parse: find --config, merge the file's values in as argparse
    defaults, then parse fully — so explicit flags always win over the file."""
    ap = argparse.ArgumentParser(description="alb dial-home device agent")
    ap.add_argument(
        "--config",
        default=None,
        help=f"config file (default: {DEFAULT_CONFIG_NAME} next to this script)",
    )
    ap.add_argument(
        "--hub-url", default=None, help="wss://<hub>/agent/connect (or hub_url in agent.conf)"
    )
    ap.add_argument(
        "--token", default=None, help="agent auth token (matches ALB_AGENT_TOKEN on the hub)"
    )
    ap.add_argument("--name", default="device-agent", help="human-readable agent name")
    ap.add_argument("--agent-id", default=None, help="stable agent id (default: random)")
    ap.add_argument(
        "--status-port",
        type=int,
        default=DEFAULT_STATUS_PORT,
        help=f"local status page port on localhost (default {DEFAULT_STATUS_PORT}; 0 disables)",
    )
    ap.add_argument(
        "--status-host",
        default="127.0.0.1",
        help="status page bind host (default 127.0.0.1 — localhost only)",
    )
    ap.add_argument(
        "--log-file",
        default=None,
        help="also log to this file, rotating at ~5 MB x3 (relative paths are"
        " resolved next to this script; 'none' disables)",
    )
    ap.add_argument("-v", "--verbose", action="store_true")

    pre, _ = ap.parse_known_args(argv)
    config_used: Path | None = None
    if pre.config:
        config_path = Path(pre.config)
        if not config_path.is_file():
            ap.error(f"config file not found: {config_path}")
        ap.set_defaults(**_load_config(config_path))
        config_used = config_path
    elif _default_config_path().is_file():
        ap.set_defaults(**_load_config(_default_config_path()))
        config_used = _default_config_path()

    args = ap.parse_args(argv)
    if not args.hub_url:
        ap.error(f"missing hub URL — set hub_url in {DEFAULT_CONFIG_NAME} or pass --hub-url")
    args.agent_id = args.agent_id or uuid.uuid4().hex
    args.config_used = str(config_used) if config_used else ""
    return args


_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def _setup_file_logging(log_file: str) -> None:
    """Mirror all log output into a rotating file (~5 MB, 3 backups, UTF-8)
    so problems can be analyzed after the console window is gone. Relative
    paths resolve against the script directory, not the CWD — double-click
    and Task Scheduler both start elsewhere. 'none' disables."""
    if log_file.strip().lower() == "none":
        return
    path = Path(log_file)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent / path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    except OSError as e:
        _log.warning("file logging disabled — cannot open %s (%s)", path, e)
        return
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    logging.getLogger().addHandler(handler)
    _log.info("logging to %s", path)


def _log_environment(args: argparse.Namespace) -> None:
    """One startup line with everything a post-mortem needs (never the token)."""
    try:
        import serial

        pyserial_ver = getattr(serial, "__version__", "?")
    except ImportError:
        pyserial_ver = "not installed"
    _log.info(
        "starting: python %s · websockets %s · pyserial %s · %s · config %s",
        sys.version.split()[0],
        getattr(websockets, "__version__", "?"),
        pyserial_ver,
        sys.platform,
        args.config_used or "(none — flags only)",
    )
    _log.info(
        "identity: name=%s agent_id=%s hub=%s",
        args.name,
        args.agent_id,
        args.hub_url,
    )
    ports = _enumerate_com()
    _log.info(
        "serial ports here: %s",
        ", ".join(p["port"] for p in ports) if ports else "NONE",
    )


def main() -> None:
    args = _parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format=_LOG_FORMAT,
    )
    if args.log_file:
        _setup_file_logging(args.log_file)
    _log_environment(args)

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_run(args))


async def _run(args: argparse.Namespace) -> None:
    _install_signal_handlers()
    _STATUS.init(hub_url=args.hub_url, agent_id=args.agent_id, name=args.name)
    httpd = _start_status_server(args.status_host, args.status_port) if args.status_port else None
    try:
        await _main_loop(args)
    finally:
        if httpd is not None:
            with contextlib.suppress(Exception):
                httpd.shutdown()


if __name__ == "__main__":
    main()
