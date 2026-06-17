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
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from alb.infra.workspace import workspace_root


_log = logging.getLogger(__name__)

SUBSCRIBER_QUEUE_SIZE = 256


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def events_log_path() -> Path:
    return workspace_root() / "events.jsonl"


class EventBroadcaster:
    """Process-wide pub-sub for audit events. Construct once, share via
    `get_bus()`. Tests can replace the singleton via `reset_bus()`.

    Two consumer classes (ADR-049):
      - async queue subscribers (`subscribe()`) — backpressure-tolerant
        live streams (e.g. /audit/stream WS). Bounded queue, drop-on-full.
      - synchronous in-process listeners (`add_listener()`) — read-model
        projections (e.g. MetricStore) that must see every event with
        zero loss. Called inline on `publish()`, so they MUST be cheap
        and never block / raise (contract enforced + isolated below).

    Thread-safety: this bus runs entirely on the asyncio event loop;
    publishers and subscribers must be async. The `_lock` only guards
    the subscriber set against concurrent subscribe/unsubscribe.
    Listeners are added/removed at startup/shutdown, not concurrently
    with high-frequency publishes, so they need no lock."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._listeners: list[Callable[[dict[str, Any]], None]] = []
        self._lock = asyncio.Lock()

    def add_listener(self, fn: Callable[[dict[str, Any]], None]) -> None:
        """Register a synchronous in-process listener invoked on every
        publish (BEFORE async fan-out + disk write, so the read model is
        current the instant publish returns).

        Contract — the listener MUST be:
          - synchronous and cheap (O(1) in-memory work),
          - non-blocking (no I/O, no awaits),
          - exception-free (a raise is swallowed + logged, but a noisy
            listener still wastes every publish).
        For anything that can block or fall behind, use `subscribe()`
        (bounded queue, drop-on-full) instead. Idempotent — re-adding the
        same callable is a no-op (guards against double registration when
        a per-test `create_app()` re-attaches to a shared bus)."""
        if fn not in self._listeners:
            self._listeners.append(fn)

    def remove_listener(self, fn: Callable[[dict[str, Any]], None]) -> None:
        """Unregister a listener. No-op if not registered."""
        with suppress(ValueError):
            self._listeners.remove(fn)

    @property
    def listener_count(self) -> int:
        """Diagnostic helper for tests (catches double registration)."""
        return len(self._listeners)

    async def publish(self, event: dict[str, Any]) -> None:
        """Notify listeners, fan out to subscribers, then append to disk.

        Order matters:
          1. synchronous listeners (read-model projections) run FIRST so a
             query right after publish() reflects this event;
          2. live queue subscribers see it before the disk write completes,
             keeping the WS stream snappy;
          3. the durable append runs off the event loop.
        Each listener call is isolated — a contract violation (raising)
        is logged but never breaks fan-out or chat persistence (same
        philosophy as the QueueFull drop below).
        """
        for fn in list(self._listeners):
            try:
                fn(event)
            except Exception as e:  # noqa: BLE001 — listener isolation
                _log.warning("event listener failed: %s", e)
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
