"""Web API: /uart/stream — live UART byte stream over WebSocket (PR-C.b/c).

Companion to the REST capture endpoints in `uart_route.py`. Where the
REST flow is "press Capture, get an N-second artifact", this WS flow
is "stream raw UART bytes as they arrive, indefinitely". xterm.js on
the frontend renders the bytes (ANSI-aware), so kernel printk +
bootloader output keep their colour codes.

Protocol:

    C → S (first JSON, optional, 1.5 s timeout):
        {"device": "<serial>", "write": false}
        - device: passed through to build_transport(device_serial=...)
        - write:  if true, opens a single shared link (PR-C.c
                  bidirectional mode); raw client bytes get written
                  back to the UART. Default false → read-only stream
                  via stream_read iterator (PR-C.b v1 behaviour).

    S → C (JSON, on accept):
        {"type": "ready", "device": "...", "transport": "serial",
         "write": false|true}

    S → C (binary frames):
        Raw UART bytes (verbatim).

    C → S (binary frames, **only when `write=true`**):
        Raw bytes to write to the UART. Forwarded to link.writer.write
        + drain. PR-C.c lets the user interrupt u-boot, type into a
        fastboot prompt, send kernel sysrq, etc.

    S → C (JSON, on stream end / error):
        {"type": "closed", "reason": "...", "error": "..."}

    C → S (JSON, optional control):
        {"type": "close"}  → server shuts down the stream cleanly

The server forces `transport=serial` regardless of the env-default
because UART is the whole point of this endpoint. If serial isn't
configured (no `/dev/ttyUSB*` discoverable, no ALB_TRANSPORT=serial),
build_transport raises and the WS closes with reason='init_failed'.

Bidirectional mode (PR-C.c) requires SerialTransport.open_session()
because two concurrent _open calls to the same physical UART (or the
same single-client ser2net endpoint) would EBUSY/refuse. The shared
link path keeps read+write on one physical channel.
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
    write_enabled = bool(config.get("write"))

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

    # Bidirectional mode needs SerialTransport.open_session — refuse
    # write upgrades against any transport that doesn't expose it
    # (e.g. a future Hybrid that proxies stream_read but not raw IO).
    if write_enabled and not hasattr(transport, "open_session"):
        await ws.send_json({
            "type": "closed",
            "reason": "write_unsupported",
            "error": (
                f"transport {type(transport).__name__} does not support "
                "bidirectional UART write"
            ),
        })
        with contextlib.suppress(Exception):
            await ws.close()
        return

    await ws.send_json(
        {
            "v": API_VERSION,
            "type": "ready",
            "device": device or "",
            "transport": getattr(transport, "name", "serial"),
            "write": write_enabled,
        }
    )

    if write_enabled:
        await _run_bidirectional(ws, transport)
    else:
        await _run_read_only(ws, transport)


async def _run_read_only(ws: WebSocket, transport: Any) -> None:
    """v1 PR-C.b path — stream_read iterator, no write."""
    pump_task = asyncio.create_task(_pump_uart_to_ws(ws, transport))
    recv_task = asyncio.create_task(_recv_loop(ws, link=None))
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


async def _run_bidirectional(ws: WebSocket, transport: Any) -> None:
    """PR-C.c path — single shared link; writer used for client→UART."""
    try:
        link = await transport.open_session()
    except Exception as e:  # noqa: BLE001
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

    pump_task = asyncio.create_task(_pump_link_to_ws(ws, link))
    recv_task = asyncio.create_task(_recv_loop(ws, link=link))
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
            await transport.close_session(link)
        with contextlib.suppress(Exception):
            await ws.send_json({"type": "closed", "reason": "ended"})
        with contextlib.suppress(Exception):
            await ws.close()


async def _pump_uart_to_ws(ws: WebSocket, transport: Any) -> None:
    """Async iterator over UART bytes → ws.send_bytes per chunk.

    Read-only PR-C.b path — uses transport.stream_read which opens its
    own link. Stops on WebSocketDisconnect / iterator exhaustion /
    send error. Errors reported as closed-frame before bubbling.
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


async def _pump_link_to_ws(ws: WebSocket, link: Any) -> None:
    """Bidirectional PR-C.c path — read directly off the shared link's
    StreamReader so the same physical UART can also be written to from
    `_recv_loop`. Stops on read EOF / disconnect / send error."""
    try:
        while True:
            try:
                chunk = await link.reader.read(4096)
            except (ConnectionResetError, OSError) as e:
                with contextlib.suppress(Exception):
                    await ws.send_json({
                        "type": "closed", "reason": "stream_error",
                        "error": f"{type(e).__name__}: {e}",
                    })
                return
            if not chunk:
                return  # EOF — UART closed at the other end
            try:
                await ws.send_bytes(chunk)
            except (WebSocketDisconnect, RuntimeError):
                return
    except (asyncio.CancelledError, WebSocketDisconnect):
        raise


async def _recv_loop(ws: WebSocket, *, link: Any | None = None) -> None:
    """Watch for client-initiated control / data frames.

    Honoured frames:
        {"type":"close"}  → return; outer wait cancels the pump task
        <binary>          → if `link` provided (bidirectional mode),
                            forward to link.writer.write + drain.
                            Silently dropped in read-only mode.
    """
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                return
            # Binary frame — UART input from client (PR-C.c).
            data = msg.get("bytes")
            if data and link is not None:
                try:
                    link.writer.write(data)
                    await link.writer.drain()
                except (ConnectionResetError, OSError) as e:
                    with contextlib.suppress(Exception):
                        await ws.send_json({
                            "type": "closed", "reason": "write_error",
                            "error": f"{type(e).__name__}: {e}",
                        })
                    return
                continue
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
