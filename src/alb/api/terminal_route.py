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

from alb.infra.workspace import iso_timestamp, session_path
from alb.mcp.transport_factory import build_transport
from alb.transport.interactive import InteractiveShell
from alb.transport.terminal_guard import TerminalGuard

router = APIRouter()


@router.websocket("/terminal/ws")
async def terminal_ws(ws: WebSocket) -> None:
    await ws.accept()

    config = await _read_config(ws)
    config = config if isinstance(config, dict) else {}
    device = config.get("device")
    rows = _safe_int(config.get("rows", 24), 24)
    cols = _safe_int(config.get("cols", 80), 80)
    read_only = bool(config.get("read_only", False))
    session_id = str(config.get("session_id") or f"term-{iso_timestamp()}")

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

    audit_path = session_path(session_id, "terminal.jsonl")

    async def on_hitl(line: str, rule) -> None:  # noqa: ANN001
        await ws.send_json({
            "type": "hitl_request",
            "command": line,
            "rule": rule.name,
            "reason": rule.reason,
        })

    async def on_audit(payload: dict) -> None:
        try:
            with audit_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": iso_timestamp(), **payload}, ensure_ascii=False))
                f.write("\n")
        except OSError:
            pass

    async def on_echo(data: bytes) -> None:
        with contextlib.suppress(Exception):
            await ws.send_bytes(data)

    guard = TerminalGuard(
        shell,
        read_only=read_only,
        on_hitl=on_hitl,
        on_audit=on_audit,
        on_echo=on_echo,
    )

    await ws.send_json({
        "type": "ready",
        "device": device or "",
        "transport": getattr(transport, "name", "adb"),
        "session_id": session_id,
        "read_only": read_only,
    })

    recv_task = asyncio.create_task(_recv_loop(ws, shell, guard))
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
        guard.close()
        await shell.close()
        with contextlib.suppress(Exception):
            await ws.send_json({
                "type": "closed",
                "exit_code": shell.returncode if shell.returncode is not None else 0,
            })
        with contextlib.suppress(Exception):
            await ws.close()


# ─── Loops ─────────────────────────────────────────────────────────


async def _recv_loop(
    ws: WebSocket, shell: InteractiveShell, guard: TerminalGuard
) -> None:
    """Forward client messages — bytes go through the HITL guard,
    JSON frames carry resize / control / hitl_response / set_read_only."""
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                return
            data = msg.get("bytes")
            text = msg.get("text")
            if data is not None:
                await guard.feed(data)
                continue
            if text is not None:
                try:
                    obj = json.loads(text)
                except json.JSONDecodeError:
                    await guard.feed(text.encode("utf-8", errors="replace"))
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
                        await guard.feed(payload.encode("utf-8", errors="replace"))
                elif kind == "hitl_response":
                    await guard.respond_hitl(
                        approve=bool(obj.get("approve", False)),
                        allow_session=bool(obj.get("allow_session", False)),
                    )
                elif kind == "set_read_only":
                    guard.read_only = bool(obj.get("value", False))
                    await ws.send_json({
                        "type": "control_ack",
                        "action": "set_read_only",
                        "read_only": guard.read_only,
                    })
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
