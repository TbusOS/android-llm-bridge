"""GET /audit — recent activity stream for the Web UI Timeline.

Reads the cross-session audit log produced by the event bus
(`workspace/events.jsonl`, see src/alb/infra/event_bus.py). The bus
appends one canonical event per line, so this endpoint is just a
windowed tail with optional filtering.

Companion endpoint `WS /audit/stream` streams new events live; clients
typically call `GET /audit?minutes=30` for an initial snapshot then
subscribe to the WS for live updates. The schema returned here is the
same shape the WS pushes via `{type:"event", data:...}`, so a single
mapping function on the client handles both.

`ts_approx` is kept in the response for backward compatibility with
the old fs-scan implementation but is always `false` now (every event
in the log carries a real ts).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from alb.infra.event_bus import events_log_path, get_bus

router = APIRouter()

# Snapshot returns at most this many events when the client connects.
# Matches the GET /audit default upper bound.
_SNAPSHOT_LIMIT = 200
_FIRST_MESSAGE_TIMEOUT_S = 0.5


def _parse_ts(value: str) -> datetime | None:
    try:
        ts = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _project(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Convert a raw events.jsonl entry into the GET /audit shape.

    Drops malformed rows (missing ts / unparseable ts) silently — the
    log is append-only best-effort, we don't want a single bad row to
    poison the whole response."""
    ts = _parse_ts(raw.get("ts") or "")
    if ts is None:
        return None
    return {
        "ts": ts.isoformat(),
        "session_id": raw.get("session_id") or "",
        "source": raw.get("source") or "system",
        "kind": raw.get("kind") or "unknown",
        "summary": raw.get("summary") or "",
        "ts_approx": False,
    }


def _read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            projected = _project(raw)
            if projected is not None:
                out.append(projected)
    return out


@router.get("/audit")
async def list_audit(
    minutes: int = Query(30, ge=1, le=1440),
    limit: int = Query(200, ge=1, le=2000),
) -> dict[str, Any]:
    """Return the last `minutes` minutes of audit events, newest first."""
    until = datetime.now(timezone.utc)
    since = until - timedelta(minutes=minutes)

    in_window: list[dict[str, Any]] = []
    for e in _read_events(events_log_path()):
        ts = _parse_ts(e["ts"])
        if ts is not None and since <= ts <= until:
            in_window.append(e)
    in_window.sort(key=lambda e: e["ts"], reverse=True)
    events = in_window

    return {
        "ok": True,
        "since": since.isoformat(),
        "until": until.isoformat(),
        "events": events[:limit],
    }


@router.websocket("/audit/stream")
async def audit_stream(ws: WebSocket) -> None:
    """Live audit stream.

    Protocol:

        C → S (optional first JSON, 0.5s timeout):
            {"minutes": 30}            # snapshot window; default 30
        S → C (one shot):
            {"type": "snapshot",
             "since": "<ISO>",
             "until": "<ISO>",
             "events": [<event>, ...]}     # newest-first, ≤ 200 entries
        S → C (live):
            {"type": "event", "data": <event>}
        C → S (any time):
            {"type": "control", "action": "pause"}
            {"type": "control", "action": "resume"}
        S → C:
            {"type": "control_ack", "paused": <bool>}

    A paused stream silently drops events that arrive while paused —
    catching up on history is what GET /audit + reconnect is for.
    """
    await ws.accept()

    # Optional first message — opt-in window override.
    minutes = 30
    try:
        first = await asyncio.wait_for(
            ws.receive_json(), timeout=_FIRST_MESSAGE_TIMEOUT_S
        )
    except (asyncio.TimeoutError, json.JSONDecodeError):
        first = None
    except WebSocketDisconnect:
        return
    if isinstance(first, dict):
        try:
            minutes = max(1, min(1440, int(first.get("minutes", minutes))))
        except (TypeError, ValueError):
            pass

    # 1. Snapshot
    until = datetime.now(timezone.utc)
    since = until - timedelta(minutes=minutes)
    snapshot: list[dict[str, Any]] = []
    for e in _read_events(events_log_path()):
        ts = _parse_ts(e["ts"])
        if ts is not None and since <= ts <= until:
            snapshot.append(e)
    snapshot.sort(key=lambda e: e["ts"], reverse=True)
    snapshot = snapshot[:_SNAPSHOT_LIMIT]
    try:
        await ws.send_json({
            "type": "snapshot",
            "since": since.isoformat(),
            "until": until.isoformat(),
            "events": snapshot,
        })
    except WebSocketDisconnect:
        return

    # 2. Live + control. Two coroutines, FIRST_COMPLETED tears both
    #    down — same pattern as terminal_route.
    state = {"paused": False}
    bus = get_bus()

    async with bus.subscribe() as q:

        async def reader_loop() -> None:
            while True:
                msg = await ws.receive_json()
                if not isinstance(msg, dict):
                    continue
                if msg.get("type") != "control":
                    continue
                action = msg.get("action")
                if action == "pause":
                    state["paused"] = True
                elif action == "resume":
                    state["paused"] = False
                await ws.send_json({
                    "type": "control_ack",
                    "action": action,
                    "paused": state["paused"],
                })

        async def writer_loop() -> None:
            while True:
                event = await q.get()
                if state["paused"]:
                    continue
                projected = _project(event)
                if projected is None:
                    continue
                await ws.send_json({"type": "event", "data": projected})

        reader = asyncio.create_task(reader_loop())
        writer = asyncio.create_task(writer_loop())

        try:
            done, pending = await asyncio.wait(
                {reader, writer},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
            for t in done:
                exc = t.exception()
                if exc and not isinstance(exc, WebSocketDisconnect):
                    raise exc
        except WebSocketDisconnect:
            pass
        finally:
            for t in (reader, writer):
                if not t.done():
                    t.cancel()
            with contextlib.suppress(Exception):
                await ws.close()
