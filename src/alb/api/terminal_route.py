"""Web API: /terminal/ws — interactive PTY shell over WebSocket.

Designed for xterm.js (or any byte-oriented terminal emulator) on the
Web UI Terminal panel.

Protocol:

    C → S (first JSON, optional, 1.5 s timeout):
        {"device": "<serial>", "transport": "adb",
         "rows": 24, "cols": 80}

    C → S (binary frames):
        Raw bytes typed by the user. Goes straight to the shell stdin.

    C → S (JSON):
        {"type": "resize", "rows": 30, "cols": 120}
        {"type": "control", "action": "close"}

    S → C (JSON, on accept):
        {"type": "ready", "device": "...", "transport": "adb"}

    S → C (binary frames):
        Bytes coming back from the shell stdout.

    S → C (JSON, on shell exit):
        {"type": "closed", "exit_code": 0}

The PTY is owned by this WebSocket connection; closing the WS tears
the shell down. HITL command interception lands in a follow-up commit.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from alb.mcp.transport_factory import build_transport
from alb.transport.interactive import InteractiveShell

router = APIRouter()


@router.websocket("/terminal/ws")
async def terminal_ws(ws: WebSocket) -> None:
    await ws.accept()

    config = await _read_config(ws)
    device = config.get("device") if isinstance(config, dict) else None
    rows = _safe_int(config.get("rows", 24) if isinstance(config, dict) else 24, 24)
    cols = _safe_int(config.get("cols", 80) if isinstance(config, dict) else 80, 80)

    transport = build_transport(device_serial=device)

    try:
        shell = await transport.interactive_shell(rows=rows, cols=cols)
    except NotImplementedError as e:
        await ws.send_json({
            "type": "closed",
            "exit_code": -1,
            "error": {
                "code": "TRANSPORT_NO_PTY",
                "message": str(e),
                "suggestion": "use the adb transport — serial PTY support is M3+",
            },
        })
        with contextlib.suppress(Exception):
            await ws.close()
        return
    except Exception as e:  # noqa: BLE001 — keep WS alive long enough to report
        await ws.send_json({
            "type": "closed",
            "exit_code": -1,
            "error": {
                "code": "PTY_SPAWN_FAILED",
                "message": str(e),
                "suggestion": "",
            },
        })
        with contextlib.suppress(Exception):
            await ws.close()
        return

    await ws.send_json({
        "type": "ready",
        "device": device or "",
        "transport": getattr(transport, "name", "adb"),
    })

    recv_task = asyncio.create_task(_recv_loop(ws, shell))
    send_task = asyncio.create_task(_send_loop(ws, shell))
    try:
        done, pending = await asyncio.wait(
            {recv_task, send_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t
    finally:
        await shell.close()
        with contextlib.suppress(Exception):
            await ws.send_json({
                "type": "closed",
                "exit_code": shell.returncode if shell.returncode is not None else 0,
            })
        with contextlib.suppress(Exception):
            await ws.close()


# ─── Loops ─────────────────────────────────────────────────────────


async def _recv_loop(ws: WebSocket, shell: InteractiveShell) -> None:
    """Forward client messages to the shell.

    Binary frames go straight to stdin. Text frames are parsed as JSON
    control messages (resize / control:close).
    """
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                return
            data = msg.get("bytes")
            text = msg.get("text")
            if data is not None:
                await shell.write(data)
                continue
            if text is not None:
                try:
                    obj = json.loads(text)
                except json.JSONDecodeError:
                    # Treat as raw text input — terminals occasionally
                    # send pasted strings as text frames.
                    await shell.write(text.encode("utf-8", errors="replace"))
                    continue
                if not isinstance(obj, dict):
                    continue
                kind = obj.get("type")
                if kind == "resize":
                    rows = _safe_int(obj.get("rows", 24), 24)
                    cols = _safe_int(obj.get("cols", 80), 80)
                    await shell.resize(rows, cols)
                elif kind == "control" and obj.get("action") == "close":
                    return
                elif kind == "input":
                    payload = obj.get("data", "")
                    if isinstance(payload, str):
                        await shell.write(payload.encode("utf-8", errors="replace"))
    except WebSocketDisconnect:
        return
    except Exception:
        return


async def _send_loop(ws: WebSocket, shell: InteractiveShell) -> None:
    """Forward shell stdout to the client as binary frames."""
    try:
        while True:
            chunk = await shell.read()
            if not chunk:
                return
            await ws.send_bytes(chunk)
    except WebSocketDisconnect:
        return
    except Exception:
        return


# ─── Helpers ───────────────────────────────────────────────────────


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


def _safe_int(v: Any, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default
