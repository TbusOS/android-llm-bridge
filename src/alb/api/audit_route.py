"""GET /audit — recent activity stream for the Web UI Timeline.

Aggregates two on-disk audit sources under each session directory:

    workspace/sessions/<sid>/messages.jsonl     # ChatSession appends — no per-line ts
    workspace/sessions/<sid>/terminal.jsonl     # TerminalGuard appends — each line has ts

`messages.jsonl` lines do not carry a per-line timestamp (Message has
no `ts` field; see src/alb/agent/backend.py). We use the file's mtime
as an approximate ts for every line and mark `ts_approx: true` so the
UI can render it accordingly. terminal.jsonl lines are kept exact.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query

from alb.infra.workspace import workspace_root

router = APIRouter()


def _parse_ts(value: str) -> datetime | None:
    try:
        ts = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _summarize_terminal(d: dict[str, Any]) -> str:
    ev = d.get("event")
    line = (d.get("line") or "")[:120]
    if ev == "command":
        return f"$ {line}"
    if ev == "deny":
        rule = d.get("rule") or ""
        return f"deny: {line} ({rule})" if rule else f"deny: {line}"
    if ev in ("hitl_approve", "hitl_deny"):
        return f"{ev}: {line}"
    return str(ev or "?")


def _summarize_chat(d: dict[str, Any]) -> str:
    tool_calls = d.get("tool_calls") or []
    if tool_calls:
        names = ", ".join(tc.get("name", "?") for tc in tool_calls)
        return f"tool_calls: {names}"
    role = d.get("role")
    content = (d.get("content") or "").strip().replace("\n", " ")
    if role == "tool":
        return f"tool result: {content[:120]}"
    return content[:120]


def _terminal_events(
    path: Path, sid: str, since: datetime, until: datetime
) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = _parse_ts(d.get("ts") or "")
            if ts is None or not (since <= ts <= until):
                continue
            out.append({
                "ts": ts.isoformat(),
                "session_id": sid,
                "source": "terminal",
                "kind": d.get("event") or "unknown",
                "summary": _summarize_terminal(d),
                "ts_approx": False,
            })
    return out


def _chat_events(
    path: Path, sid: str, since: datetime, until: datetime
) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    if not (since <= mtime <= until):
        return []
    out: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            out.append({
                "ts": mtime.isoformat(),
                "session_id": sid,
                "source": "chat",
                "kind": d.get("role") or "unknown",
                "summary": _summarize_chat(d),
                "ts_approx": True,
            })
    return out


@router.get("/audit")
async def list_audit(
    minutes: int = Query(30, ge=1, le=1440),
    limit: int = Query(200, ge=1, le=2000),
) -> dict[str, Any]:
    """Return audit events from the last `minutes` minutes, newest first.

    Window: [now - minutes, now]. The `since` / `until` ISO strings in
    the response let the UI label the window without re-deriving it.
    """
    until = datetime.now(timezone.utc)
    since = until - timedelta(minutes=minutes)

    sessions_root = workspace_root() / "sessions"
    if not sessions_root.exists():
        return {
            "ok": True,
            "since": since.isoformat(),
            "until": until.isoformat(),
            "events": [],
        }

    events: list[dict[str, Any]] = []
    for session_dir in sessions_root.iterdir():
        if not session_dir.is_dir():
            continue
        sid = session_dir.name
        events.extend(_terminal_events(session_dir / "terminal.jsonl", sid, since, until))
        events.extend(_chat_events(session_dir / "messages.jsonl", sid, since, until))

    events.sort(key=lambda e: e["ts"], reverse=True)
    return {
        "ok": True,
        "since": since.isoformat(),
        "until": until.isoformat(),
        "events": events[:limit],
    }
