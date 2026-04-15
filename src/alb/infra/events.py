"""Simple in-process pub/sub event bus.

Used for fan-out of streaming data (logcat lines, uart bytes) to multiple
consumers: CLI printer, file sink, future WebSocket pushers for Web UI.

Design intentionally minimal for M1; M2 may extend with backpressure / priorities.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import time
from typing import Any


Handler = Callable[["Event"], Awaitable[None]]


@dataclass(frozen=True)
class Event:
    topic: str
    data: Any
    ts: float


class EventBus:
    """Per-process event bus. Not thread-safe (async-single-loop only)."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, topic: str, handler: Handler) -> Callable[[], None]:
        """Subscribe to a topic. Returns an unsubscribe callable."""
        self._handlers[topic].append(handler)

        def unsubscribe() -> None:
            if handler in self._handlers.get(topic, []):
                self._handlers[topic].remove(handler)

        return unsubscribe

    async def publish(self, topic: str, data: Any) -> None:
        handlers = list(self._handlers.get(topic, []))
        if not handlers:
            return
        event = Event(topic=topic, data=data, ts=time())
        # Fire-and-forget but awaitable — gather with return_exceptions so one
        # bad handler doesn't poison others.
        await asyncio.gather(
            *(h(event) for h in handlers),
            return_exceptions=True,
        )

    def clear(self) -> None:
        self._handlers.clear()


# Process-wide singleton (lazy).
_bus: EventBus | None = None


def bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus
