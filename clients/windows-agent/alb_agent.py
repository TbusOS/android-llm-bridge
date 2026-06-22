"""Standalone dial-home device agent.

Runs on the machine that physically holds the device (typically a Windows
host with the device on USB + serial). It dials OUT to the Linux hub over a
single WebSocket — no inbound port, no SSH, no third-party terminal — and lets
the hub reach the local adb server and the device's serial / UART port.

Design: see the hub's ADR-050/051/052. This file is intentionally standalone:
it depends only on the stdlib + `websockets` (+ `pyserial` for serial channels),
NOT the alb package, so it can be dropped onto a bare host. The wire constants
below mirror the hub's `alb.remote.protocol` and MUST stay in lockstep with it.

Usage:
    pip install -r requirements.txt   # websockets + pyserial
    python alb_agent.py --hub-url wss://<hub>/agent/connect --token <token>

The agent maintains the signaling connection (auto-reconnect with backoff) and,
on each `open_channel` from the hub, dials back a separate data connection and
bridges raw bytes to the requested local target.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import signal
import sys
import uuid
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

_shutdown = asyncio.Event()


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
            await _bridge(reader, writer, data_ws)
    except Exception as e:
        _log.warning("channel %s ended: %s", cid[:8], e)
    finally:
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
            await _bridge_serial(ser, data_ws)
    except Exception as e:
        _log.warning("serial channel %s ended: %s", cid[:8], e)
    finally:
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
    with contextlib.suppress(Exception):
        await ws.send(_frame("adb_list", devices=devices))


def _enumerate_com() -> list[dict[str, str]]:
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    return [{"port": p.device, "desc": (p.description or "")} for p in list_ports.comports()]


async def _reply_com_list(ws: Any) -> None:
    ports = await asyncio.to_thread(_enumerate_com)
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
                elif verb == "list_com":
                    # pyserial enumeration is sync → run as its own task so it
                    # doesn't block the reader loop.
                    task = asyncio.create_task(_reply_com_list(ws))
                    channels.add(task)
                    task.add_done_callback(channels.discard)
        finally:
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
        if _shutdown.is_set():
            break
        delay = RECONNECT_BACKOFF_S[min(attempt, len(RECONNECT_BACKOFF_S) - 1)]
        attempt += 1
        _log.info("reconnecting in %.0fs ...", delay)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(_shutdown.wait(), timeout=delay)


def _install_signal_handlers() -> None:
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, ValueError):
            loop.add_signal_handler(sig, _shutdown.set)


def main() -> None:
    ap = argparse.ArgumentParser(description="alb dial-home device agent")
    ap.add_argument("--hub-url", required=True, help="wss://<hub>/agent/connect")
    ap.add_argument(
        "--token", default=None, help="agent auth token (matches ALB_AGENT_TOKEN on the hub)"
    )
    ap.add_argument("--name", default="device-agent", help="human-readable agent name")
    ap.add_argument("--agent-id", default=None, help="stable agent id (default: random)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    args.agent_id = args.agent_id or uuid.uuid4().hex

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_run(args))


async def _run(args: argparse.Namespace) -> None:
    _install_signal_handlers()
    await _main_loop(args)


if __name__ == "__main__":
    main()
