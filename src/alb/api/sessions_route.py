"""GET /sessions — recent ChatSession listing for the Web UI Dashboard.

Pure filesystem scan over the layout produced by
`alb.agent.session.ChatSession`:

    workspace/sessions/<session-id>/
        meta.json        # session_id / created / backend / model / device
        messages.jsonl   # one Message per line — used for turn count + last activity

No transport / LLM backend dependency, so this endpoint stays cheap and
always answers (even when no device is attached).

L-033: scan runs in a worker thread via `asyncio.to_thread`; do NOT
inline sync FS in the `async def` endpoint, or N sessions × 4 FS calls
will stall the event loop on each request.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Path as FPath, Query

from alb.infra.workspace import (
    is_safe_session_id,
    workspace_root,
)

router = APIRouter()


def _count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    n = 0
    with path.open("rb") as f:
        for _ in f:
            n += 1
    return n


def _mtime_iso(path: Path) -> str | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def _load_meta(meta_file: Path) -> dict[str, Any]:
    if not meta_file.exists():
        return {}
    try:
        data = json.loads(meta_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _summarize(session_dir: Path) -> dict[str, Any]:
    meta = _load_meta(session_dir / "meta.json")
    messages = session_dir / "messages.jsonl"
    return {
        "session_id": meta.get("session_id") or session_dir.name,
        "created": meta.get("created"),
        "backend": meta.get("backend") or "",
        "model": meta.get("model") or "",
        "device": meta.get("device"),
        "turns": _count_lines(messages),
        "last_event_ts": _mtime_iso(messages),
    }


def _scan_sessions_in_thread(sessions_root: Path, limit: int) -> list[dict[str, Any]]:
    """Sync helper: bulk-scan sessions/ and return summaries (newest first).

    All FS calls (`exists` / `iterdir` / `is_dir` / `stat` / `read_text` /
    binary `open`) live here so the caller pays ONE thread hop instead of
    4×N for N sessions. Pure sync — callers from async paths must wrap in
    `asyncio.to_thread`.
    """
    if not sessions_root.exists():
        return []
    summaries = [_summarize(p) for p in sessions_root.iterdir() if p.is_dir()]
    summaries.sort(
        key=lambda s: s.get("created") or s["session_id"],
        reverse=True,
    )
    return summaries[:limit]


@router.get("/sessions")
async def list_sessions(
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    """Return up to `limit` most recent sessions, newest first.

    Sort key: `meta.created` (ISO 8601 sorts lexicographically); falls
    back to the directory name when meta.json is missing or malformed
    (session_id format `<utc-date>-<short-uuid>` also sorts by time).

    L-033: scan delegates to a worker thread so 4×N sync FS calls don't
    stall the event loop. One thread hop per request regardless of N.
    """
    sessions_root = workspace_root() / "sessions"
    sessions = await asyncio.to_thread(
        _scan_sessions_in_thread, sessions_root, limit
    )
    return {"ok": True, "sessions": sessions}


def _load_messages_in_thread(messages_file: Path) -> list[dict[str, Any]]:
    """Sync helper: parse messages.jsonl into a list of message dicts.

    Lines that fail to parse are skipped (don't 500 the whole detail
    fetch for one bad line). Pure sync — async callers must wrap in
    `asyncio.to_thread` per L-033.
    """
    if not messages_file.exists():
        return []
    out: list[dict[str, Any]] = []
    with messages_file.open("rb") as f:
        for raw in f:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                out.append(obj)
    return out


def _load_detail_in_thread(
    sessions_root: Path, session_id: str
) -> dict[str, Any] | None:
    """Sync helper: build the full session detail payload (meta + messages).

    Returns ``None`` if the session directory doesn't exist so the
    caller can surface a clean 404 instead of an empty payload.
    """
    session_dir = sessions_root / session_id
    if not session_dir.is_dir():
        return None
    meta = _load_meta(session_dir / "meta.json")
    messages_file = session_dir / "messages.jsonl"
    messages = _load_messages_in_thread(messages_file)
    return {
        "session_id": meta.get("session_id") or session_id,
        "created": meta.get("created"),
        "backend": meta.get("backend") or "",
        "model": meta.get("model") or "",
        "device": meta.get("device"),
        "turns": len(messages),
        "last_event_ts": _mtime_iso(messages_file),
        "messages": messages,
    }


@router.get("/sessions/{session_id}")
async def get_session_detail(
    session_id: str = FPath(..., min_length=1, max_length=128),
) -> dict[str, Any]:
    """Return full session detail (meta + every message) for ``session_id``.

    Used by the Web SessionDetailPage to render a chat replay. Path-traversal
    is rejected at the root layer via :func:`is_safe_session_id` (L-035);
    a missing session yields ``404`` so the front-end can show a clean
    "not found" state.
    """
    if not is_safe_session_id(session_id):
        # L-051: no-echo on reject (sec MID-1). See diag_route._resolve_transport.
        # `session not found` below intentionally DOES echo session_id —
        # client already supplied it, the 404 carries no new info.
        raise HTTPException(status_code=400, detail="invalid session id")
    sessions_root = workspace_root() / "sessions"
    detail = await asyncio.to_thread(
        _load_detail_in_thread, sessions_root, session_id
    )
    if detail is None:
        raise HTTPException(
            status_code=404,
            detail=f"session not found: {session_id}",
        )
    return {"ok": True, **detail}
