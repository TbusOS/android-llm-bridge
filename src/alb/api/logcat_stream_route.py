"""Web API: /logcat/stream — live adb logcat byte stream over WebSocket (PR-D).

Adb-side counterpart to /uart/stream (PR-C.b). Same protocol family — the
frontend useUartStream hook can be reused mostly verbatim.

Protocol:

    C → S (first JSON, optional, 1.5 s timeout):
        {"device": "<serial>", "filter": "*:E", "tags": ["MyApp"]}

    S → C (JSON, on accept):
        {"type": "ready", "device": "...", "transport": "adb",
         "filter": "<final filter>"}

    S → C (binary frames):
        Raw logcat bytes (verbatim from AdbTransport.stream_read('logcat')).

    S → C (JSON, on stream end / error):
        {"type": "closed", "reason": "...", "error": "..."}

    C → S (JSON, optional control):
        {"type": "close"}  → server shuts down the stream cleanly

The default transport is honoured (typically adb). If the active
transport doesn't expose stream_read('logcat'), we close with
reason='unsupported_source'.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from alb.api.schema import API_VERSION
from alb.mcp.transport_factory import build_transport

router = APIRouter()

_FILTER_TOKEN = re.compile(r"^[A-Za-z0-9_.*-]+:[VDIWEFSvdiwefs]$")


def _validate_filter_spec(spec: str | None) -> str | None:
    """Return error message if logcat filter spec is invalid, None if OK.

    Accepts None / empty / whitespace-only as valid (no filter).
    Each whitespace-separated token must be `<TAG>:<LEVEL>` where TAG is
    alphanumeric / underscore / dash / dot / asterisk, and LEVEL is one
    of V D I W E F S (case-insensitive).
    """
    if spec is None:
        return None
    s = spec.strip()
    if not s:
        return None
    bad: list[str] = []
    for tok in s.split():
        if not _FILTER_TOKEN.match(tok):
            bad.append(tok)
    if bad:
        sample = ", ".join(repr(t) for t in bad[:3])
        return (
            f"invalid filter token(s): {sample}; "
            "expected '<TAG>:<LEVEL>' where LEVEL ∈ V D I W E F S"
        )
    return None


@router.websocket("/logcat/stream")
async def logcat_stream_ws(ws: WebSocket) -> None:
    await ws.accept()

    config = await _read_config(ws)
    config = config if isinstance(config, dict) else {}
    device = config.get("device")
    filter_spec = config.get("filter")
    tags = config.get("tags")

    # Build a filter spec from `tags` if provided and `filter` wasn't —
    # mirrors alb_logcat MCP tool ergonomics.
    if not filter_spec and isinstance(tags, list) and tags:
        filter_spec = " ".join([f"{t}:V" for t in tags] + ["*:S"])

    spec_err = _validate_filter_spec(filter_spec)
    if spec_err is not None:
        await ws.send_json(
            {
                "type": "closed",
                "reason": "bad_filter",
                "error": spec_err,
            }
        )
        with contextlib.suppress(Exception):
            await ws.close()
        return
    # Normalize whitespace-only / empty to None so downstream "no filter"
    # path is taken consistently.
    if filter_spec is not None and not filter_spec.strip():
        filter_spec = None
    elif isinstance(filter_spec, str):
        filter_spec = filter_spec.strip()

    try:
        transport = build_transport(device_serial=device)
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

    if not hasattr(transport, "stream_read"):
        await ws.send_json(
            {
                "type": "closed",
                "reason": "unsupported_source",
                "error": f"transport {type(transport).__name__} has no stream_read",
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
            "transport": getattr(transport, "name", "adb"),
            "filter": filter_spec or "",
        }
    )

    pump_task = asyncio.create_task(
        _pump_logcat_to_ws(ws, transport, filter_spec)
    )
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


async def _pump_logcat_to_ws(
    ws: WebSocket, transport: Any, filter_spec: str | None
) -> None:
    """Iterate transport.stream_read('logcat', filter=...) and forward
    each chunk as a binary WS frame."""
    kwargs: dict[str, Any] = {}
    if filter_spec:
        kwargs["filter"] = filter_spec
    try:
        async for chunk in transport.stream_read("logcat", **kwargs):
            if not chunk:
                continue
            try:
                await ws.send_bytes(chunk)
            except (WebSocketDisconnect, RuntimeError):
                return
    except (asyncio.CancelledError, WebSocketDisconnect):
        raise
    except Exception as e:  # noqa: BLE001
        with contextlib.suppress(Exception):
            await ws.send_json(
                {
                    "type": "closed",
                    "reason": "stream_error",
                    "error": f"{type(e).__name__}: {e}",
                }
            )


async def _recv_loop(ws: WebSocket) -> None:
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
