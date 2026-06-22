"""Web API: dial-home rendezvous for the remote device agent (ADR-050/051).

    /agent/connect  — the agent's persistent SIGNALING WebSocket (control
                      frames only: hello / heartbeat / list_* / channel_*).
    /agent/channel  — a per-channel DATA WebSocket the agent dials back, one
                      per open_channel, carrying raw bytes. Correlated by the
                      ?cid= query param and authenticated by the ?csecret=
                      per-channel secret (DEBT-084) to the pending request.

Security (ADR-050 §6): both endpoints validate the agent token (env
ALB_AGENT_TOKEN; when unset the API is open, same posture as the rest of
alb-api which prints a no-auth banner). The token check happens BEFORE any
registry insert / forwarder attach. tcp channel targets are allowlisted to the
local adb server on both sides.

The adb forwarder binds 127.0.0.1:5037 by default; ALB_ADB_FORWARD_PORT can
override it (tests use 0 for an ephemeral port).
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
import os
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from alb.api.schema import API_VERSION
from alb.remote import protocol
from alb.remote.forwarder import (
    forwarder_status,
    get_adb_forwarder,
    get_serial_forwarder,
    serial_configured,
)
from alb.remote.protocol import ProtocolError, Verb
from alb.remote.registry import AgentConnection, get_agent_registry

_log = logging.getLogger(__name__)
router = APIRouter()

HELLO_TIMEOUT_S = 10.0
HEARTBEAT_TIMEOUT_S = 60.0


def _expected_token() -> str | None:
    return os.environ.get("ALB_AGENT_TOKEN")


def _token_ok(supplied: str | None) -> bool:
    expected = _expected_token()
    if not expected:
        return True  # no token configured = open (dev posture)
    if not supplied:
        return False
    return hmac.compare_digest(supplied, expected)


class _WsDataChannel:
    """DataChannel backed by a dialed-back data WebSocket (raw bytes)."""

    def __init__(self, ws: WebSocket) -> None:
        self._ws = ws
        self._closed = asyncio.Event()

    async def recv(self) -> bytes:
        try:
            msg = await self._ws.receive()
        except (WebSocketDisconnect, RuntimeError):
            return b""
        if msg.get("type") == "websocket.disconnect":
            return b""
        data = msg.get("bytes")
        if isinstance(data, bytes):
            return data
        # a text/control frame on a binary channel = treat as EOF
        return b""

    async def send(self, data: bytes) -> None:
        await self._ws.send_bytes(data)

    async def aclose(self) -> None:
        self._closed.set()

    async def wait_closed(self) -> None:
        await self._closed.wait()


async def _recv_control(ws: WebSocket, timeout: float) -> dict[str, Any] | None:
    """Receive one control frame (JSON text). Returns None on disconnect /
    timeout / malformed frame."""
    try:
        msg = await asyncio.wait_for(ws.receive(), timeout=timeout)
    except (TimeoutError, WebSocketDisconnect):
        return None
    if not isinstance(msg, dict) or msg.get("type") == "websocket.disconnect":
        return None
    text = msg.get("text")
    if text is None:
        return None
    try:
        return protocol.decode_control(text)
    except ProtocolError:
        return None


def _apply_agent_frame(conn: AgentConnection, frame: dict[str, Any]) -> None:
    """Update the agent connection's device cache from an agent→hub control
    frame (adb_list / com_list). Other verbs are advisory (logged by caller)."""
    verb = frame.get("verb")
    if verb == Verb.ADB_LIST.value:
        conn.adb_devices = [str(d) for d in (frame.get("devices") or [])]
    elif verb == Verb.COM_LIST.value:
        conn.com_ports = list(frame.get("ports") or [])


@router.websocket("/agent/connect")
async def agent_connect(ws: WebSocket) -> None:
    await ws.accept()
    registry = get_agent_registry()
    conn: AgentConnection | None = None
    epoch = 0

    try:
        # ── handshake: first frame MUST be hello; auth BEFORE any side effect
        hello = await _recv_control(ws, HELLO_TIMEOUT_S)
        if hello is None or hello.get("verb") != Verb.HELLO.value:
            with contextlib.suppress(Exception):
                await ws.close(code=1002)
            return
        if not _token_ok(hello.get("token")):
            with contextlib.suppress(Exception):
                await ws.close(code=1008)  # policy violation
            return
        agent_id = str(hello.get("agent_id") or "").strip()
        if not agent_id:
            with contextlib.suppress(Exception):
                await ws.close(code=1002)
            return

        async def send_control(m: dict[str, Any]) -> None:
            await ws.send_text(protocol.encode_control(m))

        conn = AgentConnection(
            agent_id=agent_id,
            name=str(hello.get("name") or agent_id),
            version=int(hello.get("agent_version") or 0),
            caps=list(hello.get("caps") or []),
            send_control=send_control,
            registry=registry,
        )
        epoch = await registry.register(conn)
        # The forwarders are PROCESS-LEVEL singletons (ADR-051): attach() binds
        # the OS listener once and is idempotent, so a reconnecting / second
        # agent never re-binds the port (no EADDRINUSE race). They route to the
        # current agent via the registry, so we do NOT detach on disconnect. The
        # serial forwarder only binds when a COM is configured on this hub.
        await get_adb_forwarder().attach()
        if serial_configured():
            await get_serial_forwarder().attach()
        await send_control(protocol.hello_ok(server_version=protocol.PROTOCOL_VERSION))
        # populate the device cache so the Connection Center has data right away
        await conn.request_device_list()

        # ── recv loop: heartbeat + agent→hub control frames
        while True:
            frame = await _recv_control(ws, HEARTBEAT_TIMEOUT_S)
            if frame is None:
                # disconnect, heartbeat timeout, or malformed — end the session
                return
            verb = frame.get("verb")
            if verb == Verb.HEARTBEAT.value:
                continue
            # adb_list / com_list update the device cache; channel_* are advisory
            # (the data-plane correlation happens on /agent/channel).
            _apply_agent_frame(conn, frame)
            _log.debug("agent %s control: %s", agent_id, verb)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        _log.warning("agent_connect error: %s", e)
    finally:
        # teardown on ALL paths (checklist #4): compare-and-clear unregister.
        # The forwarder stays bound (process-level); it just sees no current
        # agent until one reconnects, so new local connections fail fast.
        if conn is not None:
            with contextlib.suppress(Exception):
                await registry.unregister(conn.agent_id, epoch)
        with contextlib.suppress(Exception):
            await ws.close()


@router.websocket("/agent/channel")
async def agent_channel(ws: WebSocket) -> None:
    await ws.accept()
    cid = ws.query_params.get("cid")
    token = ws.query_params.get("token")
    csecret = ws.query_params.get("csecret")
    if not cid or not _token_ok(token):
        with contextlib.suppress(Exception):
            await ws.close(code=1008)
        return

    registry = get_agent_registry()
    channel = _WsDataChannel(ws)
    # The endpoint token gates WHO may dial back; the per-channel secret
    # (DEBT-084) gates WHICH channel this dial-back may claim — only the agent
    # that received the open_channel knows it.
    if not registry.resolve_pending(cid, channel, csecret):
        # unknown / expired cid or wrong secret — do not leave a dangling WS
        with contextlib.suppress(Exception):
            await ws.close(code=1008)
        return

    # The forwarder now owns `channel` and shuttles bytes. Keep this coroutine
    # alive until the forwarder closes the channel (aclose sets the event);
    # only this handler reads the WS via channel.recv(), so no double-read race.
    try:
        await channel.wait_closed()
    finally:
        with contextlib.suppress(Exception):
            await ws.close()


@router.get("/agent/status")
async def agent_status() -> dict[str, Any]:
    """Snapshot for the web Connection Center: connected remote agents (with
    their last-reported devices) + adb/serial forwarder state. Fires a
    fire-and-forget device refresh so the next poll reflects plug/unplug."""
    registry = get_agent_registry()
    await registry.request_device_refresh()
    current = registry.current_agent()
    current_id = current.agent_id if current else None
    agents = [{**a, "current": a["agent_id"] == current_id} for a in registry.list_agents()]
    return {
        "v": API_VERSION,
        "agents": agents,
        "forwarders": forwarder_status(),
    }
