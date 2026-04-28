"""In-process event broadcaster + persistent jsonl log.

The bus has two responsibilities:

1. Live fan-out — async subscribers (e.g. /audit/stream WS) get every
   event published in this process via an asyncio.Queue. Slow
   subscribers drop events rather than blocking producers.
2. Persistence — every published event is appended to
   `workspace/events.jsonl` so that:
     - GET /audit can read the same source as the WS stream
     - history survives a process restart
     - off-line analysis can replay the log

Producers (chat_route, terminal_route) call `get_bus().publish(event)`.
The event schema is fixed to keep the WS protocol and the on-disk log
in lockstep:

    {
        "ts": "<ISO 8601>",
        "session_id": "<sid>",
        "source": "chat" | "terminal" | "system",
        "kind": "<role-or-event-name>",
        "summary": "<short human-readable line>",
        "data": { ... }              # optional structured payload
    }

`workspace/events.jsonl` is append-only. It does NOT replace the
per-session messages.jsonl / terminal.jsonl files — those still serve
as the per-session replay source. events.jsonl is the cross-session
audit log, which is the right granularity for the dashboard.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from alb.infra.workspace import workspace_root


SUBSCRIBER_QUEUE_SIZE = 256


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def events_log_path() -> Path:
    return workspace_root() / "events.jsonl"


class EventBroadcaster:
    """Process-wide pub-sub for audit events. Construct once, share via
    `get_bus()`. Tests can replace the singleton via `reset_bus()`.

    Thread-safety: this bus runs entirely on the asyncio event loop;
    publishers and subscribers must be async. The `_lock` only guards
    the subscriber set against concurrent subscribe/unsubscribe."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._lock = asyncio.Lock()

    async def publish(self, event: dict[str, Any]) -> None:
        """Fan out to live subscribers, then append to events.jsonl.

        Order matters: live subscribers see the event before the disk
        write completes, which keeps the WS stream as snappy as
        possible. A disk failure is logged into the event payload (via
        the caller's responsibility) but does not block fan-out.
        """
        async with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Drop the event for this slow consumer; the WS layer
                # is responsible for closing it if it sees gaps.
                pass
        await asyncio.to_thread(_append_jsonl, events_log_path(), event)

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
        """Yield a Queue that receives every subsequent published event.

        Use as `async with bus.subscribe() as q: ...`. The queue is
        unsubscribed automatically when the context exits.
        """
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=SUBSCRIBER_QUEUE_SIZE,
        )
        async with self._lock:
            self._subscribers.add(q)
        try:
            yield q
        finally:
            async with self._lock:
                self._subscribers.discard(q)

    @property
    def subscriber_count(self) -> int:
        """Diagnostic helper for tests."""
        return len(self._subscribers)


def _append_jsonl(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        json.dump(event, f, ensure_ascii=False)
        f.write("\n")


_BUS: EventBroadcaster | None = None


def get_bus() -> EventBroadcaster:
    """Return the process-wide EventBroadcaster, lazily created."""
    global _BUS
    if _BUS is None:
        _BUS = EventBroadcaster()
    return _BUS


def reset_bus() -> None:
    """Drop the singleton — tests use this to start each test with a
    fresh, subscriber-free bus."""
    global _BUS
    _BUS = None


def make_event(
    *,
    session_id: str,
    source: str,
    kind: str,
    summary: str,
    data: dict[str, Any] | None = None,
    ts: str | None = None,
) -> dict[str, Any]:
    """Helper to build the canonical event shape. Keeps producers from
    forgetting required fields and keeps the schema in one place."""
    out: dict[str, Any] = {
        "ts": ts or now_iso(),
        "session_id": session_id,
        "source": source,
        "kind": kind,
        "summary": summary,
    }
    if data is not None:
        out["data"] = data
    return out
