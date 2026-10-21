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
import contextvars
import csv
import functools
import hashlib
import json
import logging
import os
import queue
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
import zipfile
from collections.abc import Awaitable, Callable, Mapping
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
_PARTITION_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}\Z")
_FASTBOOT_REBOOT_TARGETS = frozenset({"bootloader", "fastboot", "recovery"})
# An image is received into a temp file before fastboot sees it (fastboot
# needs a real file: it splits large images into sparse chunks itself). So
# the real ceiling is this host's free temp space, not a fixed number — the
# fixed 2 GiB ceiling this replaced refused a 2.2 GB super image that the
# disk had room for many times over. The reserve keeps one transfer from
# filling the disk the OS and this agent's own log also live on.
_FLASH_TMP_RESERVE_BYTES = 256 * 1024 * 1024
# Tool output is echoed back in the terminal frame; cap it so a chatty or
# looping tool cannot turn one job into an unbounded message.
_JOB_OUTPUT_CAP = 64 * 1024
# Per-op timeouts live on the op table rows (`_fastboot_rows`).
_WAITING_MARKER = "waiting for any device"

HEARTBEAT_INTERVAL_S = 20.0
RECONNECT_BACKOFF_S = (1.0, 2.0, 5.0, 10.0)
_CHUNK = 65536
DEFAULT_STATUS_PORT = 8731

_shutdown = asyncio.Event()


def _file_build(path: Path) -> str:
    """Which code this is: the first 12 hex of the file's sha256.

    `agent_version` in hello is the PROTOCOL number and stays 1 across every
    build, so the hub could not tell an old agent from a new one — the very
    thing a remote update has to confirm (ADR-060). Content-derived, so there
    is no version number for anyone to forget to bump."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    except OSError:
        return ""


_BUILD = _file_build(Path(__file__).resolve())


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
    ops: list[str] = field(default_factory=list)
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

    def set_ops(self, ops: list[str]) -> None:
        with self._lock:
            self.ops = list(ops)

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
                "build": _BUILD,
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
                "ops": list(self.ops),
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
        ("build", esc(snap.get("build", "")) or "—"),
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
        # the op table as reported to the hub — "can this bench erase?" is
        # answered here without logging into the hub
        ("ops", esc(", ".join(snap.get("ops", []))) or "—"),
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
        # Which code is running (ADR-060): what `alb bench agent-update` compares
        # after the restart to tell "updated" from "rolled back".
        build=_BUILD,
        caps=caps,
        # The op table (ADR-059): what this bench can do, each op with its
        # parameter rules, so the hub, the CLI and the web page all drive
        # the same rows and a flash plan can ask "is there an `erase` row"
        # instead of reading a capability string that had to be kept in
        # step by hand. Fastboot rows are present only when the tool is.
        ops=[spec.describe() for spec in _OPS.values()],
        # The allowlist travels with the hello because THIS side is the one
        # that can be sure — the hub cannot know which partitions a given
        # bench permits, and a UI that guesses offers names the agent will
        # refuse. Empty list = no allowlist configured = any well-formed name
        # (the agent still applies its shape check either way), which is a
        # different statement from "no partitions allowed" and must stay
        # distinguishable at the far end.
        flash_partitions=sorted(_FLASH_PARTITIONS),
        # Outcome of the last remote update, if any (ADR-060).
        update=_LAST_UPDATE or None,
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
    if _STOP_REASON:
        # About to exit: opening it now would only cut it a moment later.
        await _report_channel_error(ws, cid, _STOP_REASON)
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


# ── job channel: bench ops on this host (ADR-056 · ADR-059) ──────────
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
#
# ADR-059 collects the ops into ONE table (`_build_ops`). A row names the
# op, the rule every parameter must pass, and either an argv TEMPLATE or a
# builtin coroutine. The agent reports the table in `hello`, so the hub
# learns what this bench can do from the bench itself instead of from
# capability strings that had to be kept in step by hand — and a new op is
# one row here, no hub code.

_JOB_HEADER = struct.Struct(">cI")
_JOB_KIND_CONTROL = b"C"
_JOB_KIND_DATA = b"D"
_JOB_MAX_FRAME = 8 * 1024 * 1024

# Only one job may touch the device at a time. Not a queue: a caller whose
# flash is refused should learn that NOW, not sit in a line behind an
# unrelated job it cannot see (ADR-056 §决定 3). Ops that only read this
# host (usb-list, log-tail) never take it — they must answer DURING a
# thirty-minute flash, since that is when someone wants to know whether the
# board is even on the bus.
_job_lock = asyncio.Lock()

# How the running job gives its hold of _job_lock back the moment its result
# is sent (see _job_reported). Per task, not a global: a read-only job that
# finishes while a flash runs must not release the lock the flash holds.
_job_release: contextvars.ContextVar[Callable[[], None] | None] = contextvars.ContextVar(
    "_job_release", default=None
)


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
# channel is handled far from the arg parsing, and threading the values
# through every call would be noise.
_FASTBOOT_PATH = ""
_FLASH_PARTITIONS: frozenset[str] = frozenset()
# Where this agent's own log lands (None = file logging off). It is the only
# file `log-tail agent` will ever read.
_AGENT_LOG_PATH: Path | None = None


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


# ── the three value rules the table's rows encode ────────────────────
# They stay as named functions, next to the reasoning that produced them:
# the table row says WHICH rule a parameter follows, these say WHY the rule
# is shaped the way it is.

# getvar 变量名的形状。与分区名**不能共用一条规则**:变量名里合法地带冒号
# (`partition-size:cfg`、`has-slot:boot`),而分区名里冒号是非法的。
# 放宽的只有冒号这一项 —— 仍然禁开头的 `-`(会被当成 flag)、禁空白(会拆成
# 两个 argv 元素)、禁路径分隔符,并限长。空字符串是合法输入,代表 `getvar all`。
_GETVAR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}\Z")


def _getvar_allowed(name: str) -> bool:
    """`getvar` 的变量名会进 argv,所以照分区名同样的最小权限对待。

    这里**没有**白名单:变量名的集合是平台相关的(标准名之外厂商还会加),
    hub 侧无从知道哪些合法,写死一份反而会把这台设备真有的变量挡在外面 ——
    那正是 web 分区下拉写死四个名字的同一个错。形状对了就放行,答不答由设备说。
    """
    return name == "" or bool(_GETVAR_RE.match(name))


# `oem` 命令的白名单。跟 getvar 相反,这里**必须**有白名单:getvar 只是问,
# oem 会改设备,而且 oem 命令做什么是厂商自己定义的 —— 有的是单向的
# (有的 bootloader 文档列了 `oem at-disable-unlock-vboot`,永久禁止解锁)。
# 所以只放行查过「可逆」的命令,检查放在拼 argv 的这一侧(ADR-056)。
# 现有两条:u-boot fastboot 实现里的 AVB 锁定 / 解锁(f_fastboot.c 里的两个 oem
# 分支),互为逆操作,解锁不需要授权;锁定只写 lock_state,不碰刷机锁
# flash_lock_state,锁着也照样能 flash(按代码读的,2026-09-30)。
# 要加别的,先查清它可不可逆。
_OEM_COMMANDS = frozenset({"at-lock-vboot", "at-unlock-vboot"})


def _oem_allowed(command: str) -> bool:
    """只认白名单里一字不差的命令:不做大小写折叠、不去空白 ——
    设备那边也不会替你折叠,多一个空格就会变成两个 argv 元素。"""
    return command in _OEM_COMMANDS


def _partition_allowed(name: str) -> bool:
    """Partition names this agent will pass to fastboot.

    Deliberately a shape check plus an optional allowlist, both evaluated
    HERE. `_PARTITION_RE` alone already stops the dangerous class — a name
    with a path separator, a space, or a leading dash would otherwise turn
    into an extra argv element or a path escape once fastboot parses it.
    `erase` shares the list with `flash`: erasing a partition takes no more
    authority than rewriting it, and a second list would only be a second
    thing to keep in step."""
    if not _PARTITION_RE.match(name):
        return False
    return True if not _FLASH_PARTITIONS else name in _FLASH_PARTITIONS


# ── the op table ─────────────────────────────────────────────────────


class _JobRejected(Exception):
    """A request this agent will not act on. `code` is the wire error code
    the done frame carries; `message` is the human-readable reason."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# A placeholder is a WHOLE argv element, never part of one: "{partition}" is
# replaced by exactly one validated value, while "--part={partition}" is a
# table bug and is refused when the table is built. That is the whole
# security argument in one rule — a value can never grow, split or merge an
# argv element, so what the validator saw is what the tool gets.
_PLACEHOLDER_RE = re.compile(r"^\{([a-z_]+)\}$")
_PARAM_KINDS = ("partition", "enum", "int", "text")
_REJECT_CODE_DEFAULT = "BENCH_PARAM_REJECTED"


@dataclass(frozen=True)
class ParamSpec:
    """One parameter of an op: name, kind, and the rule a value must pass.
    `describe()` is the ParamDesc the hub sees in `hello`."""

    name: str
    kind: str
    required: bool = True
    default: Any = None
    choices: tuple[str, ...] = ()  # enum
    min: int | None = None  # int
    max: int | None = None  # int
    pattern: str = ""  # text: re.fullmatch
    max_len: int = 0  # text
    # Agent-side only (not in describe()): the error code a rejection carries
    # and the noun used in its message. partition / oem.command / getvar.name
    # keep the codes and the wording the hub already knows.
    code: str = _REJECT_CODE_DEFAULT
    label: str = ""

    def __post_init__(self) -> None:
        if self.kind not in _PARAM_KINDS:
            raise ValueError(f"param {self.name}: unknown kind {self.kind!r}")
        if self.kind == "enum" and not self.choices:
            raise ValueError(f"param {self.name}: enum without choices")
        if self.kind == "int" and (self.min is None or self.max is None):
            raise ValueError(f"param {self.name}: int without min/max")
        if self.kind == "text" and (not self.pattern or self.max_len <= 0):
            raise ValueError(f"param {self.name}: text without pattern/max_len")

    def describe(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "kind": self.kind,
            "required": self.required,
            "default": self.default,
        }
        if self.kind == "enum":
            d["choices"] = list(self.choices)
        elif self.kind == "int":
            d["min"] = self.min
            d["max"] = self.max
        elif self.kind == "text":
            d["pattern"] = self.pattern
            d["max_len"] = self.max_len
        return d

    def resolve(self, req: Mapping[str, Any]) -> Any:
        """The validated value this param takes from a request, or raise
        _JobRejected. Missing / null / "" means "use the default" for an
        optional param and is a rejection for a required one."""
        raw = req.get(self.name)
        if raw is None or raw == "":
            if not self.required:
                return self.default
            raw = ""
        what = self.label or self.name
        if self.kind == "partition":
            if not isinstance(raw, str) or not _partition_allowed(raw):
                raise _JobRejected(self.code, f"{what} {raw!r} rejected by this agent")
            return raw
        if self.kind == "enum":
            # Exact: no case folding, no strip. The device would not fold it
            # either, and a stray space would become a second argv element.
            if not isinstance(raw, str) or raw not in self.choices:
                raise _JobRejected(self.code, f"{what} {raw!r} is not on this agent's allowlist")
            return raw
        if self.kind == "int":
            # bool subclasses int in Python; `true` for a line count is a
            # caller bug to report, not the number 1.
            lo, hi = int(self.min or 0), int(self.max or 0)
            if isinstance(raw, bool) or not isinstance(raw, int) or raw < lo or raw > hi:
                raise _JobRejected(
                    self.code, f"{what} {raw!r} must be an integer from {lo} to {hi}"
                )
            return raw
        if (
            not isinstance(raw, str)
            or len(raw) > self.max_len
            or not re.fullmatch(self.pattern, raw)
        ):
            raise _JobRejected(self.code, f"{what} {raw!r} rejected")
        return raw


# A builtin gets the op's row and the validated values; it returns (stdout,
# data) for the done frame or raises _JobRejected. An image builtin finds the
# received, verified file under the reserved key "image" (no param may use
# that name — it is the {image} placeholder of argv rows). A verdict turns a tool's
# (rc, stdout, stderr) into (ok, code, error) when exit code 0 is not the
# whole story.
_Builtin = Callable[["OpSpec", Mapping[str, Any]], Awaitable[tuple[str, dict[str, Any]]]]
_Verdict = Callable[[int, str, str], tuple[bool, str, str]]


@dataclass(frozen=True)
class OpSpec:
    """One row of the op table. `describe()` is the OpDesc the hub sees."""

    name: str
    group: str  # "fastboot" | "host"
    summary: str
    params: tuple[ParamSpec, ...] = ()  # in argv order
    needs: str = ""  # "fastboot-device" | ""
    image: bool = False  # receives an image stream before running
    max_image_bytes: int = 0  # image ops: 0 = bounded only by this host's temp space
    mutates: bool = True  # changes the device: the hub confirms + records
    exclusive: bool = True  # needs the single job lock (touches the USB device)
    record: str = "none"  # hub-side recording tier: none | events | uart
    timeout_s: float = 300.0
    # Exactly one of the two. argv[0] is the tool path from THIS host's
    # config; every other element is literal or a whole-element placeholder.
    argv: tuple[str, ...] = ()
    builtin: _Builtin | None = None
    # Agent-side only (not in describe()):
    verdict: _Verdict | None = None
    no_device_msg: str = "no device is in fastboot on this host"

    def __post_init__(self) -> None:
        if self.group not in ("fastboot", "host"):
            raise ValueError(f"op {self.name}: unknown group {self.group!r}")
        if self.needs not in ("", "fastboot-device"):
            raise ValueError(f"op {self.name}: unknown needs {self.needs!r}")
        if self.record not in ("none", "events", "uart"):
            raise ValueError(f"op {self.name}: unknown record tier {self.record!r}")
        if bool(self.argv) == (self.builtin is not None):
            raise ValueError(f"op {self.name}: exactly one of argv / builtin")
        names = {p.name for p in self.params}
        used: set[str] = set()
        for el in self.argv[1:]:
            m = _PLACEHOLDER_RE.match(el)
            if m is None:
                if "{" in el or "}" in el:
                    raise ValueError(
                        f"op {self.name}: a placeholder must be a whole argv element, got {el!r}"
                    )
                continue
            key = m.group(1)
            if key == "image":
                if not self.image:
                    raise ValueError(f"op {self.name}: {{image}} on an op that takes no image")
            elif key not in names:
                raise ValueError(f"op {self.name}: placeholder {el} names no param")
            used.add(key)
        if any(p.name == "image" for p in self.params):
            raise ValueError(f"op {self.name}: 'image' is reserved for the received image")
        if self.image and self.argv and "image" not in used:
            raise ValueError(f"op {self.name}: image op without an {{image}} element")

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "group": self.group,
            "summary": self.summary,
            "params": [p.describe() for p in self.params],
            "needs": self.needs,
            "image": self.image,
            "max_image_bytes": self.max_image_bytes,
            "mutates": self.mutates,
            "exclusive": self.exclusive,
            "record": self.record,
            "timeout_s": self.timeout_s,
        }

    def resolve(self, req: Mapping[str, Any]) -> dict[str, Any]:
        """Every param validated from the request. Keys the table does not
        name are ignored: they cannot reach argv, so there is nothing to
        refuse — and `op` / `size` / `sha256` ride the same frame."""
        return {p.name: p.resolve(req) for p in self.params}

    def assemble(self, values: Mapping[str, Any], image: str = "") -> list[str]:
        """argv from the template: each placeholder becomes exactly one
        element holding one validated value. An optional param resolved to
        "" drops its element (`fastboot reboot` with no target)."""
        argv: list[str] = [self.argv[0]] if self.argv else []
        for el in self.argv[1:]:
            m = _PLACEHOLDER_RE.match(el)
            if m is None:
                argv.append(el)
                continue
            key = m.group(1)
            if key == "image":
                if not image:
                    raise ValueError(f"op {self.name}: no image path to substitute")
                argv.append(image)
                continue
            value = values[key]
            if value == "":
                continue
            argv.append(str(value))
        return argv


def _devices_verdict(rc: int, out: str, _err: str) -> tuple[bool, str, str]:
    """An empty listing exits 0 — that is fastboot saying "nothing is here",
    not "the query worked". Reporting it as success would send the caller
    on to flash a device that is not in fastboot at all. Say what was
    observed: the generic fallback ("fastboot failed") is right for a tool
    that crashed with no output and useless here."""
    if rc != 0:
        return False, "FLASH_FAILED", ""
    if not out.strip():
        return (
            False,
            "FASTBOOT_NO_DEVICE",
            "fastboot ran but listed no devices — the board is not in fastboot",
        )
    return True, "", ""


# ── builtin: usb-list ────────────────────────────────────────────────
# Why it exists (2026-10-14 bench): a board confirmed on the serial console to
# be in bootloader fastboot, and `fastboot devices` empty four times in a
# row. From the hub there is no telling "cable not on this machine" from
# "driver not installed" — and the next step differs completely. This op
# returns the RAW facts (VID:PID, driver, problem code, interface class
# triples); the hub decides what mode a board is in. Keeping the vendor
# tables on the hub means a new vendor never needs a new agent.

# ONE fixed command line. Nothing from the hub is interpolated — the op has no
# parameters, so there is nothing to interpolate. CompatibleID carries the
# interface class triple (class ff / subclass 42 / protocol 01 = adb, 03 =
# fastboot) even when no driver is installed, which is exactly the case
# worth diagnosing. The output encoding is pinned to UTF-8 so a device name
# with non-ASCII characters survives the pipe.
_USB_PS_QUERY = (
    "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
    "Get-CimInstance Win32_PnPEntity | "
    "Where-Object { $_.PNPDeviceID -like 'USB\\*' } | "
    "Select-Object Name,PNPDeviceID,Status,ConfigManagerErrorCode,PNPClass,"
    "Service,Manufacturer,CompatibleID | "
    "ConvertTo-Json -Compress"
)
_USB_PS_ARGV = ("powershell", "-NoProfile", "-NonInteractive", "-Command", _USB_PS_QUERY)
_USB_VID_RE = re.compile(r"VID_([0-9A-Fa-f]{4})")
_USB_PID_RE = re.compile(r"PID_([0-9A-Fa-f]{4})")
_USB_MI_RE = re.compile(r"&MI_([0-9A-Fa-f]{2})")
# Non-Windows hosts enumerate sysfs instead, so the op works (and is
# testable) anywhere. Module-level so a test can point it at a fake tree.
_SYSFS_USB_ROOT = Path("/sys/bus/usb/devices")
# Device directories look like `1-3` / `2-1.4`; `usb1` is a root hub and
# `1-3:1.0` is an interface of 1-3 — both are excluded here on purpose.
_SYSFS_USB_DEVICE_RE = re.compile(r"^\d+-[\d.]+$")


def _str_or_empty(value: Any) -> str:
    return "" if value is None else str(value)


def _compatible_ids(value: Any) -> list[str]:
    """ConvertTo-Json renders a one-element array as a bare string and an
    absent value as null; the hub always gets a list of strings."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    return [str(value)]


def _usb_dev_from_pnp(entry: Mapping[str, Any]) -> dict[str, Any]:
    """One Win32_PnPEntity record → UsbDev. Everything is verbatim except
    the three fields pulled out of the instance id for convenience."""
    instance_id = _str_or_empty(entry.get("PNPDeviceID"))
    vid = _USB_VID_RE.search(instance_id)
    pid = _USB_PID_RE.search(instance_id)
    mi = _USB_MI_RE.search(instance_id)
    problem = entry.get("ConfigManagerErrorCode")
    return {
        "name": _str_or_empty(entry.get("Name")),
        "instance_id": instance_id,
        "vid": vid.group(1).lower() if vid else "",
        "pid": pid.group(1).lower() if pid else "",
        # The last backslash segment: a serial for a device that reports one,
        # a bus-assigned token otherwise. Raw either way.
        "serial": instance_id.rsplit("\\", 1)[1] if "\\" in instance_id else "",
        "interface": mi.group(1).lower() if mi else "",
        "status": _str_or_empty(entry.get("Status")),
        "problem": problem if isinstance(problem, int) and not isinstance(problem, bool) else None,
        "class": _str_or_empty(entry.get("PNPClass")),
        "service": _str_or_empty(entry.get("Service")),
        "manufacturer": _str_or_empty(entry.get("Manufacturer")),
        "compatible_ids": _compatible_ids(entry.get("CompatibleID")),
    }


def _parse_usb_json(text: str) -> list[dict[str, Any]]:
    """PowerShell's ConvertTo-Json output → UsbDev list. One match comes
    back as a bare object, not a one-element list, and no match prints
    nothing at all — both are normal, not errors. Garbage raises ValueError."""
    text = text.strip()
    if not text:
        return []
    doc = json.loads(text)
    if isinstance(doc, dict):
        doc = [doc]
    if not isinstance(doc, list):
        raise ValueError("expected a JSON object or list")
    return [_usb_dev_from_pnp(e) for e in doc if isinstance(e, dict)]


def _sysfs_read(directory: Path, name: str) -> str:
    try:
        return (directory / name).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _sysfs_driver(directory: Path) -> str:
    """Basename of the `driver` symlink ("usb-storage", "cdc_acm"), or ""."""
    try:
        return Path(os.readlink(directory / "driver")).name
    except OSError:
        return ""


def _sysfs_class_triple(directory: Path, prefix: str) -> str:
    """`USB\\Class_xx&SubClass_xx&Prot_xx` from sysfs, in the same shape as
    a Windows CompatibleID so the hub keeps one classification table."""
    cls = _sysfs_read(directory, f"b{prefix}Class").lower()
    sub = _sysfs_read(directory, f"b{prefix}SubClass").lower()
    prot = _sysfs_read(directory, f"b{prefix}Protocol").lower()
    if not (cls and sub and prot):
        return ""
    return f"USB\\Class_{cls}&SubClass_{sub}&Prot_{prot}"


def _enumerate_sysfs_usb(root: Path) -> list[dict[str, Any]]:
    """UsbDev list from a sysfs tree: one entry per device plus one per
    interface (the Windows listing shows a composite device the same way,
    as a parent plus `MI_xx` children), so the hub sees one shape."""
    try:
        devices = sorted(p for p in root.iterdir() if _SYSFS_USB_DEVICE_RE.match(p.name))
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for dev in devices:
        try:
            interfaces = sorted(
                p for p in dev.iterdir() if p.is_dir() and p.name.startswith(dev.name + ":")
            )
        except OSError:
            interfaces = []
        triples = [_sysfs_class_triple(dev, "Device")]
        triples += [_sysfs_class_triple(itf, "Interface") for itf in interfaces]
        base = {
            "name": _sysfs_read(dev, "product"),
            "instance_id": dev.name,
            "vid": _sysfs_read(dev, "idVendor").lower(),
            "pid": _sysfs_read(dev, "idProduct").lower(),
            "serial": _sysfs_read(dev, "serial"),
            "interface": "",
            "status": "OK",
            "problem": None,
            "class": "",
            "service": _sysfs_driver(dev),
            "manufacturer": _sysfs_read(dev, "manufacturer"),
            "compatible_ids": [t for t in triples if t],
        }
        out.append(base)
        for itf in interfaces:
            triple = _sysfs_class_triple(itf, "Interface")
            out.append(
                {
                    **base,
                    "instance_id": itf.name,
                    "interface": _sysfs_read(itf, "bInterfaceNumber").lower(),
                    "service": _sysfs_driver(itf),
                    "compatible_ids": [triple] if triple else [],
                }
            )
    return out


async def _reap(proc: Any) -> None:
    """Kill a child that may still be running, then wait for it.

    Every subprocess path calls this on its way out — on a timeout AND when
    the job is cancelled (agent stopping, hub dropping the session). An
    abandoned fastboot keeps writing the partition, holds the USB interface
    and keeps the temp image open so it cannot be deleted. The wait is
    shielded so a second cancel cannot leave the child unreaped."""
    if proc.returncode is None:
        with contextlib.suppress(ProcessLookupError, OSError):
            proc.kill()
    with contextlib.suppress(Exception):
        await asyncio.shield(proc.wait())


async def _usb_list_windows(timeout_s: float) -> list[dict[str, Any]]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *_USB_PS_ARGV, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except (FileNotFoundError, OSError) as e:
        raise _JobRejected("FLASH_FAILED", f"cannot run powershell on this host: {e}") from e
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout_s)
    except TimeoutError:
        await _reap(proc)
        raise _JobRejected(
            "FLASH_FAILED", f"powershell did not answer within {timeout_s:.0f}s"
        ) from None
    except BaseException:
        await _reap(proc)
        raise
    if proc.returncode != 0:
        detail = err.decode("utf-8", errors="replace").strip()[-500:]
        raise _JobRejected("FLASH_FAILED", f"powershell failed (rc {proc.returncode}): {detail}")
    try:
        return _parse_usb_json(out.decode("utf-8", errors="replace"))
    except ValueError as e:
        raise _JobRejected("FLASH_FAILED", f"unexpected powershell output: {e}") from e


async def _op_usb_list(spec: OpSpec, _values: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    if sys.platform == "win32":
        devices = await _usb_list_windows(spec.timeout_s)
    else:
        # An empty listing must mean "nothing plugged in", never "this host
        # has nowhere to look" (e.g. macOS) — the two lead to opposite fixes.
        if not await asyncio.to_thread(_SYSFS_USB_ROOT.is_dir):
            raise _JobRejected(
                "FLASH_FAILED", f"this host cannot list USB devices: no {_SYSFS_USB_ROOT}"
            )
        devices = await asyncio.to_thread(_enumerate_sysfs_usb, _SYSFS_USB_ROOT)
    return "", {"devices": devices}


# ── builtin: log-tail ────────────────────────────────────────────────
# Sources are NAMES, resolved here from this host's config. The hub never
# sends a path — a job that read "the file the hub names" would read any
# file on this machine. Only the tail of a file is read: setupapi.dev.log
# grows for years and the agent's own log rotates at 5 MB, so 8 MiB covers
# everything anyone wants to see and bounds the memory a request can take.

_LOG_TAIL_WINDOW = 8 * 1024 * 1024
# `grep` is a literal substring, not a regex; the only shape rule is "no
# control characters" so a filter can never smuggle a newline into the
# echoed error message.
_GREP_PATTERN = r"[^\x00-\x1f\x7f]*"


def _log_sources() -> dict[str, Path]:
    """Host-side logs `log-tail` may read, by name."""
    sources: dict[str, Path] = {}
    if _AGENT_LOG_PATH is not None:
        sources["agent"] = _AGENT_LOG_PATH
    if sys.platform == "win32":
        windir = Path(os.environ.get("WINDIR") or r"C:\Windows")
        sources["setupapi"] = windir / "INF" / "setupapi.dev.log"
    # What the last remote update did (ADR-060) — readable from the hub, so
    # "why did it roll back" needs nobody at this machine.
    update_log = _state_dir(_install_dir()) / "update.log"
    if update_log.is_file():
        sources["update"] = update_log
    return sources


def _sniff_bom(head: bytes) -> tuple[str, int]:
    """(codec, BOM length). Windows logs come in UTF-16 LE with a BOM
    (setupapi.dev.log), UTF-8 with Notepad's BOM, or plain UTF-8."""
    if head.startswith(b"\xef\xbb\xbf"):
        return "utf-8", 3
    if head.startswith(b"\xff\xfe"):
        return "utf-16-le", 2
    if head.startswith(b"\xfe\xff"):
        return "utf-16-be", 2
    return "utf-8", 0


def _tail_log(
    path: Path, lines: int, grep: str, window: int = _LOG_TAIL_WINDOW
) -> tuple[list[str], bool]:
    """Last `lines` lines of `path` (after the optional case-insensitive
    substring filter), and whether the read window stopped short of the
    file's start. Blocking — run it off the loop."""
    with path.open("rb") as fh:
        codec, bom = _sniff_bom(fh.read(4))
        size = fh.seek(0, os.SEEK_END)
        start = max(bom, size - window)
        if codec.startswith("utf-16"):
            # keep the window on a code-unit boundary or every character
            # after the cut decodes as garbage
            start = bom + ((start - bom) // 2) * 2
        fh.seek(start)
        chunk = fh.read()
    truncated = start > bom
    rows = chunk.decode(codec, errors="replace").splitlines()
    if truncated and rows:
        rows = rows[1:]  # began mid-line: a fragment, not a line
    if grep:
        needle = grep.lower()
        rows = [r for r in rows if needle in r.lower()]
    return rows[-lines:], truncated


async def _op_log_tail(
    _spec: OpSpec, values: Mapping[str, Any], *, sources: Mapping[str, Path]
) -> tuple[str, dict[str, Any]]:
    source = str(values["source"])
    path = sources[source]  # the enum rule already pinned it to a configured name
    try:
        rows, truncated = await asyncio.to_thread(
            _tail_log, path, int(values["lines"]), str(values["grep"])
        )
    except OSError as e:
        raise _JobRejected(
            "BENCH_LOG_UNAVAILABLE", f"log source {source!r} is not readable right now: {e}"
        ) from e
    rows, clipped = _fit_rows(rows, _JOB_OUTPUT_CAP)
    # `truncated` = older lines exist that the caller did not get, whether
    # the read window or the frame cap left them out.
    return "\n".join(rows), {
        "source": source,
        "lines": len(rows),
        "truncated": truncated or clipped,
    }


def _fit_rows(rows: list[str], budget: int) -> tuple[list[str], bool]:
    """The newest rows whose joined text fits `budget` characters.

    `_job_finish` keeps only the last `_JOB_OUTPUT_CAP` characters of stdout;
    cutting here instead, on line boundaries, means the caller never gets a
    half line at the top and `lines` counts what actually arrives."""
    kept: list[str] = []
    used = 0
    for row in reversed(rows):
        cost = len(row) + (1 if kept else 0)
        if used + cost > budget:
            if not kept:  # one line longer than the whole budget: keep its end
                kept.append(row[-budget:])
            kept.reverse()
            return kept, True
        kept.append(row)
        used += cost
    kept.reverse()
    return kept, False


# ── the rows ─────────────────────────────────────────────────────────
# Timeouts: fastboot BLOCKS waiting for a device when none is in fastboot
# mode ("< waiting for any device >"). Without a ceiling one such command
# holds the single-job lock until the hub's 30-minute timeout — a bench
# taken out of service by a command that was never going to succeed. Per
# row because the right patience differs: a query should answer now, a
# partition write legitimately takes minutes.
_DEVICE_QUERY_TIMEOUT_S = 20.0


def _fastboot_rows(fastboot: str) -> list[OpSpec]:
    partition = ParamSpec(
        "partition", "partition", code="FLASH_PARTITION_REJECTED", label="partition"
    )
    return [
        OpSpec(
            "devices",
            "fastboot",
            "fastboot devices",
            mutates=False,
            timeout_s=_DEVICE_QUERY_TIMEOUT_S,
            argv=(fastboot, "devices"),
            verdict=_devices_verdict,
        ),
        OpSpec(
            "getvar",
            "fastboot",
            "fastboot getvar <name>",
            # Transport only: the value's MEANING is platform-specific (this
            # bench answers `partition-size:cfg` with 0 on flashes that
            # succeed), so nothing on this path interprets it. fastboot
            # writes getvar's answer to stderr; both streams go back.
            params=(
                ParamSpec(
                    "name",
                    "text",
                    required=False,
                    default="all",
                    pattern=_GETVAR_RE.pattern,
                    max_len=64,
                    code="FLASH_VAR_REJECTED",
                    label="variable name",
                ),
            ),
            needs="fastboot-device",
            mutates=False,
            timeout_s=_DEVICE_QUERY_TIMEOUT_S,
            argv=(fastboot, "getvar", "{name}"),
        ),
        OpSpec(
            "oem",
            "fastboot",
            "fastboot oem <command>",
            # Transport only, like getvar: whether the lock took is read from
            # the device afterwards, not inferred from OKAY.
            params=(
                ParamSpec(
                    "command",
                    "enum",
                    choices=tuple(sorted(_OEM_COMMANDS)),
                    code="FLASH_OEM_REJECTED",
                    label="oem command",
                ),
            ),
            needs="fastboot-device",
            record="events",
            timeout_s=30.0,
            argv=(fastboot, "oem", "{command}"),
        ),
        OpSpec(
            "reboot",
            "fastboot",
            "fastboot reboot [target]",
            params=(
                ParamSpec(
                    "target",
                    "enum",
                    required=False,
                    default="",
                    choices=tuple(sorted(_FASTBOOT_REBOOT_TARGETS)),
                    label="reboot target",
                ),
            ),
            needs="fastboot-device",
            record="events",
            timeout_s=60.0,
            argv=(fastboot, "reboot", "{target}"),
            no_device_msg="no device is in fastboot on this host — nothing to reboot",
        ),
        OpSpec(
            "flash",
            "fastboot",
            "fastboot flash <partition> <image>",
            params=(partition,),
            needs="fastboot-device",
            image=True,
            record="uart",
            timeout_s=900.0,
            argv=(fastboot, "flash", "{partition}", "{image}"),
            no_device_msg="no device is in fastboot on this host — nothing was transferred",
        ),
        OpSpec(
            "erase",
            "fastboot",
            "fastboot erase <partition>",
            params=(partition,),
            needs="fastboot-device",
            record="events",
            # Erasing a large partition (userdata) can mean a full-device
            # discard; a small one (misc) answers at once. Sized for the
            # large case.
            timeout_s=300.0,
            argv=(fastboot, "erase", "{partition}"),
            no_device_msg="no device is in fastboot on this host — nothing was erased",
        ),
        OpSpec(
            "set-active",
            "fastboot",
            "fastboot set_active <slot>",
            # A bootloader without slots refuses; the refusal goes back verbatim.
            params=(ParamSpec("slot", "enum", choices=("a", "b"), label="slot"),),
            needs="fastboot-device",
            record="events",
            timeout_s=30.0,
            argv=(fastboot, "set_active", "{slot}"),
        ),
        OpSpec(
            "boot",
            "fastboot",
            "fastboot boot <image>",
            # Runs the image from RAM without writing a partition — mutates the
            # RUNNING state, so it is confirmed and recorded like a write.
            needs="fastboot-device",
            image=True,
            record="uart",
            timeout_s=120.0,
            argv=(fastboot, "boot", "{image}"),
            no_device_msg="no device is in fastboot on this host — nothing was transferred",
        ),
    ]


def _host_rows(log_sources: Mapping[str, Path]) -> list[OpSpec]:
    rows = [
        OpSpec(
            "usb-list",
            "host",
            "USB devices attached to this host, as the OS sees them",
            mutates=False,
            exclusive=False,
            timeout_s=30.0,
            builtin=_op_usb_list,
        )
    ]
    # No sources configured = no row: a dropdown with zero valid choices
    # is a question nobody can answer.
    if log_sources:
        rows.append(
            OpSpec(
                "log-tail",
                "host",
                "last lines of a host-side log, by source name",
                params=(
                    ParamSpec("source", "enum", choices=tuple(log_sources), label="log source"),
                    ParamSpec("lines", "int", required=False, default=200, min=1, max=2000),
                    ParamSpec(
                        "grep",
                        "text",
                        required=False,
                        default="",
                        pattern=_GREP_PATTERN,
                        max_len=64,
                    ),
                ),
                mutates=False,
                exclusive=False,
                timeout_s=30.0,
                builtin=functools.partial(_op_log_tail, sources=dict(log_sources)),
            )
        )
    # ADR-060: this agent's own files. Exclusive, so it never lands in the
    # middle of a flash; "mutates" because it restarts the bench's only link.
    rows.append(
        OpSpec(
            "agent-update",
            "host",
            "replace this agent's files with the pushed bundle and restart it; "
            "the old files come back if the new build does not reach the hub",
            image=True,
            max_image_bytes=_BUNDLE_MAX_BYTES,
            mutates=True,
            exclusive=True,
            record="events",
            timeout_s=_SELF_CHECK_TIMEOUT_S + 30.0,
            builtin=_op_agent_update,
        )
    )
    return rows


def _build_ops(
    fastboot: str = "", log_sources: Mapping[str, Path] | None = None
) -> dict[str, OpSpec]:
    """The op table for a host with THIS fastboot (none = no fastboot rows,
    in the table and therefore in hello) and THESE log sources."""
    rows = [*(_fastboot_rows(fastboot) if fastboot else []), *_host_rows(log_sources or {})]
    return {row.name: row for row in rows}


# Which names are fastboot ops — known even when the table has no fastboot
# rows, so a hub asking for `flash` on a bench without fastboot is told THAT
# rather than "unknown op".
_FASTBOOT_OP_NAMES = frozenset(row.name for row in _fastboot_rows("fastboot"))

# The live table, built by _apply_flash_config once the config is known.
_OPS: dict[str, OpSpec] = {}


# ── dispatch ─────────────────────────────────────────────────────────


async def _handle_job_channel(
    hub_url: str, token: str | None, frame: dict[str, Any], ws: Any = None
) -> None:
    cid = str(frame.get("cid") or "")
    csecret = str(frame.get("csecret") or "")
    params = frame.get("params") or {}
    # The op hint lets this side refuse BEFORE dialling back — the caller
    # is holding a device it thinks is about to be written — and decide
    # whether the job needs the lock at all. No hint = an older hub: treat
    # the job as an exclusive fastboot job, as before.
    hint = params.get("op")
    hint = None if hint is None else str(hint)
    spec = _OPS.get(hint) if hint is not None else None
    if hint is None:
        wants_fastboot, exclusive = True, True
    elif spec is None:
        # Not in the table: dial back so the answer is a structured
        # BENCH_OP_UNKNOWN — unless it is a fastboot op this host lacks the
        # tool for, which is a different, more useful answer.
        wants_fastboot, exclusive = hint in _FASTBOOT_OP_NAMES, False
    else:
        wants_fastboot, exclusive = spec.group == "fastboot", spec.exclusive
    if wants_fastboot and not _fastboot_path():
        await _report_channel_error(ws, cid, "no fastboot executable on this agent host")
        return
    url = _channel_url(hub_url, cid)
    headers = _channel_headers(token, csecret)
    if exclusive:
        # Check and take the lock with no await in between, BEFORE dialling
        # back: dialling yields, and a second request arriving in that gap
        # would pass the check and then queue behind the lock — the rule is
        # "rejected at once", never "queued". An unlocked asyncio.Lock is
        # acquired without suspending.
        if _job_lock.locked():
            await _report_channel_error(ws, cid, "another flash job is already running")
            return
        await _job_lock.acquire()
    held = exclusive

    def release() -> None:
        nonlocal held
        if held:
            held = False
            _job_lock.release()

    # Normally released by _job_reported() as `done` goes out; the finally
    # below covers a job that ended without one.
    reset = _job_release.set(release)
    try:
        async with ws_connect(url, max_size=None, additional_headers=headers) as data_ws:
            _STATUS.channel_opened(cid, "job", hint or "fastboot")
            await _run_job(data_ws, _OPS, hint=hint)
    except Exception as e:
        _log.warning("job channel %s ended: %s", cid[:8], e)
    finally:
        _job_release.reset(reset)
        release()
        _STATUS.channel_closed(cid)


async def _run_job(data_ws: Any, ops: Mapping[str, OpSpec], *, hint: str | None = None) -> None:
    """Read the job request and run it through the table: validate every
    parameter, check the precondition, then run the row's argv or builtin."""
    reader = _JobReader(data_ws)
    try:
        req = await reader.read_control()
    except (_JobProtocolError, ValueError) as e:
        await _job_fail(data_ws, f"bad opening frame: {e}", code="")
        return
    if req is None:
        return
    op = str(req.get("op") or "")
    if hint is not None and op != hint:
        # The lock decision was made from the hint; a request that names
        # another op would run it under the wrong decision.
        await _job_fail(
            data_ws,
            f"job op {op!r} does not match the channel's op hint {hint!r}",
            code="BENCH_PARAM_REJECTED",
        )
        return
    spec = ops.get(op)
    if spec is None:
        await _job_fail(data_ws, f"unsupported job op {op!r}", code="BENCH_OP_UNKNOWN")
        return
    try:
        await _dispatch(data_ws, reader, spec, req)
    except _JobRejected as e:
        await _job_fail(data_ws, e.message, code=e.code)
    except Exception as e:
        # Whatever escaped (fastboot removed after start-up, a broken temp
        # dir, …) still ends the job with a `done` frame: without one the
        # hub can only report a dropped channel, not what went wrong.
        _log.warning("job %r failed on the agent: %r", op, e)
        await _job_fail(
            data_ws,
            f"agent error while running {op!r}: {type(e).__name__}: {e}",
            code="FLASH_FAILED",
        )


async def _dispatch(data_ws: Any, reader: _JobReader, spec: OpSpec, req: Mapping[str, Any]) -> None:
    """Validate, check the precondition, run. Raises _JobRejected for a
    refusal that `_run_job` turns into a structured `done` frame."""
    try:
        values = spec.resolve(req)
    except _JobRejected as e:
        await _job_fail(data_ws, e.message, code=e.code)
        return
    if spec.image:
        await _run_image_op(data_ws, reader, spec, values, req)
        return
    if spec.needs == "fastboot-device" and not await _device_present(
        spec.argv[0] if spec.argv else _fastboot_path()
    ):
        await _job_fail(data_ws, spec.no_device_msg, code="FASTBOOT_NO_DEVICE")
        return
    if spec.builtin is not None:
        await data_ws.send(_job_control({"ev": "accepted", "detail": spec.name}))
        try:
            out, data = await spec.builtin(spec, values)
        except _JobRejected as e:
            await _job_fail(data_ws, e.message, code=e.code)
            return
        await _job_finish(data_ws, 0, out, "", fail_code="", data=data)
        return
    argv = spec.assemble(values)
    await data_ws.send(_job_control({"ev": "accepted", "detail": " ".join(argv[1:])}))
    rc, out, err = await _run_fastboot(data_ws, argv, spec.timeout_s)
    await _finish_tool(data_ws, spec, rc, out, err)


async def _finish_tool(data_ws: Any, spec: OpSpec, rc: int, out: str, err: str) -> None:
    if spec.verdict is None:
        await _job_finish(data_ws, rc, out, err, fail_code="FLASH_FAILED")
        return
    ok, code, error = spec.verdict(rc, out, err)
    await _job_finish(data_ws, rc, out, err, fail_code=code, ok_override=ok, fail_error=error)


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
    _job_reported()


def _job_reported() -> None:
    """`done` is out: free the job lock now, not when the channel has
    finished closing. run_plan sends the next row the moment it reads
    `done`, and that open arrives while this side is still in the close
    handshake — every real plan run stopped at its second row with "another
    flash job is already running" (2026-10-20). Called right after the send
    with no await in between, so a stop request waiting for the lock still
    cannot cut the result off."""
    release = _job_release.get()
    if release is not None:
        release()


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
    try:
        proc = await asyncio.create_subprocess_exec(
            fastboot, "devices", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
    except OSError as e:
        raise _JobRejected("FLASH_FAILED", f"cannot run {fastboot!r} on this host: {e}") from e
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), _DEVICE_QUERY_TIMEOUT_S)
    except TimeoutError:
        await _reap(proc)
        # Not FASTBOOT_NO_DEVICE: a hung query is a stuck USB link or
        # driver, and "no board" would send the operator to the wrong end.
        raise _JobRejected(
            "FLASH_FAILED",
            f"`fastboot devices` did not answer within {_DEVICE_QUERY_TIMEOUT_S:.0f}s — the "
            "USB link or its driver is stuck; this says nothing about whether a board is there",
        ) from None
    except BaseException:
        await _reap(proc)
        raise
    return bool(out.decode("utf-8", errors="replace").strip())


async def _run_image_op(
    data_ws: Any,
    reader: _JobReader,
    spec: OpSpec,
    values: Mapping[str, Any],
    req: Mapping[str, Any],
) -> None:
    """The ONE receive path for every image op (flash, boot): refuse what can
    be refused before a byte crosses the tunnel, receive into a temp file
    this agent chose, verify size and digest, and only then run the tool."""
    try:
        size = int(req.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    want_digest = str(req.get("sha256") or "").lower()
    fastboot = spec.argv[0] if spec.argv else _fastboot_path()

    if size <= 0:
        await _job_fail(
            data_ws, f"image size {req.get('size')!r} out of range", code="FLASH_IMAGE_CORRUPT"
        )
        return
    free = await _tmp_free_bytes()
    if free < size + _FLASH_TMP_RESERVE_BYTES:
        # Refuse before a single byte crosses the tunnel: running out of
        # disk halfway through a multi-gigabyte transfer costs the whole
        # transfer and then fails anyway.
        await _job_fail(
            data_ws,
            f"not enough temp space on the agent host: image is {size} bytes, "
            f"{free} free (keeping {_FLASH_TMP_RESERVE_BYTES} in reserve) — "
            "nothing was transferred",
            code="FLASH_AGENT_NO_SPACE",
        )
        return

    # Check BEFORE accepting the image: streaming megabytes to a bench whose
    # board is not even in fastboot wastes the tunnel and then fails anyway.
    if spec.needs == "fastboot-device" and not await _device_present(fastboot):
        await _job_fail(data_ws, spec.no_device_msg, code="FASTBOOT_NO_DEVICE")
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

        if spec.builtin is not None:
            # A builtin image op (agent-update): it gets the verified file,
            # never a path the hub named.
            try:
                out, data = await spec.builtin(spec, {**values, "image": str(tmp_img)})
            except _JobRejected as e:
                await _job_fail(data_ws, e.message, code=e.code)
                return
            await _job_finish(data_ws, 0, out, "", fail_code="", data=data)
            return
        # argv assembled HERE from vetted pieces — see the section header.
        argv = spec.assemble(values, image=str(tmp_img))
        shown = " ".join(spec.assemble(values, image="<image>")[1:])
        await _job_progress(data_ws, "flash", 0, 0, f"starting fastboot {shown}")
        rc, out, err = await _run_fastboot(data_ws, argv, spec.timeout_s)
        await _finish_tool(data_ws, spec, rc, out, err)
    finally:
        # Also off-loop: deleting a multi-gigabyte file is not instant, and
        # this runs on the path back out of a job that may have just failed.
        with contextlib.suppress(Exception):
            await asyncio.to_thread(tmp_img.unlink, missing_ok=True)
        with contextlib.suppress(Exception):
            await asyncio.to_thread(tmp_dir.rmdir)


async def _tmp_free_bytes() -> int:
    """Free bytes where `_run_image_op` puts the received image."""
    usage = await asyncio.to_thread(shutil.disk_usage, tempfile.gettempdir())
    return int(usage.free)


async def _run_fastboot(
    data_ws: Any, argv: list[str], timeout_s: float = 300.0
) -> tuple[int, str, str]:
    """Run fastboot, relaying its stderr as progress while it works.

    fastboot writes its own progress to stderr, so relaying it is what makes
    a long write visible instead of a frozen bar. Reading it concurrently
    also keeps the pipe from filling and deadlocking the child."""
    _log.info("job: running %s", " ".join(argv[1:]))
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
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
            timeout_s,
        )
        rc = await proc.wait()
    except TimeoutError:
        timed_out = True
        # Kill, do not just abandon: an orphaned fastboot keeps holding the
        # USB interface and the next job would fail for a reason that has
        # nothing to do with it.
        await _reap(proc)
        rc = -1
    except BaseException:
        # Cancelled (agent stopping, session dropped): same reasoning, and
        # here fastboot may be halfway through writing a partition.
        await _reap(proc)
        raise
    out = b"".join(out_chunks).decode("utf-8", errors="replace")
    err = b"".join(err_chunks).decode("utf-8", errors="replace")
    if timed_out:
        err = (err + f"\n[agent] killed after {timeout_s:.0f}s").strip()
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
    data: dict[str, Any] | None = None,
) -> None:
    ok = (rc == 0) if ok_override is None else ok_override
    msg: dict[str, Any] = {
        "ev": "done",
        "ok": ok,
        "rc": rc,
        "stdout": out[-_JOB_OUTPUT_CAP:],
        "stderr": err[-_JOB_OUTPUT_CAP:],
        "error": "" if ok else (fail_error or err.strip() or out.strip() or "fastboot failed"),
        "code": "" if ok else fail_code,
    }
    # Only builtins have structured results; the frame of every other op
    # stays byte-for-byte what it was.
    if data is not None:
        msg["data"] = data
    with contextlib.suppress(Exception):
        await data_ws.send(_job_control(msg))
    _job_reported()


# ── self-update and single instance (ADR-060) ────────────────────────
# Two things that share one mechanism:
#
#  * every start first stops the previous instance of the SAME agent;
#  * the hub can push a new copy of this folder (`agent-update`), and the
#    agent restarts itself on it — or goes back to the old copy when the
#    new one does not reach the hub.
#
# "The same agent" is the same config file: one agent.conf = one bench
# identity. Two agents on one host, each with its own folder (or its own
# --config), never touch each other. Each running agent leaves a record in
# <its folder>/.alb-agent/instance-<key>.json; a newer start reads it, asks
# that process to stop through a stop-<pid> file the process polls, and
# kills it only when it does not go.
#
# The file swap is done by a COPY of the running file, in a separate process
# (`--apply-update <plan>`): this process has to exit before its files can be
# replaced and its COM ports reopened, and the code doing the swap should be
# the code already proven to run here, not the code that just arrived.

_EXIT_REPLACED = 75  # run-agent.bat closes its window on this instead of pausing
_STATE_DIR_NAME = ".alb-agent"
_LAUNCHER_ENV = "ALB_AGENT_LAUNCHER"  # set by run-agent.bat
_LAUNCHER_BAT = "run-agent.bat"
# The bundle rules. MUST stay in lockstep with alb.remote.agent_update, which
# builds the bundle: flat names only (no separator, no leading dot), so no
# member can land outside the folder it is unpacked into.
_BUNDLE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_BUNDLE_SUFFIXES = (".py", ".bat", ".md", ".txt", ".example")
_BUNDLE_CONFIG = "agent.conf"
_BUNDLE_MAIN = "alb_agent.py"
_BUNDLE_MAX_BYTES = 16 * 1024 * 1024
_BUNDLE_MAX_FILES = 64
_SELF_CHECK_TIMEOUT_S = 60.0
_UPDATE_HEALTH_TIMEOUT_S = 60.0
# How long the old agent has to send its `done` frame before the helper asks
# it to stop; and how long an agent that was asked to stop gets before it is
# killed (unless it reports a job in progress — see _stop_instance).
_UPDATE_SETTLE_S = 3.0
_STOP_GRACE_S = 15.0
_STOP_POLL_S = 1.0
# If the helper never asks this agent to stop, stop refusing channels.
_UPDATE_STALL_S = 120.0

_CREATE_NEW_CONSOLE = 0x00000010
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000

# Set when this process is about to stop (a newer instance asked, or an
# update is being applied): new channels are refused with this reason rather
# than opened and then cut.
_STOP_REASON = ""
_EXIT_CODE = 0
# This process's own record and where it lives (None until claimed).
_INSTANCE_PATH: Path | None = None
_INSTANCE: dict[str, Any] = {}
# How this process was started — what a restart repeats.
_LAUNCH_ARGV: list[str] = []
_CONFIG_USED = ""
_CONFIG_EXPLICIT = False
# False without a fixed agent_id (agent.conf or --agent-id): the restarted
# copy would get a new random id, and the hub — which waits for THIS id to
# come back — could never tell that it did.
_IDENTITY_STABLE = False
_AGENT_ID = ""
# What the last update did, read at start-up and sent in hello: how the hub
# tells "came back on the new build" from "came back rolled back" when the
# two builds have the same agent file.
_LAST_UPDATE: dict[str, Any] = {}


def _own_file() -> Path:
    return Path(__file__).resolve()


def _install_dir() -> Path:
    return _own_file().parent


def _state_dir(install: Path) -> Path:
    return install / _STATE_DIR_NAME


def _instance_key(config_used: str, agent_id: str) -> str:
    if config_used:
        ident = str(Path(config_used).resolve())
        if sys.platform == "win32":
            ident = ident.lower()  # one file, however its path is spelled
        return "conf-" + hashlib.sha256(ident.encode("utf-8")).hexdigest()[:12]
    return "id-" + hashlib.sha256(agent_id.encode("utf-8")).hexdigest()[:12]


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _replace_retry(src: Path, dst: Path) -> None:
    """os.replace, retried: on Windows a reader holding `dst` open (the
    helper polls the instance record) makes the replace fail for a moment."""
    for attempt in range(20):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.25)


def _write_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    _replace_retry(tmp, path)


# ── processes: is it still the one we recorded, and how to stop it ──


@functools.lru_cache(maxsize=1)
def _win_kernel32() -> Any:
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    k32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    k32.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    return k32


def _process_created(pid: int) -> float | None:
    """Windows: when the LIVE process `pid` was created (epoch seconds), or
    None when no such process is running."""
    import ctypes
    from ctypes import wintypes

    k32 = _win_kernel32()
    handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        code = wintypes.DWORD()
        if not k32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:
            return None  # 259 = STILL_ACTIVE
        times = [wintypes.FILETIME() for _ in range(4)]
        if not k32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return None
        ticks = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        return float(ticks / 1e7 - 11644473600.0)  # FILETIME counts from 1601
    finally:
        k32.CloseHandle(handle)


def _win_process_table() -> dict[int, tuple[int, str]]:
    """Windows: every process as pid -> (parent pid, lower-case exe name);
    empty when the snapshot cannot be taken."""
    import ctypes
    from ctypes import wintypes

    class _Entry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    k32 = _win_kernel32()
    k32.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(_Entry))
    k32.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(_Entry))
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    table: dict[int, tuple[int, str]] = {}
    if not snap or snap == ctypes.c_void_p(-1).value:
        return table
    try:
        entry = _Entry()
        entry.dwSize = ctypes.sizeof(_Entry)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            table[int(entry.th32ProcessID)] = (
                int(entry.th32ParentProcessID),
                str(entry.szExeFile).lower(),
            )
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return table


def _win_console_ancestor(pid: int) -> int:
    """Windows: the cmd.exe running run-agent.bat for agent `pid` (through
    the py launcher when the bat used it), or 0."""
    table = _win_process_table()
    cur = table.get(pid, (0, ""))[0]
    for _ in range(4):
        parent, exe = table.get(cur, (0, ""))
        if exe == "cmd.exe":
            return cur
        if exe not in ("py.exe", "python.exe", "pythonw.exe"):
            return 0
        cur = parent
    return 0


def _posix_alive(pid: int) -> bool:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        stat = ""
    if stat:
        # A zombie still answers kill(pid, 0); its state letter follows the
        # ")" that closes the command name.
        return stat.rpartition(")")[2].split()[:1] != ["Z"]
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _posix_cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        return raw.replace(b"\0", b" ").decode("utf-8", errors="replace")
    except OSError:
        pass
    try:
        return subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _own_created() -> float:
    if sys.platform == "win32":
        return _process_created(os.getpid()) or 0.0
    return time.time()


def _same_process(rec: Mapping[str, Any]) -> bool:
    """Is the process this record describes still running?

    The PID alone is not enough: after a reboot the number belongs to some
    other program, and "stop the previous agent" would stop that one.
    Windows: the creation time must match what the process recorded about
    itself. Elsewhere: its command line must still name alb_agent.py."""
    try:
        pid = int(rec.get("pid") or 0)
        recorded = float(rec.get("created") or 0)
    except (TypeError, ValueError):
        return False
    if pid <= 0 or pid == os.getpid():
        return False
    if sys.platform == "win32":
        created = _process_created(pid)
        return created is not None and abs(created - recorded) < 2.0
    return _posix_alive(pid) and _BUNDLE_MAIN in _posix_cmdline(pid)


def _tree_to_kill(table: Mapping[int, tuple[int, str]], root: int, spare: int) -> list[int]:
    """`root` and its descendants, children before parents — minus `spare`
    and everything under it.

    The update helper is a CHILD of the agent it stops (the agent started
    it). `taskkill /T` follows exactly that link, so a forced stop would kill
    the helper in the middle of an update and leave no agent running."""
    children: dict[int, list[int]] = {}
    for child, (parent, _exe) in table.items():
        if child != parent:
            children.setdefault(parent, []).append(child)
    order: list[int] = []
    seen: set[int] = set()

    def walk(pid: int) -> None:
        if pid == spare or pid in seen:
            return
        seen.add(pid)
        for child in children.get(pid, []):
            walk(child)
        order.append(pid)

    walk(root)
    return order


def _win_terminate(pid: int) -> None:
    from ctypes import wintypes

    k32 = _win_kernel32()
    k32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    handle = k32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
    if not handle:
        return
    try:
        k32.TerminateProcess(handle, 1)
    finally:
        k32.CloseHandle(handle)


def _kill_tree(pid: int) -> None:
    if sys.platform == "win32":
        # No taskkill: its /T would take this process too (see
        # _tree_to_kill), and from a window-less helper every call would
        # flash a console window.
        try:
            targets = _tree_to_kill(_win_process_table(), pid, os.getpid())
        except Exception:
            targets = [pid]
        for target in targets or [pid]:
            with contextlib.suppress(Exception):
                _win_terminate(target)
        return
    with contextlib.suppress(OSError):
        os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and _posix_alive(pid):
        time.sleep(0.1)
    if _posix_alive(pid):
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGKILL)


# ── what a start that never got anywhere left on screen ──────────────


def _process_table() -> dict[int, tuple[int, str]]:
    """pid -> (parent pid, lower-case name), Windows or /proc; empty
    elsewhere."""
    if sys.platform == "win32":
        return _win_process_table()
    table: dict[int, tuple[int, str]] = {}
    proc = Path("/proc")
    if not proc.is_dir():
        return table
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        name = stat[stat.find("(") + 1 : stat.rfind(")")]
        fields = stat.rpartition(")")[2].split()
        if len(fields) > 1 and fields[1].isdigit():
            table[int(entry.name)] = (int(fields[1]), name.lower())
    return table


def _win_cmdline(pid: int) -> str:
    """Windows: the command line of the live process `pid`, or "".

    The exe name cannot tell run-agent.bat's `python -c` checks from the
    agent itself — all are python.exe."""
    import ctypes
    from ctypes import wintypes

    class _UnicodeString(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.USHORT),
            ("MaximumLength", wintypes.USHORT),
            ("Buffer", ctypes.c_void_p),
        ]

    query = ctypes.WinDLL("ntdll").NtQueryInformationProcess  # type: ignore[attr-defined]
    query.restype = ctypes.c_long
    query.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.ULONG,
        ctypes.POINTER(wintypes.ULONG),
    )
    k32 = _win_kernel32()
    handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        size = wintypes.ULONG(0)
        query(handle, 60, None, 0, ctypes.byref(size))  # 60 = ProcessCommandLineInformation
        if not size.value:
            return ""
        buf = ctypes.create_string_buffer(size.value)
        if query(handle, 60, buf, size.value, ctypes.byref(size)) != 0:
            return ""
        text = _UnicodeString.from_buffer(buf)  # Buffer points into `buf`
        if not text.Buffer or not text.Length:
            return ""
        return ctypes.wstring_at(text.Buffer, text.Length // 2)
    finally:
        k32.CloseHandle(handle)


def _describe_tree(
    table: Mapping[int, tuple[int, str]],
    root: int,
    created_of: Callable[[int], float | None],
    cmdline_of: Callable[[int], str],
    now: float,
) -> list[str]:
    """`root` and what runs under it, parents first, one line each: how long
    it has run and its command line. Empty when `root` is gone."""
    if root not in table:
        return []
    children: dict[int, list[int]] = {}
    for child, (parent, _name) in table.items():
        if child != parent:
            children.setdefault(parent, []).append(child)
    lines: list[str] = []
    seen: set[int] = set()

    def walk(pid: int, depth: int) -> None:
        if pid in seen or len(lines) >= 30:
            return
        seen.add(pid)
        created = created_of(pid)
        age = f", {now - created:.0f}s old" if created else ""
        # Verbatim but on one line: the log is line-based.
        cmd = re.sub(r"[\x00-\x1f]", " ", cmdline_of(pid)).strip()
        if len(cmd) > 300:
            cmd = cmd[:300] + " …"
        lines.append(
            f"{'  ' * depth}pid {pid} {table[pid][1]}{age}: {cmd or '(command line unreadable)'}"
        )
        for child in sorted(children.get(pid, [])):
            # A recycled pid: something older than its "parent" is not its child.
            child_created = created_of(child)
            if created and child_created and child_created < created - 1.0:
                continue
            walk(child, depth + 1)

    walk(root, 0)
    return lines


def _win_console_tail(pid: int, rows: int) -> list[str]:
    """Windows: the last `rows` non-blank lines of the console window that
    process `pid` runs in — what someone at the screen would read. Raises
    OSError when the window cannot be read.

    Needs a caller with no console of its own (the update helper has none);
    detaches again before returning."""
    import ctypes
    from ctypes import wintypes

    class _Coord(ctypes.Structure):
        _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]

    class _Rect(ctypes.Structure):
        _fields_ = [(n, wintypes.SHORT) for n in ("Left", "Top", "Right", "Bottom")]

    class _BufferInfo(ctypes.Structure):
        _fields_ = [
            ("dwSize", _Coord),
            ("dwCursorPosition", _Coord),
            ("wAttributes", wintypes.WORD),
            ("srWindow", _Rect),
            ("dwMaximumWindowSize", _Coord),
        ]

    last_error = ctypes.get_last_error  # type: ignore[attr-defined]
    k32 = _win_kernel32()
    k32.AttachConsole.argtypes = (wintypes.DWORD,)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    k32.GetConsoleScreenBufferInfo.argtypes = (wintypes.HANDLE, ctypes.POINTER(_BufferInfo))
    k32.ReadConsoleOutputCharacterW.argtypes = (
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        _Coord,
        ctypes.POINTER(wintypes.DWORD),
    )
    if not k32.AttachConsole(pid):
        raise OSError(f"cannot attach to its window (error {last_error()})")
    try:
        out = k32.CreateFileW("CONOUT$", 0xC0000000, 0x3, None, 3, 0, None)  # read+write, shared
        if not out or out == ctypes.c_void_p(-1).value:
            raise OSError(f"cannot open its window (error {last_error()})")
        try:
            info = _BufferInfo()
            if not k32.GetConsoleScreenBufferInfo(out, ctypes.byref(info)):
                raise OSError(f"cannot read its window (error {last_error()})")
            width, last = int(info.dwSize.X), int(info.dwCursorPosition.Y)
            text: list[str] = []
            # Long lines wrap onto several rows; read more rows than wanted.
            for y in range(max(0, last - 4 * rows), last + 1):
                buf = ctypes.create_unicode_buffer(width)
                got = wintypes.DWORD(0)
                k32.ReadConsoleOutputCharacterW(out, buf, width, _Coord(0, y), ctypes.byref(got))
                text.append(buf.value[: got.value].rstrip())
        finally:
            k32.CloseHandle(out)
    finally:
        k32.FreeConsole()
    text = [t for t in text if t.strip()]
    return text[-rows:]


def _within(seconds: float, fn: Callable[[], Any]) -> Any:
    """fn()'s result, its exception, or TimeoutError — never a wait longer
    than `seconds`. The helper still has to start the old build after this."""
    box: list[Any] = [TimeoutError(f"no answer within {seconds:.0f}s")]

    def run() -> None:
        try:
            box[0] = fn()
        except Exception as e:  # reported by the caller, never raised there
            box[0] = e

    worker = threading.Thread(target=run, name="stalled-start-report", daemon=True)
    worker.start()
    worker.join(seconds)
    return box[0]


def _record_stalled_start(
    proc: subprocess.Popen[bytes], instance: Path, build: str, say: Callable[[str], None]
) -> None:
    """Write down where a start that never reached the hub stands, before
    anything is stopped: its instance record, the processes under it and
    the last lines of its window.

    2026-10-20 on the bench: a new build wrote no log line and never dialed
    the hub; the helper closed its window after 60s and the only account
    of why went with it. The same files started fine the next day."""
    rec = _read_json(instance)
    if rec.get("build") == build:
        state = "running" if _same_process(rec) else "gone"
        say(
            f"instance record: build {build}, pid {rec.get('pid')} ({state}), "
            f"connected_at {rec.get('connected_at')}"
        )
    elif not rec:
        say(
            f"instance record: none — build {build} either never got as far as writing "
            "one (that comes after its first log line) or removed it when it exited"
        )
    else:
        say(
            f"instance record: build {rec.get('build')}'s — build {build} never wrote "
            "its own (that comes after its first log line)"
        )
    if sys.platform == "win32":
        created_of: Callable[[int], float | None] = _process_created
        cmdline_of: Callable[[int], str] = _win_cmdline
    else:
        created_of, cmdline_of = (lambda _pid: None), (lambda pid: _posix_cmdline(pid).strip())
    tree = _within(
        10.0,
        lambda: _describe_tree(_process_table(), proc.pid, created_of, cmdline_of, time.time()),
    )
    if isinstance(tree, BaseException):
        say(f"processes under the start (pid {proc.pid}): not read — {tree}")
    elif not tree:
        say(f"processes under the start (pid {proc.pid}): none left")
    else:
        say(f"processes under the start (pid {proc.pid}):")
        for line in tree:
            say(f"  {line}")
    if sys.platform != "win32":
        return
    window = _within(5.0, lambda: _win_console_tail(proc.pid, 40))
    if isinstance(window, BaseException):
        say(f"its window: not read — {window}")
    elif not window:
        say("its window: empty")
    else:
        say(f"its window, last {len(window)} lines:")
        for line in window:
            say(f"  | {line}")


def _stop_flag(state: Path, pid: int) -> Path:
    return state / f"stop-{pid}"


def _stop_instance(
    state: Path,
    rec: Mapping[str, Any],
    say: Callable[[str], None],
    grace_s: float = _STOP_GRACE_S,
) -> bool:
    """Ask the process `rec` describes to stop; kill it if it does not.
    True once it is gone.

    No time limit while it reports a job in progress: killing fastboot
    halfway through a partition write costs more than waiting for it."""
    if not _same_process(rec):
        return True
    pid = int(rec["pid"])
    flag = _stop_flag(state, pid)
    busy = flag.with_name(flag.name + ".busy")
    with contextlib.suppress(OSError):
        state.mkdir(parents=True, exist_ok=True)
        flag.touch()
    say(f"asked pid {pid} to stop")
    deadline = time.monotonic() + grace_s
    noted = 0.0
    while _same_process(rec):
        now = time.monotonic()
        if busy.exists():
            deadline = now + grace_s
            if now - noted > 30.0:
                say(f"pid {pid} is finishing a running job first — waiting for it")
                noted = now
        elif now > deadline:
            say(f"pid {pid} did not stop within {grace_s:.0f}s — killing it")
            console = int(rec.get("console_pid") or 0)
            # Its window too: a killed agent leaves run-agent.bat at "press
            # any key", reading a file that is about to be replaced.
            if (
                console
                and sys.platform == "win32"
                and _same_process({"pid": console, "created": rec.get("console_created")})
            ):
                _kill_tree(console)
            _kill_tree(pid)
            break
        time.sleep(0.25)
    end = time.monotonic() + 5.0
    while time.monotonic() < end and _same_process(rec):
        time.sleep(0.1)
    for leftover in (flag, busy):
        with contextlib.suppress(OSError):
            leftover.unlink(missing_ok=True)
    return not _same_process(rec)


def _spawn(
    cmd: list[str] | str,
    *,
    console: bool,
    cwd: Path,
    env: Mapping[str, str] | None = None,
) -> subprocess.Popen[bytes]:
    """Start a process that outlives this one. `console`: a window of its own
    (the agent); otherwise none at all (the update helper)."""
    if sys.platform == "win32":
        flags = _CREATE_NEW_CONSOLE if console else (_DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP)
        quiet: dict[str, Any] = (
            {}
            if console
            else {
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            }
        )
        try:
            # Out of the job object this process may run in (Task Scheduler
            # starts tasks in one), so ending this process's task cannot take
            # the new process with it. Not every job allows that: retry inside.
            return subprocess.Popen(
                cmd,
                creationflags=flags | _CREATE_BREAKAWAY_FROM_JOB,
                cwd=cwd,
                env=env,
                close_fds=True,
                **quiet,
            )
        except OSError:
            return subprocess.Popen(
                cmd, creationflags=flags, cwd=cwd, env=env, close_fds=True, **quiet
            )
    return subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


# ── this process's record ────────────────────────────────────────────


def _claim_instance(args: argparse.Namespace) -> None:
    """Stop the previous instance of THIS agent, then record this one."""
    global _INSTANCE_PATH, _INSTANCE, _LAST_UPDATE
    state = _state_dir(_install_dir())
    try:
        state.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        _log.warning(
            "cannot create %s (%s) — previous instances are not stopped and remote "
            "update is unavailable",
            state,
            e,
        )
        return
    path = state / f"instance-{_instance_key(args.config_used, args.agent_id)}.json"
    old = _read_json(path)
    if old and _same_process(old):
        _log.info(
            "the previous instance of this agent is still running (pid %s) — stopping it first",
            old.get("pid"),
        )
        if not _stop_instance(state, old, _log.info):
            sys.exit(
                f"could not stop the previous instance (pid {old.get('pid')}) — "
                "stop it by hand, then start this one again"
            )
    created = _own_created()
    if not created:
        # A record with no creation time never matches _same_process: the
        # next start would not stop this instance, and an update helper
        # could never see it come back. Better no record at all.
        _log.warning(
            "cannot read this process's start time — previous-instance stop and "
            "remote update are unavailable"
        )
        return
    launcher = os.environ.get(_LAUNCHER_ENV, "")
    rec: dict[str, Any] = {
        "pid": os.getpid(),
        "created": created,
        "build": _BUILD,
        "config": args.config_used,
        "launcher": launcher,
        "started_at": time.time(),
        "connected_at": 0.0,
    }
    if sys.platform == "win32" and launcher == _LAUNCHER_BAT:
        with contextlib.suppress(Exception):
            console = _win_console_ancestor(os.getpid())
            if console:
                rec["console_pid"] = console
                rec["console_created"] = _process_created(console) or 0.0
    try:
        _write_json(path, rec)
    except OSError as e:
        _log.warning("cannot write %s (%s) — remote update is unavailable", path, e)
        return
    _INSTANCE_PATH, _INSTANCE = path, rec
    _LAST_UPDATE = _read_json(state / "update-result.json")


def _mark_connected() -> None:
    """The signal the update helper waits for: this build reached the hub."""
    if _INSTANCE_PATH is None:
        return
    _INSTANCE["connected_at"] = time.time()
    with contextlib.suppress(OSError):
        _write_json(_INSTANCE_PATH, _INSTANCE)


def _release_instance() -> None:
    if _INSTANCE_PATH is None:
        return
    flag = _stop_flag(_INSTANCE_PATH.parent, os.getpid())
    for leftover in (flag, flag.with_name(flag.name + ".busy")):
        with contextlib.suppress(OSError):
            leftover.unlink(missing_ok=True)
    if _read_json(_INSTANCE_PATH).get("pid") == os.getpid():
        with contextlib.suppress(OSError):
            _INSTANCE_PATH.unlink()


async def _watch_stop_requests() -> None:
    """Stop when a newer instance (or the update helper) asks."""
    global _STOP_REASON, _EXIT_CODE
    if _INSTANCE_PATH is None:
        return
    flag = _stop_flag(_INSTANCE_PATH.parent, os.getpid())
    while not _shutdown.is_set():
        await asyncio.sleep(_STOP_POLL_S)
        if not await asyncio.to_thread(flag.exists):
            continue
        _log.info("a newer instance of this agent asked this one to stop")
        _STOP_REASON = _STOP_REASON or "this agent is being replaced by a newer instance"
        if _job_lock.locked():
            with contextlib.suppress(OSError):
                await asyncio.to_thread(flag.with_name(flag.name + ".busy").touch)
            _log.info("waiting for the running job to finish before stopping")
        # Taken and never released: no job can start between here and exit.
        await _job_lock.acquire()
        _EXIT_CODE = _EXIT_REPLACED
        _shutdown.set()
        return


# ── builtin: agent-update ────────────────────────────────────────────


def _bundle_member_ok(name: str) -> bool:
    if not _BUNDLE_NAME_RE.match(name):
        return False
    return name == _BUNDLE_CONFIG or name.endswith(_BUNDLE_SUFFIXES)


def _reject_bundle(why: str) -> _JobRejected:
    return _JobRejected("AGENT_UPDATE_REJECTED", f"{why} — nothing was changed")


def _unpack_bundle(bundle: Path, dest: Path) -> list[str]:
    """Unpack a pushed bundle into `dest` (emptied first); return the names.
    Anything but a flat set of allowed names is refused whole."""
    try:
        zf = zipfile.ZipFile(bundle)
    except (zipfile.BadZipFile, OSError) as e:
        raise _reject_bundle(f"the bundle is not a zip archive ({e})") from e
    with zf:
        infos = zf.infolist()
        if len(infos) > _BUNDLE_MAX_FILES:
            raise _reject_bundle(f"the bundle has {len(infos)} entries, over {_BUNDLE_MAX_FILES}")
        seen: set[str] = set()
        total = 0
        for info in infos:
            name = info.filename
            if info.is_dir() or not _bundle_member_ok(name):
                raise _reject_bundle(f"bundle entry {name!r} is not an allowed file name")
            # Windows file names ignore case: two entries differing only in
            # case would overwrite each other there.
            if name.lower() in seen:
                raise _reject_bundle(f"bundle entry {name!r} appears twice")
            seen.add(name.lower())
            total += info.file_size
            if total > _BUNDLE_MAX_BYTES:
                raise _reject_bundle(f"the bundle unpacks to more than {_BUNDLE_MAX_BYTES} bytes")
        names = [i.filename for i in infos]
        if _BUNDLE_MAIN not in names:
            raise _reject_bundle(f"the bundle has no {_BUNDLE_MAIN}")
        shutil.rmtree(dest, ignore_errors=True)
        dest.mkdir(parents=True)
        for info in infos:
            try:
                data = zf.read(info)  # checks the CRC
            except (zipfile.BadZipFile, OSError) as e:
                raise _reject_bundle(f"bundle entry {info.filename!r} is damaged ({e})") from e
            (dest / info.filename).write_bytes(data)
    return sorted(names)


def _changed_files(
    staged: Path, install: Path, files: list[str], config_target: Path | None
) -> list[str]:
    def differs(new: Path, old: Path) -> bool:
        try:
            return new.read_bytes() != old.read_bytes()
        except OSError:
            return True

    changed = [n for n in files if differs(staged / n, install / n)]
    if config_target is not None and differs(staged / _BUNDLE_CONFIG, config_target):
        changed.append(_BUNDLE_CONFIG)
    return changed


def _without_option(argv: list[str], name: str) -> list[str]:
    """argv minus `name VALUE` / `name=VALUE`."""
    out: list[str] = []
    skip = False
    for arg in argv:
        if skip:
            skip = False
            continue
        if arg == name:
            skip = True
            continue
        if arg.startswith(name + "="):
            continue
        out.append(arg)
    return out


def _relaunch_argv() -> list[str]:
    """This process's arguments, with the config path made absolute: the
    restart runs from another working directory."""
    argv = _without_option(list(_LAUNCH_ARGV), "--config")
    if _CONFIG_EXPLICIT and _CONFIG_USED:
        argv += ["--config", str(Path(_CONFIG_USED).resolve())]
    return argv


def _relaunch_mode(install: Path) -> str:
    """Restart the way it was started: through run-agent.bat (its own window,
    its environment checks) when that is how this one came up."""
    if (
        sys.platform == "win32"
        and os.environ.get(_LAUNCHER_ENV) == _LAUNCHER_BAT
        and (install / _LAUNCHER_BAT).is_file()
    ):
        return "bat"
    return "python"


async def _self_check(script: Path, config: Path | None, argv: list[str]) -> dict[str, Any]:
    """Run the NEW build's --self-check against the config it will use.

    The last moment a broken bundle (syntax error, missing dependency, bad
    config key, no hub_url) costs nothing: the old build is still running
    and still connected."""
    cmd = [sys.executable, str(script), "--self-check", *_without_option(argv, "--config")]
    if config is not None:
        cmd += ["--config", str(config)]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(script.parent),
    )
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), _SELF_CHECK_TIMEOUT_S)
    except TimeoutError:
        await _reap(proc)
        raise _JobRejected(
            "AGENT_UPDATE_CHECK_FAILED",
            f"the new build's self-check did not finish within {_SELF_CHECK_TIMEOUT_S:.0f}s "
            "— nothing was changed",
        ) from None
    except BaseException:
        await _reap(proc)
        raise
    out = out_b.decode("utf-8", errors="replace")
    err = err_b.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        detail = (err.strip() or out.strip() or f"exit code {proc.returncode}")[-2000:]
        raise _JobRejected(
            "AGENT_UPDATE_CHECK_FAILED",
            f"the new build failed its self-check — nothing was changed: {detail}",
        )
    for line in reversed(out.splitlines()):
        with contextlib.suppress(ValueError):
            data = json.loads(line)
            if isinstance(data, dict):
                return data
    raise _JobRejected(
        "AGENT_UPDATE_CHECK_FAILED",
        "the new build's self-check printed no result — nothing was changed",
    )


def _update_stalled() -> None:
    global _STOP_REASON
    if not _shutdown.is_set() and _STOP_REASON.startswith("this agent is restarting"):
        _log.warning(
            "the update helper never asked this agent to stop — carrying on with build %s "
            "(see .alb-agent/update.log)",
            _BUILD,
        )
        _STOP_REASON = ""


async def _op_agent_update(_spec: OpSpec, values: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    global _STOP_REASON
    if _INSTANCE_PATH is None:
        raise _JobRejected(
            "AGENT_UPDATE_REJECTED",
            "this agent could not record itself at start-up (see its log), so it "
            "cannot restart itself — nothing was changed",
        )
    if not _IDENTITY_STABLE:
        raise _JobRejected(
            "AGENT_UPDATE_REJECTED",
            "this agent has no fixed identity (no agent_id in agent.conf, no --agent-id), "
            "so its restarted copy could not be recognised — set agent_id; nothing was changed",
        )
    install = _install_dir()
    state = _state_dir(install)
    staged = state / "staged"
    names = await asyncio.to_thread(_unpack_bundle, Path(values["image"]), staged)
    has_config = _BUNDLE_CONFIG in names
    if has_config and not _CONFIG_USED:
        # Running on flags alone: a new agent.conf would be read on restart
        # (the default path) and could change who this agent is.
        raise _reject_bundle("this agent runs without a config file, so it takes no agent.conf")
    code_files = [n for n in names if n != _BUNDLE_CONFIG]
    config_target = Path(_CONFIG_USED) if _CONFIG_USED else install / _BUNDLE_CONFIG
    changed = await asyncio.to_thread(
        _changed_files, staged, install, code_files, config_target if has_config else None
    )
    to_build = await asyncio.to_thread(_file_build, staged / _BUNDLE_MAIN)
    summary: dict[str, Any] = {
        "from_build": _BUILD,
        "to_build": to_build,
        "changed": changed,
        "config": has_config,
    }
    if not changed:
        await asyncio.to_thread(shutil.rmtree, staged, True)
        return f"already running these files (build {_BUILD}); nothing to do", {
            **summary,
            "restarting": False,
        }
    check_config = (
        staged / _BUNDLE_CONFIG if has_config else (Path(_CONFIG_USED) if _CONFIG_USED else None)
    )
    check = await _self_check(staged / _BUNDLE_MAIN, check_config, _LAUNCH_ARGV)
    if has_config and (check.get("agent_id") != _AGENT_ID or not check.get("agent_id_fixed")):
        # The hub waits for THIS agent_id to come back; under another one
        # (or a random one) a good update would read as "never returned".
        raise _reject_bundle(
            f"the pushed agent.conf would make this agent {check.get('agent_id')!r} "
            f"instead of {_AGENT_ID!r} — keep agent_id the same"
        )

    update_id = uuid.uuid4().hex[:12]
    plan = {
        "id": update_id,
        "install_dir": str(install),
        "staged_dir": str(staged),
        "files": code_files,
        "changed": changed,
        "config_target": str(config_target) if has_config else "",
        "instance_file": str(_INSTANCE_PATH),
        "old": dict(_INSTANCE),
        "from_build": _BUILD,
        "to_build": to_build,
        "relaunch": {
            "mode": _relaunch_mode(install),
            "python": sys.executable,
            "argv": _relaunch_argv(),
        },
        "health_timeout_s": _UPDATE_HEALTH_TIMEOUT_S,
        "settle_s": _UPDATE_SETTLE_S,
    }
    plan_path = state / "update-plan.json"
    runner = state / "apply-update.py"
    await asyncio.to_thread(_write_json, plan_path, plan)
    await asyncio.to_thread(shutil.copyfile, _own_file(), runner)
    _STOP_REASON = "this agent is restarting to apply an update"
    try:
        await asyncio.to_thread(
            _spawn,
            [sys.executable, str(runner), "--apply-update", str(plan_path)],
            console=False,
            cwd=state,
        )
    except OSError as e:
        _STOP_REASON = ""
        raise _JobRejected(
            "AGENT_UPDATE_REJECTED", f"cannot start the update helper ({e}) — nothing was changed"
        ) from e
    asyncio.get_running_loop().call_later(_UPDATE_STALL_S, _update_stalled)
    _log.info(
        "update %s staged: build %s -> %s, changed %s; the agent restarts now",
        update_id,
        _BUILD,
        to_build,
        ", ".join(changed),
    )
    return (
        f"build {_BUILD} -> {to_build}: {len(changed)} file(s) change; "
        "the agent restarts now and reconnects",
        {**summary, "restarting": True, "update_id": update_id},
    )


# ── the update helper (`alb_agent.py --apply-update <plan>`) ─────────


def _update_logger(path: Path) -> Callable[[str], None]:
    with contextlib.suppress(OSError):
        if path.stat().st_size > 1_000_000:
            path.write_bytes(path.read_bytes()[-200_000:])

    def say(msg: str) -> None:
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} [helper pid {os.getpid()}] {msg}\n"
        with contextlib.suppress(OSError), path.open("a", encoding="utf-8") as fh:
            fh.write(line)

    return say


def _put_file(src: Path, dst: Path) -> None:
    tmp = dst.with_name(dst.name + ".alb-new")
    shutil.copy2(src, tmp)
    _replace_retry(tmp, dst)


def _backup_files(
    install: Path, files: list[str], config_target: Path | None, backup: Path
) -> dict[str, Any]:
    shutil.rmtree(backup, ignore_errors=True)
    backup.mkdir(parents=True)
    existed: dict[str, bool] = {}
    for name in files:
        src = install / name
        existed[name] = src.is_file()
        if existed[name]:
            shutil.copy2(src, backup / name)
    manifest: dict[str, Any] = {"files": existed, "config": None}
    if config_target is not None:
        manifest["config"] = config_target.is_file()
        if manifest["config"]:
            shutil.copy2(config_target, backup / (_BUNDLE_CONFIG + ".previous"))
    _write_json(backup / "manifest.json", manifest)
    return manifest


def _install_files(
    staged: Path, install: Path, files: list[str], config_target: Path | None
) -> None:
    for name in files:
        _put_file(staged / name, install / name)
    if config_target is not None:
        _put_file(staged / _BUNDLE_CONFIG, config_target)


def _restore_files(
    install: Path,
    backup: Path,
    manifest: Mapping[str, Any],
    config_target: Path | None,
    say: Callable[[str], None],
) -> None:
    targets = [(install / n, backup / n, bool(e)) for n, e in manifest["files"].items()]
    if config_target is not None and manifest.get("config") is not None:
        targets.append(
            (config_target, backup / (_BUNDLE_CONFIG + ".previous"), bool(manifest["config"]))
        )
    for dst, saved, existed in targets:
        try:
            if existed:
                _put_file(saved, dst)
            else:
                dst.unlink(missing_ok=True)
        except OSError as e:
            say(f"could not restore {dst.name}: {e}")


def _launch_agent(install: Path, relaunch: Mapping[str, Any]) -> subprocess.Popen[bytes]:
    argv = [str(a) for a in relaunch.get("argv") or []]
    env = dict(os.environ)
    if relaunch.get("mode") == "bat":
        bat = install / _LAUNCHER_BAT
        # /s: cmd strips exactly the outer pair of quotes, so quoted
        # arguments inside survive.
        line = f'cmd.exe /d /s /c "{subprocess.list2cmdline([str(bat), *argv])}"'
        return _spawn(line, console=True, cwd=install, env=env)
    env.pop(_LAUNCHER_ENV, None)
    python = str(relaunch.get("python") or sys.executable)
    return _spawn([python, str(install / _BUNDLE_MAIN), *argv], console=True, cwd=install, env=env)


def _launch_and_wait(
    plan: Mapping[str, Any],
    build: str,
    say: Callable[[str], None],
    *,
    stop_on_timeout: bool,
) -> bool:
    """Start the agent in the install folder; True once THAT build has
    reached the hub (its record says connected after this start)."""
    install = Path(plan["install_dir"])
    instance = Path(plan["instance_file"])
    timeout = float(plan.get("health_timeout_s") or _UPDATE_HEALTH_TIMEOUT_S)
    since = time.time()
    try:
        proc = _launch_agent(install, plan["relaunch"])
    except OSError as e:
        say(f"could not start the agent: {e}")
        return False
    say(f"started build {build} ({plan['relaunch'].get('mode')}, pid {proc.pid})")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rec = _read_json(instance)
        if (
            rec.get("build") == build
            and float(rec.get("connected_at") or 0) >= since
            and _same_process(rec)
        ):
            return True
        if proc.poll() is not None and plan["relaunch"].get("mode") != "bat":
            say(f"the agent exited with code {proc.returncode} before reaching the hub")
            break
        time.sleep(0.5)
    # Before the stop below: it closes the window, and with it the reason.
    with contextlib.suppress(Exception):
        _record_stalled_start(proc, instance, build, say)
    if stop_on_timeout:
        rec = _read_json(instance)
        if rec.get("build") == build and _same_process(rec):
            _stop_instance(_state_dir(install), rec, say, grace_s=10.0)
        if proc.poll() is None:
            _kill_tree(proc.pid)
            with contextlib.suppress(Exception):
                proc.wait(timeout=5)
    return False


def _apply_update(plan_path: Path, progress: dict[str, Any]) -> int:
    """Swap the files and confirm the new build reaches the hub; otherwise
    put the old files back and start the old build again.

    `progress` tells _apply_update_main how far this got, so an unplanned
    failure after the old agent is gone still ends with an agent running."""
    plan = _read_json(plan_path)
    if not plan:
        return 2
    # Read every field BEFORE the old agent is stopped: a malformed plan must
    # fail while that agent is still running and connected.
    install = Path(plan["install_dir"])
    state = _state_dir(install)
    say = _update_logger(state / "update.log")
    result_path = state / "update-result.json"
    staged = Path(plan["staged_dir"])
    files = [str(n) for n in plan["files"]]
    config_target = Path(plan["config_target"]) if plan.get("config_target") else None
    from_build, to_build = str(plan["from_build"]), str(plan["to_build"])
    old = dict(plan["old"])
    settle = float(plan.get("settle_s", _UPDATE_SETTLE_S))
    # Not used here — read now only so a bad value fails before the stop.
    _checked = (
        float(plan.get("health_timeout_s") or _UPDATE_HEALTH_TIMEOUT_S),
        str(plan["instance_file"]),
        dict(plan["relaunch"]),
    )
    del _checked

    def result(outcome: str) -> None:
        with contextlib.suppress(OSError):
            _write_json(
                result_path,
                {
                    "id": plan.get("id", ""),
                    "outcome": outcome,
                    "from_build": from_build,
                    "to_build": to_build,
                    "at": time.time(),
                },
            )

    backup = state / "backup"
    progress.update(plan=plan, say=say, result=result, backup=backup, config_target=config_target)
    say(f"update {plan.get('id')}: build {from_build} -> {to_build}, changed {plan.get('changed')}")
    time.sleep(settle)
    if not _stop_instance(state, old, say):
        say("the running agent could not be stopped — update abandoned, nothing was changed")
        return 1
    progress["stopped"] = True
    # run-agent.bat reads itself line by line while it runs: let the stopped
    # agent's window finish its last lines before that file is replaced.
    time.sleep(2.0)
    try:
        manifest = _backup_files(install, files, config_target, backup)
    except OSError as e:
        say(f"backup failed ({e}) — nothing was changed; starting build {from_build} again")
        result("aborted")
        _launch_and_wait(plan, from_build, say, stop_on_timeout=False)
        return 1
    progress["manifest"] = manifest
    result("pending")
    try:
        _install_files(staged, install, files, config_target)
    except OSError as e:
        say(f"installing the new files failed ({e}) — putting the previous ones back")
        _restore_files(install, backup, manifest, config_target, say)
        result("rolled_back")
        _launch_and_wait(plan, from_build, say, stop_on_timeout=False)
        return 1
    started = time.monotonic()
    if _launch_and_wait(plan, to_build, say, stop_on_timeout=True):
        say(f"updated: build {to_build} is connected to the hub")
        result("updated")
        shutil.rmtree(staged, ignore_errors=True)
        return 0
    # The time actually waited, not the timeout: a build that exits at once
    # is given up on after a second or two.
    say(
        f"build {to_build} did not reach the hub (gave up after "
        f"{time.monotonic() - started:.0f}s) — rolling back to {from_build}"
    )
    _restore_files(install, backup, manifest, config_target, say)
    result("rolled_back")
    if _launch_and_wait(plan, from_build, say, stop_on_timeout=False):
        say(f"rolled back: build {from_build} is connected again")
    else:
        say(
            f"rolled back, but build {from_build} has not reached the hub either — "
            "look at the agent window / log on this host"
        )
    return 1


def _recover_after_crash(progress: Mapping[str, Any], say: Callable[[str], None]) -> None:
    """The old agent is gone and something unplanned went wrong: put back
    whatever was replaced and start the old build, each step best effort.
    If a new build is already running, the old one's start-up stops it."""
    plan = progress["plan"]
    if progress.get("manifest") is not None:
        with contextlib.suppress(Exception):
            _restore_files(
                Path(plan["install_dir"]),
                progress["backup"],
                progress["manifest"],
                progress.get("config_target"),
                say,
            )
    with contextlib.suppress(Exception):
        progress["result"]("rolled_back")
    with contextlib.suppress(Exception):
        say(f"starting build {plan['from_build']} again")
        _launch_and_wait(plan, str(plan["from_build"]), say, stop_on_timeout=False)


def _apply_update_main(plan_arg: str) -> int:
    plan_path = Path(plan_arg)
    progress: dict[str, Any] = {}
    try:
        return _apply_update(plan_path, progress)
    except Exception as e:  # last resort: never die silently mid-swap
        say = progress.get("say") or _update_logger(plan_path.parent / "update.log")
        say(f"update helper crashed: {e!r}")
        if progress.get("stopped"):
            _recover_after_crash(progress, say)
        return 3


def _self_check_main(args: argparse.Namespace) -> int:
    """`--self-check`: load the config and build the op table, then report
    and exit. Run by the OLD build on a NEW bundle before anything is
    replaced."""
    _apply_flash_config(args)
    try:
        import serial  # noqa: F401

        pyserial = True
    except ImportError:
        pyserial = False
    print(
        json.dumps(
            {
                "build": _BUILD,
                "ops": list(_OPS),
                "pyserial": pyserial,
                "agent_id": args.agent_id,
                "agent_id_fixed": args.agent_id_explicit,
            }
        )
    )
    return 0


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


async def _close_on_shutdown(ws: Any) -> None:
    """End the session when a shutdown is requested.

    On POSIX the SIGINT / SIGTERM handlers only set `_shutdown` (see
    _install_signal_handlers); nothing raises. The reader loop in
    _run_session sits in `async for raw in ws` and never looks at the
    event, so a stop request to a CONNECTED agent did nothing until the hub
    happened to drop the session. Closing the socket is what ends that loop.
    (Windows takes the KeyboardInterrupt path and never depended on this.)
    """
    await _shutdown.wait()
    with contextlib.suppress(Exception):
        await ws.close()


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
        _log.info("connected to %s as %s (build %s)", args.hub_url, args.agent_id, _BUILD)
        _STATUS.on_connected()
        await asyncio.to_thread(_mark_connected)

        hb = asyncio.create_task(_heartbeat(ws))
        stopper = asyncio.create_task(_close_on_shutdown(ws))
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
            stopper.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await hb
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await stopper
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
    ap.add_argument(
        "--self-check",
        action="store_true",
        help="load the config, build the op table, print a JSON summary and exit "
        "(run on a pushed update before it replaces anything)",
    )

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
    args.agent_id_explicit = bool(args.agent_id)
    args.agent_id = args.agent_id or uuid.uuid4().hex
    args.config_used = str(config_used) if config_used else ""
    args.config_explicit = bool(pre.config)
    return args


_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class _ConsoleLog(logging.Handler):
    """Log lines to the console window from a thread of its own.

    A text selection in a Windows console window blocks every write to it
    until the selection is cleared. Written from the event loop, one stray
    selection froze the whole agent: adb and serial forwarding, jobs, stop
    requests (2026-10-16 on the bench: an update stalled until the helper
    killed it). Now only this thread waits. The log file keeps up; lines
    past the backlog limit are counted and reported when the window moves."""

    _BACKLOG = 5000

    def __init__(self, stream: Any) -> None:
        super().__init__()
        self._stream = stream
        self._lines: queue.Queue[str | None] = queue.Queue(maxsize=self._BACKLOG)
        self._dropped = 0
        self._dropped_lock = threading.Lock()
        self._thread = threading.Thread(target=self._pump, name="console-log", daemon=True)
        self._thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
        except Exception:
            self.handleError(record)
            return
        try:
            self._lines.put_nowait(line)
        except queue.Full:
            with self._dropped_lock:
                self._dropped += 1

    def _pump(self) -> None:
        while (line := self._lines.get()) is not None:
            with self._dropped_lock:
                dropped, self._dropped = self._dropped, 0
            if dropped:
                line = (
                    f"({dropped} log lines were skipped here while this window was "
                    "blocked; the log file, if one is set, has them)\n" + line
                )
            with contextlib.suppress(Exception):  # console closed, encoding
                self._stream.write(line + "\n")
                self._stream.flush()

    def close(self) -> None:
        # Bounded: a window still blocked at exit must not hold the exit up.
        with contextlib.suppress(queue.Full):
            self._lines.put_nowait(None)
        self._thread.join(timeout=2.0)
        super().close()


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
    # Remember where it went: `log-tail agent` reads exactly this file.
    global _AGENT_LOG_PATH
    _AGENT_LOG_PATH = path
    _log.info("logging to %s", path)


def _log_environment(args: argparse.Namespace) -> None:
    """One startup line with everything a post-mortem needs (never the token)."""
    try:
        import serial

        pyserial_ver = getattr(serial, "__version__", "?")
    except ImportError:
        pyserial_ver = "not installed"
    _log.info(
        "starting: build %s · python %s · websockets %s · pyserial %s · %s · config %s",
        _BUILD,
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
    _log.info("ops: %s", ", ".join(_OPS) if _OPS else "none")
    ports = _enumerate_com()
    _log.info(
        "serial ports here: %s",
        ", ".join(p["port"] for p in ports) if ports else "NONE",
    )


def _apply_flash_config(args: argparse.Namespace) -> None:
    """Resolve the flash settings and build the op table once, before
    anything can use them.

    Runs before the first `hello`, because the resolved path is what decides
    whether this agent claims the `fastboot` capability at all — and which
    rows the hello's op table carries."""
    global _FASTBOOT_PATH, _FLASH_PARTITIONS, _OPS
    _FASTBOOT_PATH = _resolve_fastboot(getattr(args, "fastboot_path", None) or "")
    raw = (getattr(args, "flash_partitions", None) or "").strip()
    _FLASH_PARTITIONS = frozenset(p.strip() for p in raw.split(",") if p.strip())
    # The op table follows from the same resolution (ADR-059): fastboot rows
    # only when the tool resolved, log-tail sources from what is configured.
    _OPS = _build_ops(_FASTBOOT_PATH, _log_sources())
    _STATUS.set_ops(list(_OPS))


def main() -> None:
    # The update helper is this same file, copied aside and started with
    # `--apply-update <plan>` (ADR-060); it never runs the agent itself.
    if len(sys.argv) == 3 and sys.argv[1] == "--apply-update":
        sys.exit(_apply_update_main(sys.argv[2]))
    args = _parse_args()
    if args.self_check:
        sys.exit(_self_check_main(args))

    root = logging.getLogger()
    root.setLevel(logging.DEBUG if args.verbose else logging.INFO)
    console = _ConsoleLog(sys.stderr)
    console.setFormatter(logging.Formatter(_LOG_FORMAT))
    root.addHandler(console)
    if args.log_file:
        _setup_file_logging(args.log_file)
    global _LAUNCH_ARGV, _CONFIG_USED, _CONFIG_EXPLICIT, _IDENTITY_STABLE, _AGENT_ID
    _LAUNCH_ARGV = sys.argv[1:]
    _CONFIG_USED = args.config_used
    _CONFIG_EXPLICIT = args.config_explicit
    _IDENTITY_STABLE = args.agent_id_explicit
    _AGENT_ID = args.agent_id
    # Before anything opens a COM port or the status page: the previous
    # instance of this agent holds both until it is gone.
    _claim_instance(args)
    _apply_flash_config(args)
    _log_environment(args)

    try:
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(_run(args))
    finally:
        _release_instance()
    if _EXIT_CODE:
        sys.exit(_EXIT_CODE)


async def _run(args: argparse.Namespace) -> None:
    _install_signal_handlers()
    _STATUS.init(hub_url=args.hub_url, agent_id=args.agent_id, name=args.name)
    httpd = _start_status_server(args.status_host, args.status_port) if args.status_port else None
    watcher = asyncio.create_task(_watch_stop_requests())
    try:
        await _main_loop(args)
    finally:
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await watcher
        if httpd is not None:
            with contextlib.suppress(Exception):
                httpd.shutdown()


if __name__ == "__main__":
    main()
