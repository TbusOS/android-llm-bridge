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

from fastapi import APIRouter, Query

from alb.infra.workspace import workspace_root

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
