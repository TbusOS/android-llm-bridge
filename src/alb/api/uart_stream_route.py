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
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from alb.api.schema import API_VERSION
from alb.capabilities.logging import _reconnecting_serial_stream
from alb.infra.event_bus import get_bus, make_event
from alb.infra.workspace import is_safe_device
from alb.transport.factory import build_transport


def _safe_device(raw: object) -> str | None:
    """Return raw if it's a safe ASCII serial, else None.

    Forgiving wrapper around `infra.workspace.is_safe_device` — bad input
    becomes None so callers can fall back to "unknown" in audit logs
    without raising. Hard-reject form lives in `workspace_path` (raises
    `InvalidDeviceSerial`) for L-035 enforcement.
    """
    # is_safe_device's isinstance check is enough for the type narrow.
    if is_safe_device(raw):
        assert isinstance(raw, str)  # for mypy / readability
        return raw
    return None

router = APIRouter()

# Per-frame cap on bidirectional client→UART writes.
# Real keystrokes are 1-8 bytes; large pastes split into reasonable
# frames by xterm. A single 64KB frame is ~430 ms of full-duplex 1.5
# Mbaud serial — anything larger is likely a misuse (DoS / paste of
# entire log file) and worth audit-logging + dropping.
# (DEBT-026 / security audit 2026-05-02 LOW 4)
_MAX_WRITE_FRAME_BYTES = 64 * 1024

# Backoff between PR-C.c bidirectional link reconnects on idle EOF.
# Mirrors `_reconnecting_serial_stream`'s 0.5 s constant (no exponential
# growth — we want responsiveness when the bridge starts flowing again,
# and the client closing the WS is the hard cap regardless).
_LINK_RECONNECT_BACKOFF_S = 0.5


@dataclass
class _CloseState:
    """Shared between pump + recv tasks so they DON'T each send their
    own close frame (HIGH 1 from PR-C.c review 2026-05-02). The outer
    finally reads this and sends exactly one close frame."""

    reason: str = "ended"
    error: str | None = None


@router.websocket("/uart/stream")
async def uart_stream_ws(ws: WebSocket) -> None:
    await ws.accept()

    config = await _read_config(ws)
    config = config if isinstance(config, dict) else {}
    # Sanitize device early: it ends up in build_transport, audit-log
    # session_id, and audit data.device. A None fallback is fine —
    # build_transport(device_serial=None) lets serial transport pick
    # its env-default port.
    device = _safe_device(config.get("device"))
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
        await _run_bidirectional(ws, transport, device=device)
    else:
        await _run_read_only(ws, transport, device=device)


async def _run_read_only(
    ws: WebSocket, transport: Any, *, device: str | None = None,
) -> None:
    """v1 PR-C.b path — stream_read iterator, no write."""
    cs = _CloseState()
    pump_task = asyncio.create_task(_pump_uart_to_ws(ws, transport, cs))
    recv_task = asyncio.create_task(_recv_loop(ws, link=None, device=device))
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
        # Single close frame — a mid-stream stream_error (set by the pump)
        # is no longer clobbered by an unconditional `ended` (CODE-2 / L-026).
        payload: dict[str, Any] = {"type": "closed", "reason": cs.reason}
        if cs.error:
            payload["error"] = cs.error
        with contextlib.suppress(Exception):
            await ws.send_json(payload)
        with contextlib.suppress(Exception):
            await ws.close()


async def _run_bidirectional(
    ws: WebSocket, transport: Any, *, device: str | None = None,
) -> None:
    """PR-C.c path — single shared link; writer used for client→UART.

    Reconnect-on-EOF: TCP UART bridges close the client connection when
    the COM port goes idle. Instead of tearing the WS down on the first
    EOF, we close the dead link, open a fresh session, and re-spawn
    both pump + recv tasks. The loop exits when:

    * recv side finishes (client sent ``{"type":"close"}`` or
      disconnected) — those tag ``cs.reason`` with ``client_close`` /
      ``client_disconnect``;
    * either side reports a non-EOF transport error (``stream_error`` /
      ``write_error`` from the read / write paths);
    * ``open_session`` fails on the very first attempt — we surface
      ``init_failed`` and bail.

    Only ``link_eof`` (set by ``_pump_link_to_ws`` on empty read) is
    treated as "soft" and triggers reconnect.
    """
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

    cs = _CloseState()
    try:
        while True:
            pump_task = asyncio.create_task(_pump_link_to_ws(ws, link, cs))
            recv_task = asyncio.create_task(
                _recv_loop(ws, link=link, close_state=cs, device=device)
            )
            done, pending = await asyncio.wait(
                {pump_task, recv_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await t

            # Reconnect only when the pump side died of a pure link EOF
            # AND the recv side was still alive (i.e. user hasn't asked
            # to close). Any other combo = terminate the session.
            if (
                cs.reason == "link_eof"
                and pump_task in done
                and recv_task in pending
            ):
                with contextlib.suppress(Exception):
                    await transport.close_session(link)
                # Reset the soft signal before the next attempt so a
                # subsequent client_close / write_error isn't shadowed.
                cs.reason = "ended"
                cs.error = None
                try:
                    await asyncio.sleep(_LINK_RECONNECT_BACKOFF_S)
                    link = await transport.open_session()
                except (asyncio.CancelledError, Exception) as e:  # noqa: BLE001
                    if isinstance(e, asyncio.CancelledError):
                        raise
                    cs.reason = "reconnect_failed"
                    cs.error = f"{type(e).__name__}: {e}"
                    break
                continue
            break
    finally:
        with contextlib.suppress(Exception):
            await transport.close_session(link)
        # Single close frame, populated from whichever task finished
        # first (or 'ended' if both finished cleanly).
        payload: dict[str, Any] = {"type": "closed", "reason": cs.reason}
        if cs.error:
            payload["error"] = cs.error
        with contextlib.suppress(Exception):
            await ws.send_json(payload)
        with contextlib.suppress(Exception):
            await ws.close()


async def _pump_uart_to_ws(
    ws: WebSocket, transport: Any, cs: _CloseState
) -> None:
    """Async iterator over UART bytes → ws.send_bytes per chunk.

    Read-only PR-C.b path — wraps ``transport.stream_read("uart")`` in
    :func:`_reconnecting_serial_stream` (no deadline) so an idle UART
    bridge that EOFs the client connection doesn't tear the WS down;
    we reopen and keep listening until the client closes the WS or
    ``_run_read_only`` cancels us. See
    ``BUG_serial_capture_idle_auto_exit.md`` for the field report on
    the equivalent CLI path.

    Stops on WebSocketDisconnect / outer cancel / send error. Other
    errors reported as closed-frame before bubbling.
    """
    try:
        async for chunk in _reconnecting_serial_stream(transport, "uart"):
            if not chunk:
                continue
            try:
                await ws.send_bytes(chunk)
            except (WebSocketDisconnect, RuntimeError):
                return
    except (asyncio.CancelledError, WebSocketDisconnect):
        raise
    except Exception as e:  # noqa: BLE001 — record for the single close frame
        cs.reason = "stream_error"
        cs.error = f"{type(e).__name__}: {e}"


async def _pump_link_to_ws(
    ws: WebSocket, link: Any, close_state: _CloseState,
) -> None:
    """Bidirectional PR-C.c path — read directly off the shared link's
    StreamReader so the same physical UART can also be written to from
    `_recv_loop`. Stops on read EOF / disconnect / send error.

    On error sets `close_state.{reason,error}` and returns — outer
    finally is the only place that emits a close frame (avoids the
    double-frame race fixed in PR-C.c review HIGH 1).

    On link EOF (idle bridge closing the connection) sets
    ``close_state.reason = "link_eof"`` so the outer reconnect loop in
    ``_run_bidirectional`` can distinguish a soft EOF (reopen) from a
    hard error (terminate)."""
    while True:
        try:
            chunk = await link.reader.read(4096)
        except (ConnectionResetError, OSError) as e:
            close_state.reason = "stream_error"
            close_state.error = f"{type(e).__name__}: {e}"
            return
        if not chunk:
            close_state.reason = "link_eof"
            return  # EOF — outer reconnect loop decides whether to reopen
        try:
            await ws.send_bytes(chunk)
        except (WebSocketDisconnect, RuntimeError):
            return


async def _recv_loop(
    ws: WebSocket,
    *,
    link: Any | None = None,
    close_state: _CloseState | None = None,
    device: str | None = None,
) -> None:
    """Watch for client-initiated control / data frames.

    Honoured frames:
        {"type":"close"}  → return; outer wait cancels the pump task
        <binary>          → if `link` provided (bidirectional mode),
                            forward to link.writer.write + drain.
                            Silently dropped in read-only mode.

    `close_state` (bidirectional only) is updated on writer error so
    the outer finally can surface a single coherent close frame.
    `device` is the serial captured at connect time, only used to tag
    audit-log events when frames are dropped."""
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                if close_state is not None:
                    close_state.reason = "client_disconnect"
                return
            # Binary frame — UART input from client (PR-C.c).
            data = msg.get("bytes")
            if data and link is not None:
                if len(data) > _MAX_WRITE_FRAME_BYTES:
                    # DEBT-026: drop oversized frame, surface back to
                    # client so they can split. Don't close the WS — a
                    # single bad frame shouldn't tear the session down.
                    with contextlib.suppress(Exception):
                        await ws.send_json({
                            "type": "write_dropped",
                            "reason": "frame_too_large",
                            "max_bytes": _MAX_WRITE_FRAME_BYTES,
                            "got_bytes": len(data),
                        })
                    # Also persist to audit log so /audit/stream
                    # subscribers can surface it (operator visibility,
                    # not just per-WS feedback). Bus is best-effort —
                    # any failure is swallowed since the user already
                    # got the inline ack frame above. CancelledError
                    # included because event_bus.publish awaits both an
                    # asyncio.Lock and a to_thread call (cancel-suspend
                    # points); on Python 3.11+ CancelledError is
                    # BaseException so plain suppress(Exception) lets
                    # it leak into the WS handler shutdown path
                    # (lessons.md L-031).
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await get_bus().publish(
                            make_event(
                                session_id=f"uart-stream:{device or 'unknown'}",
                                source="uart_stream",
                                kind="write_dropped",
                                summary=(
                                    f"UART write frame dropped — "
                                    f"{len(data)}B > {_MAX_WRITE_FRAME_BYTES}B cap"
                                ),
                                data={
                                    "reason": "frame_too_large",
                                    "max_bytes": _MAX_WRITE_FRAME_BYTES,
                                    "got_bytes": len(data),
                                    "device": device or "",
                                },
                            )
                        )
                    continue
                try:
                    link.writer.write(data)
                    await link.writer.drain()
                except (ConnectionResetError, OSError) as e:
                    if close_state is not None:
                        close_state.reason = "write_error"
                        close_state.error = f"{type(e).__name__}: {e}"
                    return
                continue
            text = msg.get("text")
            if text:
                with contextlib.suppress(json.JSONDecodeError):
                    obj = json.loads(text)
                    if isinstance(obj, dict) and obj.get("type") == "close":
                        if close_state is not None:
                            close_state.reason = "client_close"
                        return
    except WebSocketDisconnect:
        if close_state is not None:
            close_state.reason = "client_disconnect"
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
