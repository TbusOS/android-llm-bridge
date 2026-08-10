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
import csv
import hashlib
import json
import logging
import os
import re
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
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

# Dial-back credential headers (ADR-055) — mirror alb.remote.protocol. They
# used to ride the query string, where the hub's access log recorded both in
# clear text on every channel open. Headers are not logged.
TOKEN_HEADER = "x-alb-token"
CSECRET_HEADER = "x-alb-csecret"

# Flash job limits (ADR-056). A partition name becomes an argv element, so
# the shape check is a security control, not tidiness: a name carrying a
# path separator, a space or a leading dash would either escape into another
# argument or point fastboot somewhere else entirely.
_PARTITION_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")
_FASTBOOT_REBOOT_TARGETS = frozenset({"bootloader", "fastboot", "recovery"})
_FLASH_MAX_BYTES = 2 * 1024 * 1024 * 1024
# Tool output is echoed back in the terminal frame; cap it so a chatty or
# looping tool cannot turn one job into an unbounded message.
_JOB_OUTPUT_CAP = 64 * 1024
# fastboot BLOCKS waiting for a device when none is in fastboot mode
# ("< waiting for any device >"). Without a ceiling one such command holds
# the single-job lock until the hub's 30-minute timeout — a bench taken out
# of service by a command that was never going to succeed. Per-op because
# the right patience differs: a query should answer now, a partition write
# legitimately takes minutes.
_FASTBOOT_TIMEOUT_S = {"devices": 20.0, "reboot": 60.0, "flash": 900.0}
_WAITING_MARKER = "waiting for any device"

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
    adb_conflicts: list[str] = field(default_factory=list)
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

    def set_adb_conflicts(self, conflicts: list[str]) -> None:
        with self._lock:
            self.adb_conflicts = list(conflicts)

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
                "web_ui": _web_ui_url(self.hub_url),
                "connected": self.connected,
                "uptime_s": uptime,
                "connected_for_s": connected_for,
                "reconnects": self.reconnects,
                "last_error": self.last_error,
                "active_channels": channels,
                "channels_total": self._channels_total,
                "adb_devices": list(self.adb_devices),
                "adb_conflicts": list(self.adb_conflicts),
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
    web_ui = esc(snap["web_ui"])
    rows = [
        ("hub", esc(snap["hub_url"])),
        # the one clickable row — so nobody has to ask where the console is
        ("web console", f'<a href="{web_ui}">{web_ui}</a>' if web_ui else "—"),
        ("agent", f"{esc(snap['name'])} · {esc(snap['agent_id'][:8])}"),
        ("uptime", f"{snap['uptime_s']}s"),
        ("connected for", f"{snap['connected_for_s']}s" if up else "—"),
        ("reconnects", str(snap["reconnects"])),
        ("last error", esc(snap["last_error"]) or "—"),
        ("adb devices", esc(", ".join(snap["adb_devices"])) or "—"),
        ("adb conflicts", esc(", ".join(snap["adb_conflicts"])) or "—"),
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
    # caps is how the hub answers "can this bench flash?" without sending a
    # job and waiting for it to time out (ADR-056 §决定 7). Advertise
    # `fastboot` only when the executable is actually resolvable here.
    caps = ["adb"]
    if _fastboot_path():
        caps.append("fastboot")
    return _frame(
        "hello",
        agent_id=agent_id,
        name=name,
        agent_version=PROTOCOL_VERSION,
        caps=caps,
        token=token,
    )


def _web_ui_url(hub_url: str) -> str:
    """Best-effort URL of the hub's web console, derived from the signaling
    URL (ws://host:port/agent/connect → http://host:port/app/). Shown at
    startup and on the status page so the operator never has to ask where
    the web UI lives."""
    parts = urlsplit(hub_url)
    if not parts.netloc:
        return ""
    scheme = "https" if parts.scheme == "wss" else "http"
    return urlunsplit((scheme, parts.netloc, "/app/", "", ""))


def _channel_url(hub_url: str, cid: str) -> str:
    """Derive wss://<host>/agent/channel?cid=... from the hub URL.

    Only the cid rides the query string: it is a routing key, not a
    credential, and keeping it visible is what makes a channel traceable in
    the hub log. The token and the per-channel secret go in headers instead
    (_channel_headers) — see ADR-055."""
    parts = urlsplit(hub_url)
    return urlunsplit((parts.scheme, parts.netloc, "/agent/channel", urlencode({"cid": cid}), ""))


def _channel_headers(token: str | None, csecret: str | None) -> dict[str, str]:
    """Dial-back credentials as HTTP headers (ADR-055). The csecret is the
    hub-minted per-channel secret carried by the open_channel frame; the hub
    verifies it on dial-back (DEBT-084)."""
    headers: dict[str, str] = {}
    if token:
        headers[TOKEN_HEADER] = token
    if csecret:
        headers[CSECRET_HEADER] = csecret
    return headers


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


async def _report_channel_error(ws: Any, cid: str, reason: str) -> None:
    """Tell the hub we could not open the channel it asked for.

    Without this the hub learns nothing and just waits out its dial-back
    timeout, holding a local socket that will never carry bytes — and for
    serial, silently swallowing whatever the caller wrote into it (issue #4).
    Best-effort: a failure to report must never take down the session.
    """
    if not cid:
        return
    with contextlib.suppress(Exception):
        await ws.send(_frame("channel_error", cid=cid, reason=reason))


async def _handle_open_channel(
    hub_url: str, token: str | None, frame: dict[str, Any], ws: Any = None
) -> None:
    cid = str(frame.get("cid") or "")
    ctype = frame.get("channel_type")
    if not cid:
        return
    if ctype == "tcp":
        await _handle_tcp_channel(hub_url, token, frame, ws)
    elif ctype == "serial":
        await _handle_serial_channel(hub_url, token, frame, ws)
    elif ctype == "job":
        await _handle_job_channel(hub_url, token, frame, ws)
    else:
        _log.warning("unsupported channel type %r", ctype)
        await _report_channel_error(ws, cid, f"unsupported channel type {ctype!r}")


async def _handle_tcp_channel(
    hub_url: str, token: str | None, frame: dict[str, Any], ws: Any = None
) -> None:
    cid = str(frame.get("cid") or "")
    params = frame.get("params") or {}
    target = str(params.get("target") or "")
    # Re-check the target against our OWN allowlist — do not trust the hub.
    if target not in ALLOWED_TCP_TARGETS:
        _log.warning("rejected channel target %r (not allowlisted)", target)
        await _report_channel_error(ws, cid, f"target {target!r} not allowlisted on the agent")
        return

    csecret = str(frame.get("csecret") or "")
    host, _, port_s = target.partition(":")
    try:
        port = int(port_s)
    except ValueError:
        _log.warning("bad target port %r", target)
        await _report_channel_error(ws, cid, f"bad target port in {target!r}")
        return

    # DAEMON channel (ADR-052): a single attempt. No retry of the data channel.
    try:
        reader, writer = await asyncio.open_connection(host, port)
    except OSError as e:
        _log.warning("channel %s: cannot reach %s: %s", cid[:8], target, e)
        await _report_channel_error(ws, cid, f"cannot reach {target}: {e}")
        return

    url = _channel_url(hub_url, cid)
    headers = _channel_headers(token, csecret)
    try:
        async with ws_connect(url, max_size=None, additional_headers=headers) as data_ws:
            _STATUS.channel_opened(cid, "adb", target)
            # Log the SUCCESS, not only the failure (ADR-057). Until
            # 2026-08-10 this path updated the status page and wrote nothing,
            # while the serial path logged "opened COM27". So a log with no
            # adb lines in it could not distinguish "the hub never asked" from
            # "it asked and it worked" — and absence of errors got read as
            # absence of traffic. A working tunnel was worked around for a day
            # on that reading. The status page only shows *now*; the log is
            # what gets consulted about an hour ago.
            # Same shape as the serial line above it, so one grep finds both.
            _log.info("adb channel %s: opened -> %s", cid[:8], target)
            await _bridge(reader, writer, data_ws)
    except Exception as e:
        _log.warning("channel %s ended: %s", cid[:8], e)
    finally:
        _STATUS.channel_closed(cid)
        with contextlib.suppress(Exception):
            writer.close()
            await writer.wait_closed()


async def _handle_serial_channel(
    hub_url: str, token: str | None, frame: dict[str, Any], ws: Any = None
) -> None:
    cid = str(frame.get("cid") or "")
    csecret = str(frame.get("csecret") or "")
    params = frame.get("params") or {}
    com = str(params.get("com") or "")
    baud = int(params.get("baud") or 115200)
    if not com:
        _log.warning("serial channel %s: no COM specified", cid[:8])
        await _report_channel_error(ws, cid, "no COM port specified in open_channel params")
        return
    try:
        import serial  # pyserial
    except ImportError:
        _log.error("pyserial not installed; cannot open serial channel (pip install pyserial)")
        await _report_channel_error(ws, cid, "pyserial not installed on the agent host")
        return

    try:
        # write_timeout: a write stuck on flow control would otherwise block
        # its worker thread FOREVER — and on Windows the executor join at
        # shutdown can't be interrupted, leaving the console unkillable.
        ser = serial.Serial(com, baud, timeout=0.05, write_timeout=2)
    except Exception as e:
        # The COM port is EXCLUSIVE. The hub now shares one channel across all
        # its readers, so a failure here means some OTHER program on this host
        # holds the port (a terminal emulator, a vendor flashing tool) — the
        # operator needs to see WHICH, so send the OS message through.
        _log.warning("serial channel %s: cannot open %s @ %s: %s", cid[:8], com, baud, e)
        await _report_channel_error(ws, cid, f"cannot open {com} @ {baud}: {e}")
        return
    _log.info("serial channel %s: opened %s @ %s", cid[:8], com, baud)

    url = _channel_url(hub_url, cid)
    headers = _channel_headers(token, csecret)
    try:
        async with ws_connect(url, max_size=None, additional_headers=headers) as data_ws:
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


# ── job channel: fastboot on this host (ADR-056) ─────────────────────
#
# Mirrors alb.remote.jobframe + the job vocabulary in alb.remote.protocol.
# Frame: kind(1B) + length(4B BE) + payload;  kind = b"C" control JSON /
# b"D" data chunk.
#
# The security shape of this whole section (ADR-056 §决定 5): the hub sends
# STRUCTURED FIELDS — an op name, a partition name, a size, a digest. It
# never sends a command line, a file path or an executable name. Everything
# that ends up in argv is assembled here, from this host's own config. A
# job channel that accepted a command string would be a remote-execution
# back door wearing a flashing-tool costume.

_JOB_HEADER = struct.Struct(">cI")
_JOB_KIND_CONTROL = b"C"
_JOB_KIND_DATA = b"D"
_JOB_MAX_FRAME = 8 * 1024 * 1024

# Only one job may touch the device at a time. Not a queue: a caller whose
# flash is refused should learn that NOW, not sit in a line behind an
# unrelated job it cannot see (ADR-056 §决定 3).
_job_lock = asyncio.Lock()


class _JobProtocolError(Exception):
    """The hub sent a frame this agent cannot interpret."""


def _job_encode(kind: bytes, payload: bytes) -> bytes:
    return _JOB_HEADER.pack(kind, len(payload)) + payload


def _job_control(msg: dict[str, Any]) -> bytes:
    return _job_encode(
        _JOB_KIND_CONTROL, json.dumps(msg, ensure_ascii=False, separators=(",", ":")).encode()
    )


class _JobReader:
    """Reassembles job frames off the data WS."""

    def __init__(self, ws: Any) -> None:
        self._ws = ws
        self._buf = bytearray()

    async def read(self) -> tuple[bytes, bytes] | None:
        header = await self._fill(5, allow_eof=True)
        if header is None:
            return None
        kind, length = _JOB_HEADER.unpack(bytes(header))
        if kind not in (_JOB_KIND_CONTROL, _JOB_KIND_DATA):
            raise _JobProtocolError(f"unknown frame kind {kind!r}")
        if length > _JOB_MAX_FRAME:
            raise _JobProtocolError(f"frame claims {length} bytes, over the cap")
        payload = await self._fill(length, allow_eof=False)
        return kind, bytes(payload or b"")

    async def read_control(self) -> dict[str, Any] | None:
        frame = await self.read()
        if frame is None:
            return None
        kind, payload = frame
        if kind != _JOB_KIND_CONTROL:
            raise _JobProtocolError("expected a control frame, got a data frame")
        msg = json.loads(payload)
        if not isinstance(msg, dict):
            raise _JobProtocolError("control frame must be a JSON object")
        return msg

    async def _fill(self, n: int, *, allow_eof: bool) -> bytearray | None:
        while len(self._buf) < n:
            chunk = await self._ws.recv()
            if isinstance(chunk, str):
                chunk = chunk.encode()
            if not chunk:
                if allow_eof and not self._buf:
                    return None
                raise _JobProtocolError(f"channel closed mid-frame ({len(self._buf)} of {n} bytes)")
            self._buf.extend(chunk)
        out = self._buf[:n]
        del self._buf[:n]
        return out


# Set once at startup from agent.conf / flags. Module-level because a job
# channel is handled far from the arg parsing, and threading the two values
# through every call would be noise.
_FASTBOOT_PATH = ""
_FLASH_PARTITIONS: frozenset[str] = frozenset()


def _resolve_fastboot(configured: str) -> str:
    """Where this host's fastboot lives. Explicit config first, then PATH.

    Returns "" when there is none — which becomes a missing `fastboot`
    capability at hello time, so the hub can tell a caller "this bench
    cannot flash" instead of letting it discover that by timing out
    (ADR-056 §决定 7)."""
    configured = (configured or "").strip()
    if configured:
        return configured if Path(configured).is_file() else ""
    return shutil.which("fastboot") or ""


def _fastboot_path() -> str:
    return _FASTBOOT_PATH


def _partition_allowed(name: str) -> bool:
    """Partition names this agent will pass to fastboot.

    Deliberately a shape check plus an optional allowlist, both evaluated
    HERE. `_PARTITION_RE` alone already stops the dangerous class — a name
    with a path separator, a space, or a leading dash would otherwise turn
    into an extra argv element or a path escape once fastboot parses it."""
    if not _PARTITION_RE.match(name):
        return False
    return True if not _FLASH_PARTITIONS else name in _FLASH_PARTITIONS


async def _handle_job_channel(
    hub_url: str, token: str | None, frame: dict[str, Any], ws: Any = None
) -> None:
    cid = str(frame.get("cid") or "")
    csecret = str(frame.get("csecret") or "")
    fastboot = _fastboot_path()
    if not fastboot:
        await _report_channel_error(ws, cid, "no fastboot executable on this agent host")
        return
    if _job_lock.locked():
        # Refuse immediately rather than dial back and then block: the caller
        # is holding a device it thinks is about to be written.
        await _report_channel_error(ws, cid, "another flash job is already running")
        return

    url = _channel_url(hub_url, cid)
    headers = _channel_headers(token, csecret)
    try:
        async with ws_connect(url, max_size=None, additional_headers=headers) as data_ws:
            _STATUS.channel_opened(cid, "job", "fastboot")
            async with _job_lock:
                await _run_job(data_ws, fastboot)
    except Exception as e:
        _log.warning("job channel %s ended: %s", cid[:8], e)
    finally:
        _STATUS.channel_closed(cid)


async def _run_job(data_ws: Any, fastboot: str) -> None:
    reader = _JobReader(data_ws)
    try:
        req = await reader.read_control()
    except (_JobProtocolError, ValueError) as e:
        await _job_fail(data_ws, f"bad opening frame: {e}", code="")
        return
    if req is None:
        return
    op = str(req.get("op") or "")
    if op == "flash":
        await _job_flash(data_ws, reader, fastboot, req)
    elif op == "reboot":
        await _job_reboot(data_ws, fastboot, str(req.get("target") or ""))
    elif op == "devices":
        await _job_devices(data_ws, fastboot)
    else:
        await _job_fail(data_ws, f"unsupported job op {op!r}", code="")


async def _job_fail(data_ws: Any, error: str, *, code: str, rc: int = -1) -> None:
    with contextlib.suppress(Exception):
        await data_ws.send(
            _job_control(
                {
                    "ev": "done",
                    "ok": False,
                    "rc": rc,
                    "stdout": "",
                    "stderr": "",
                    "error": error,
                    "code": code,
                }
            )
        )


async def _job_progress(data_ws: Any, phase: str, done: int, total: int, text: str = "") -> None:
    with contextlib.suppress(Exception):
        await data_ws.send(
            _job_control(
                {"ev": "progress", "phase": phase, "done": done, "total": total, "text": text}
            )
        )


async def _device_present(fastboot: str) -> bool:
    """Is a board actually in fastboot right now?

    Everything else here depends on this. `fastboot flash` and
    `fastboot reboot` BLOCK waiting for a device when none is present —
    they print "< waiting for any device >" and sit there. Without this
    check a bench where the board is still in Android answers every request
    by holding the single-job lock until something times out, and the
    operator sees a hang instead of the one-line truth: the board is not in
    fastboot. Costs one ~20 ms query to turn that into an instant answer.
    """
    proc = await asyncio.create_subprocess_exec(
        fastboot, "devices", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), _FASTBOOT_TIMEOUT_S["devices"])
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError, OSError):
            proc.kill()
        return False
    return bool(out.decode("utf-8", errors="replace").strip())


async def _job_flash(data_ws: Any, reader: _JobReader, fastboot: str, req: dict[str, Any]) -> None:
    partition = str(req.get("partition") or "")
    size = int(req.get("size") or 0)
    want_digest = str(req.get("sha256") or "").lower()

    if not _partition_allowed(partition):
        await _job_fail(
            data_ws,
            f"partition {partition!r} rejected by this agent",
            code="FLASH_PARTITION_REJECTED",
        )
        return
    if size <= 0 or size > _FLASH_MAX_BYTES:
        await _job_fail(data_ws, f"image size {size} out of range", code="FLASH_IMAGE_CORRUPT")
        return

    # Check BEFORE accepting the image: streaming megabytes to a bench whose
    # board is not even in fastboot wastes the tunnel and then fails anyway.
    if not await _device_present(fastboot):
        await _job_fail(
            data_ws,
            "no device is in fastboot on this host — nothing was transferred",
            code="FASTBOOT_NO_DEVICE",
        )
        return

    await data_ws.send(_job_control({"ev": "accepted", "detail": f"receiving {size} bytes"}))

    # Receive to a temp file under this host's own temp dir. The hub never
    # names a path; if it could, "flash this file" would become "write
    # anywhere on the agent host".
    digest = hashlib.sha256()
    received = 0
    tmp_dir = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="alb-flash-"))
    tmp_img = tmp_dir / "image.bin"
    try:
        # Every disk touch here goes through a worker thread. Writing a large
        # image synchronously would stall this event loop for seconds at a
        # time — and the serial pump shares that loop, so the UART view that
        # is meant to run ALONGSIDE the flash would freeze exactly when it
        # matters most (ADR-056 §决定 4).
        fh = await asyncio.to_thread(open, tmp_img, "wb")
        try:
            while received < size:
                frame = await reader.read()
                if frame is None:
                    await _job_fail(
                        data_ws,
                        f"transfer ended early: {received} of {size} bytes",
                        code="FLASH_IMAGE_CORRUPT",
                    )
                    return
                kind, payload = frame
                if kind != _JOB_KIND_DATA:
                    await _job_fail(data_ws, "expected image data, got a control frame", code="")
                    return
                if not payload:
                    break
                if received + len(payload) > size:
                    await _job_fail(
                        data_ws, "sender exceeded the declared size", code="FLASH_IMAGE_CORRUPT"
                    )
                    return
                await asyncio.to_thread(fh.write, payload)
                digest.update(payload)
                received += len(payload)
                await _job_progress(data_ws, "transfer", received, size)
        finally:
            await asyncio.to_thread(fh.close)

        # ADR-056 §决定 6: verify BEFORE touching the device. This is the last
        # moment when "is the image intact" is a free question; afterwards the
        # only way to find out is a board that will not boot.
        if received != size or digest.hexdigest() != want_digest:
            await _job_fail(
                data_ws,
                f"image digest mismatch ({received}/{size} bytes received) — nothing was written",
                code="FLASH_IMAGE_CORRUPT",
            )
            return

        await _job_progress(data_ws, "flash", 0, 0, f"starting fastboot flash {partition}")
        # argv assembled HERE from vetted pieces — see the section header.
        rc, out, err = await _run_fastboot(
            data_ws, [fastboot, "flash", partition, str(tmp_img)], "flash"
        )
        await _job_finish(data_ws, rc, out, err, fail_code="FLASH_FAILED")
    finally:
        # Also off-loop: deleting a multi-gigabyte file is not instant, and
        # this runs on the path back out of a job that may have just failed.
        with contextlib.suppress(Exception):
            await asyncio.to_thread(tmp_img.unlink, missing_ok=True)
        with contextlib.suppress(Exception):
            await asyncio.to_thread(tmp_dir.rmdir)


async def _job_reboot(data_ws: Any, fastboot: str, target: str) -> None:
    """`fastboot reboot [bootloader]` — the way back out of fastboot, which
    is the state alb itself can push a board into."""
    if target and target not in _FASTBOOT_REBOOT_TARGETS:
        await _job_fail(data_ws, f"unsupported reboot target {target!r}", code="")
        return
    if not await _device_present(fastboot):
        await _job_fail(
            data_ws,
            "no device is in fastboot on this host — nothing to reboot",
            code="FASTBOOT_NO_DEVICE",
        )
        return
    await data_ws.send(_job_control({"ev": "accepted", "detail": "rebooting"}))
    argv = [fastboot, "reboot"] + ([target] if target else [])
    rc, out, err = await _run_fastboot(data_ws, argv, "reboot")
    await _job_finish(data_ws, rc, out, err, fail_code="FLASH_FAILED")


async def _job_devices(data_ws: Any, fastboot: str) -> None:
    """`fastboot devices` — the only way to see a board that is in fastboot,
    since it has vanished from adb by then."""
    await data_ws.send(_job_control({"ev": "accepted", "detail": "listing"}))
    rc, out, err = await _run_fastboot(data_ws, [fastboot, "devices"], "devices")
    # An empty listing exits 0 — that is fastboot saying "nothing is here",
    # not "the query worked". Reporting it as success would send the caller
    # on to flash a device that is not in fastboot at all.
    listed = bool(out.strip())
    await _job_finish(
        data_ws,
        rc,
        out,
        err,
        fail_code="FASTBOOT_NO_DEVICE" if rc == 0 else "FLASH_FAILED",
        ok_override=rc == 0 and listed,
        # Say what was observed. The generic fallback ("fastboot failed") is
        # right for a tool that crashed with no output, and useless here —
        # this path knows exactly what happened: the query worked, the list
        # was empty.
        fail_error=(
            "fastboot ran but listed no devices — the board is not in fastboot" if rc == 0 else ""
        ),
    )


async def _run_fastboot(data_ws: Any, argv: list[str], op: str = "") -> tuple[int, str, str]:
    """Run fastboot, relaying its stderr as progress while it works.

    fastboot writes its own progress to stderr, so relaying it is what makes
    a long write visible instead of a frozen bar. Reading it concurrently
    also keeps the pipe from filling and deadlocking the child."""
    _log.info("job: running %s", " ".join(argv[1:]))
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    timeout = _FASTBOOT_TIMEOUT_S.get(op, 300.0)
    out_chunks: list[bytes] = []
    err_chunks: list[bytes] = []

    async def pump(stream: Any, sink: list[bytes], relay: bool) -> None:
        while True:
            line = await stream.readline()
            if not line:
                return
            sink.append(line)
            if relay:
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    await _job_progress(data_ws, "flash", 0, 0, text)

    timed_out = False
    try:
        await asyncio.wait_for(
            asyncio.gather(
                pump(proc.stdout, out_chunks, False),
                pump(proc.stderr, err_chunks, True),
            ),
            timeout,
        )
        rc = await proc.wait()
    except TimeoutError:
        timed_out = True
        # Kill, do not just abandon: an orphaned fastboot keeps holding the
        # USB interface and the next job would fail for a reason that has
        # nothing to do with it.
        with contextlib.suppress(ProcessLookupError, OSError):
            proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()
        rc = -1
    out = b"".join(out_chunks).decode("utf-8", errors="replace")
    err = b"".join(err_chunks).decode("utf-8", errors="replace")
    if timed_out:
        err = (err + f"\n[agent] killed after {timeout:.0f}s").strip()
    return rc, out, err


async def _job_finish(
    data_ws: Any,
    rc: int,
    out: str,
    err: str,
    *,
    fail_code: str,
    ok_override: bool | None = None,
    fail_error: str = "",
) -> None:
    ok = (rc == 0) if ok_override is None else ok_override
    with contextlib.suppress(Exception):
        await data_ws.send(
            _job_control(
                {
                    "ev": "done",
                    "ok": ok,
                    "rc": rc,
                    "stdout": out[-_JOB_OUTPUT_CAP:],
                    "stderr": err[-_JOB_OUTPUT_CAP:],
                    "error": ""
                    if ok
                    else (fail_error or err.strip() or out.strip() or "fastboot failed"),
                    "code": "" if ok else fail_code,
                }
            )
        )


# ── signaling connection ─────────────────────────────────────────────


async def _heartbeat(ws: Any) -> None:
    try:
        while not _shutdown.is_set():
            await asyncio.sleep(HEARTBEAT_INTERVAL_S)
            await ws.send(_frame("heartbeat"))
    except Exception:
        return


# ── adb interface conflicts ──────────────────────────────────────────
# The ADB USB interface is EXCLUSIVE-open: whichever server grabs it first
# blinds every other one. Vendor tool suites often ship a *renamed* adb build
# whose server keeps running after the tool exits — the standard adb then
# reports an empty device list while the driver looks perfectly healthy.
# Detection: "adb" as a STANDALONE token in the process name (vendor renames
# follow the xxx_adb / HD-Adb / adb_server convention) — a plain substring
# match flagged innocent bystanders whose name merely contains the letters
# (AcutaDBCore.exe, real case 2026-07-06), and the SAME list feeds the kill
# path, so precision is a safety property here, not cosmetics.

_ADB_TOKEN = re.compile(r"(?:^|[^a-z0-9])adb(?:[^a-z0-9]|$)")


def _adb_conflicts_from_listing(procs: list[tuple[str, str]]) -> list[str]:
    """procs = (name, pid) pairs → 'name pid=N' for adb-flavoured processes
    that are not the standard adb binary itself."""
    hits: list[str] = []
    for name, pid in procs:
        base = name.lower().removesuffix(".exe")
        if base != "adb" and _ADB_TOKEN.search(base):
            hits.append(f"{name} pid={pid}")
    return hits


def _list_processes() -> list[tuple[str, str]]:
    """Best-effort (name, pid) listing of running processes; [] on failure."""
    windows = sys.platform == "win32"
    cmd = ["tasklist", "/fo", "csv", "/nh"] if windows else ["ps", "-eo", "comm=,pid="]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    procs: list[tuple[str, str]] = []
    if windows:
        for row in csv.reader(out.splitlines()):
            if len(row) >= 2:
                procs.append((row[0], row[1]))
    else:
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                procs.append((parts[0], parts[-1]))
    return procs


def _find_adb_conflicts() -> list[str]:
    return _adb_conflicts_from_listing(_list_processes())


def _kill_adb_conflicts() -> list[str]:
    """Terminate the detected adb-flavoured foreign processes. The hub only
    ever passes a boolean — WHAT matches is decided here by the same fuzzy
    heuristic, so the hub can never name an arbitrary process to kill."""
    killed: list[str] = []
    for entry in _find_adb_conflicts():
        try:
            pid = int(entry.rsplit("pid=", 1)[-1])
        except ValueError:
            continue
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/f", "/pid", str(pid)], capture_output=True, timeout=10
                )
            else:
                os.kill(pid, signal.SIGTERM)
            killed.append(entry)
        except (OSError, subprocess.SubprocessError):
            _log.warning("could not terminate %s", entry)
    return killed


async def _reply_adb_list(ws: Any) -> None:
    devices = await _adb_devices()
    # empty list is the takeover's signature — surface the suspects so the
    # hub (and the local status page) can see WHY instead of just "no devices".
    conflicts = [] if devices else await asyncio.to_thread(_find_adb_conflicts)
    _STATUS.set_adb_devices(devices)
    _STATUS.set_adb_conflicts(conflicts)
    with contextlib.suppress(Exception):
        await ws.send(_frame("adb_list", devices=devices, conflicts=conflicts))


async def _restart_adb_and_report(ws: Any, kill_conflicts: bool = False) -> None:
    """Restart the LOCAL adb server, then re-report devices. Runs here on the
    agent host by design — the hub must never kill an adb server remotely.
    Unsticks the common 'interface enumerated but adb server sees nothing'
    state without anyone walking over to this machine.

    kill_conflicts additionally terminates adb-flavoured foreign processes
    first — a renamed vendor adb holding the exclusive USB interface survives
    a plain server restart, our server just loses the race again."""
    if kill_conflicts:
        killed = await asyncio.to_thread(_kill_adb_conflicts)
        if killed:
            _log.info("terminated adb conflicts on hub request: %s", ", ".join(killed))
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

        def _forget(task: asyncio.Task[None]) -> None:
            """Drop a finished side-task AND retrieve its exception.

            Ctrl-C on Windows raises KeyboardInterrupt inside whatever task step
            the main thread happens to be running — often one of these. Nobody
            awaits them (they are fire-and-forget replies), so an unretrieved
            exception makes asyncio log "Task exception was never retrieved"
            with a full traceback when the task is garbage-collected: a clean
            shutdown that reads as a crash. Retrieving it here marks it handled.
            """
            channels.discard(task)
            if not task.cancelled():
                task.exception()

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
                        _handle_open_channel(args.hub_url, args.token, frame, ws)
                    )
                    channels.add(task)
                    task.add_done_callback(_forget)
                elif verb == "list_adb":
                    # subprocess enumeration must not block the reader loop
                    # (it would delay subsequent open_channel frames) — run it
                    # as its own task.
                    task = asyncio.create_task(_reply_adb_list(ws))
                    channels.add(task)
                    task.add_done_callback(_forget)
                elif verb == "restart_adb":
                    task = asyncio.create_task(
                        _restart_adb_and_report(ws, bool(frame.get("kill_conflicts")))
                    )
                    channels.add(task)
                    task.add_done_callback(_forget)
                elif verb == "list_com":
                    # pyserial enumeration is sync → run as its own task so it
                    # doesn't block the reader loop.
                    task = asyncio.create_task(_reply_com_list(ws))
                    channels.add(task)
                    task.add_done_callback(_forget)
        finally:
            _STATUS.on_disconnected()
            hb.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await hb
            for t in list(channels):
                t.cancel()
            for t in list(channels):
                # BaseException, not Exception: a KeyboardInterrupt that landed
                # in a still-pending task must not escape the teardown loop.
                with contextlib.suppress(BaseException):
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


def _make_sigint_handler() -> Any:
    """Windows fallback handler (loop.add_signal_handler is unavailable
    there): the FIRST Ctrl-C raises KeyboardInterrupt for asyncio.run's
    normal teardown; a SECOND one hard-exits. Without the escape hatch a
    wedged worker thread makes the shutdown's executor join uninterruptible
    on Windows and the console can never be killed from the keyboard."""
    hits = 0

    def _handler(_signum: Any, _frame: Any) -> None:
        nonlocal hits
        hits += 1
        if hits >= 2:
            os._exit(130)
            return  # unreachable in production; keeps the fake-exit test honest
        print("stopping... (Ctrl-C again to force quit)", file=sys.stderr, flush=True)
        # Tell the cooperative loops to wind down too. Without this the four
        # `while not _shutdown.is_set()` loops never see the shutdown at all —
        # everything rides on KeyboardInterrupt unwinding the stack, which only
        # works because it happens to land somewhere unwindable. Runs on the
        # loop's own thread (the handler fires between bytecodes in the main
        # thread), so setting the Event directly is safe here.
        _shutdown.set()
        raise KeyboardInterrupt

    return _handler


def _install_signal_handlers() -> None:
    if sys.platform == "win32":
        # Processes spawned under a CREATE_NEW_PROCESS_GROUP lineage (Task
        # Scheduler, remote shells, IDE-embedded terminals) inherit Windows'
        # "ignore Ctrl+C" console flag — CTRL_C_EVENT is then never delivered
        # at all, no matter how often it's pressed. Clearing the inherited
        # handler restores delivery; a double-clicked console is unaffected.
        # (Field-verified 2026-07-06: injected CTRL_C_EVENT reached the agent
        # only after this call.) Ctrl+Break never had the problem.
        import ctypes

        with contextlib.suppress(Exception):
            ctypes.windll.kernel32.SetConsoleCtrlHandler(None, False)
    loop = asyncio.get_event_loop()
    installed = False
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, ValueError):
            loop.add_signal_handler(sig, _shutdown.set)
            installed = True
    if installed:
        return
    handler = _make_sigint_handler()
    with contextlib.suppress(ValueError, OSError):
        signal.signal(signal.SIGINT, handler)
    # Ctrl+Break gets the same treatment where it exists (Windows-only signal)
    if hasattr(signal, "SIGBREAK"):
        with contextlib.suppress(ValueError, OSError):
            signal.signal(signal.SIGBREAK, handler)


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
    # ADR-056 flashing. Keys here MUST stay in lockstep with the argparse
    # options in _parse_args — an unknown key is a hard error by design, so a
    # flag that exists only on the command line reads to the operator as
    # "this build does not support it" rather than "use --flag instead".
    "fastboot_path": str,
    "flash_partitions": str,
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
    ap.add_argument(
        "--fastboot-path",
        default=None,
        help="fastboot executable (or fastboot_path in agent.conf); "
        "defaults to whatever is on PATH. Absent = this agent reports no "
        "fastboot capability and the hub says so immediately",
    )
    ap.add_argument(
        "--flash-partitions",
        default=None,
        help="comma-separated partition allowlist (or flash_partitions in "
        "agent.conf). Empty = any well-formed name is accepted",
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
    web_ui = _web_ui_url(args.hub_url)
    if web_ui:
        _log.info("hub web console: %s", web_ui)
    # Say it at startup, not at the first failed flash: "no fastboot here" is
    # a five-minute fix on this host and an hour of confusion from the hub.
    if _fastboot_path():
        allow = ",".join(sorted(_FLASH_PARTITIONS)) if _FLASH_PARTITIONS else "(any well-formed)"
        _log.info("fastboot: %s · partitions %s", _fastboot_path(), allow)
    else:
        _log.info(
            "fastboot: not found — this agent will NOT advertise the fastboot "
            "capability (set fastboot_path in agent.conf to enable flashing)"
        )
    ports = _enumerate_com()
    _log.info(
        "serial ports here: %s",
        ", ".join(p["port"] for p in ports) if ports else "NONE",
    )


def _apply_flash_config(args: argparse.Namespace) -> None:
    """Resolve the flash settings once, before anything can use them.

    Runs before the first `hello`, because the resolved path is what decides
    whether this agent claims the `fastboot` capability at all."""
    global _FASTBOOT_PATH, _FLASH_PARTITIONS
    _FASTBOOT_PATH = _resolve_fastboot(getattr(args, "fastboot_path", None) or "")
    raw = (getattr(args, "flash_partitions", None) or "").strip()
    _FLASH_PARTITIONS = frozenset(p.strip() for p in raw.split(",") if p.strip())


def main() -> None:
    args = _parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format=_LOG_FORMAT,
    )
    if args.log_file:
        _setup_file_logging(args.log_file)
    _apply_flash_config(args)
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
