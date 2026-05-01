"""Web API: /uart/stream — live UART byte stream over WebSocket (PR-C.b).

Companion to the REST capture endpoints in `uart_route.py`. Where the
REST flow is "press Capture, get an N-second artifact", this WS flow
is "stream raw UART bytes as they arrive, indefinitely". xterm.js on
the frontend renders the bytes (ANSI-aware), so kernel printk +
bootloader output keep their colour codes.

Protocol:

    C → S (first JSON, optional, 1.5 s timeout):
        {"device": "<serial>"}

    S → C (JSON, on accept):
        {"type": "ready", "device": "...", "transport": "serial"}

    S → C (binary frames):
        Raw UART bytes (verbatim from SerialTransport.stream_read('uart')).

    S → C (JSON, on stream end / error):
        {"type": "closed", "reason": "...", "error": "..."}

    C → S (JSON, optional control):
        {"type": "close"}  → server shuts down the stream cleanly

The server forces `transport=serial` regardless of the env-default
because UART is the whole point of this endpoint. If serial isn't
configured (no `/dev/ttyUSB*` discoverable, no ALB_TRANSPORT=serial),
build_transport raises and the WS closes with reason='init_failed'.

PR-C.b v1 is read-only — typing into the WS is not piped back to the
UART. Bidirectional UART (poke u-boot, kernel sysrq) lands in a
follow-up.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from alb.api.schema import API_VERSION
from alb.mcp.transport_factory import build_transport

router = APIRouter()


@router.websocket("/uart/stream")
async def uart_stream_ws(ws: WebSocket) -> None:
    await ws.accept()

    config = await _read_config(ws)
    config = config if isinstance(config, dict) else {}
    device = config.get("device")

    try:
        transport = build_transport(override="serial", device_serial=device)
    except Exception as e:  # noqa: BLE001 — surface init errors to client then close
        await ws.send_json(
            {
                "type": "closed",
                "reason": "init_failed",
                "error": f"{type(e).__name__}: {e}",
            }
        )
        with contextlib.suppress(Exception):
            await ws.close()
        return

    await ws.send_json(
        {
            "v": API_VERSION,
            "type": "ready",
            "device": device or "",
            "transport": getattr(transport, "name", "serial"),
        }
    )

    pump_task = asyncio.create_task(_pump_uart_to_ws(ws, transport))
    recv_task = asyncio.create_task(_recv_loop(ws))
    try:
        _, pending = await asyncio.wait(
            {pump_task, recv_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t
    finally:
        with contextlib.suppress(Exception):
            await ws.send_json({"type": "closed", "reason": "ended"})
        with contextlib.suppress(Exception):
            await ws.close()


async def _pump_uart_to_ws(ws: WebSocket, transport: Any) -> None:
    """Async iterator over UART bytes → ws.send_bytes per chunk.

    Stops on WebSocketDisconnect / iterator exhaustion / send error.
    Errors are reported as a closed-frame before bubbling.
    """
    try:
        async for chunk in transport.stream_read("uart"):
            if not chunk:
                continue
            try:
                await ws.send_bytes(chunk)
            except (WebSocketDisconnect, RuntimeError):
                return
    except (asyncio.CancelledError, WebSocketDisconnect):
        raise
    except Exception as e:  # noqa: BLE001 — turn into closed frame, don't crash
        with contextlib.suppress(Exception):
            await ws.send_json(
                {
                    "type": "closed",
                    "reason": "stream_error",
                    "error": f"{type(e).__name__}: {e}",
                }
            )


async def _recv_loop(ws: WebSocket) -> None:
    """Watch for client-initiated close / control frames.

    PR-C.b v1 only honours `{"type":"close"}`. Returning from this
    coroutine is the signal that the WS was closed from the client
    side (the outer asyncio.wait then cancels the pump task)."""
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                return
            text = msg.get("text")
            if text:
                with contextlib.suppress(json.JSONDecodeError):
                    obj = json.loads(text)
                    if isinstance(obj, dict) and obj.get("type") == "close":
                        return
    except WebSocketDisconnect:
        return


async def _read_config(ws: WebSocket) -> dict[str, Any] | None:
    """Optional first-message config (1.5 s timeout). Mirror
    terminal_route to keep the protocol family coherent."""
    try:
        first = await asyncio.wait_for(ws.receive(), timeout=1.5)
    except (asyncio.TimeoutError, WebSocketDisconnect):
        return None
    text = first.get("text") if isinstance(first, dict) else None
    if not text:
        return None
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None
