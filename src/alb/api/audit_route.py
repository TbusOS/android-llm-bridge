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

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query

from alb.infra.event_bus import events_log_path

router = APIRouter()


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
