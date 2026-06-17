"""Tests for alb.infra.event_bus — in-process broadcaster + jsonl log."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from alb.infra.event_bus import (
    EventBroadcaster,
    events_log_path,
    get_bus,
    make_event,
    reset_bus,
)


@pytest.fixture
def workspace(monkeypatch, tmp_path) -> Path:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    reset_bus()
    yield tmp_path
    reset_bus()


def test_make_event_required_fields() -> None:
    e = make_event(
        session_id="abc", source="chat", kind="user", summary="hello"
    )
    assert set(e.keys()) == {"ts", "session_id", "source", "kind", "summary"}
    assert "T" in e["ts"]  # ISO 8601 has a T separator
    assert e["session_id"] == "abc"


def test_make_event_with_data_and_explicit_ts() -> None:
    e = make_event(
        session_id="x",
        source="terminal",
        kind="command",
        summary="$ ls",
        data={"line": "ls /data"},
        ts="2026-04-28T10:00:00+00:00",
    )
    assert e["ts"] == "2026-04-28T10:00:00+00:00"
    assert e["data"] == {"line": "ls /data"}


@pytest.mark.asyncio
async def test_publish_appends_to_events_jsonl(workspace) -> None:
    bus = get_bus()
    await bus.publish(make_event(
        session_id="s1", source="chat", kind="user", summary="hi"
    ))
    await bus.publish(make_event(
        session_id="s1", source="chat", kind="assistant", summary="hello"
    ))
    log = events_log_path()
    assert log.exists()
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    assert [e["kind"] for e in parsed] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_subscriber_receives_published_events(workspace) -> None:
    bus = get_bus()
    received: list[dict] = []

    async def consumer(ready: asyncio.Event) -> None:
        async with bus.subscribe() as q:
            ready.set()
            for _ in range(2):
                received.append(await q.get())

    ready = asyncio.Event()
    task = asyncio.create_task(consumer(ready))
    await ready.wait()  # ensure subscription happens before publish

    await bus.publish(make_event(
        session_id="s1", source="chat", kind="user", summary="one"
    ))
    await bus.publish(make_event(
        session_id="s1", source="chat", kind="user", summary="two"
    ))
    await asyncio.wait_for(task, timeout=1)

    assert [e["summary"] for e in received] == ["one", "two"]


@pytest.mark.asyncio
async def test_multiple_subscribers_each_get_a_copy(workspace) -> None:
    bus = get_bus()
    seen_a: list[dict] = []
    seen_b: list[dict] = []

    async def consume(target: list[dict], ready: asyncio.Event) -> None:
        async with bus.subscribe() as q:
            ready.set()
            target.append(await q.get())

    ra, rb = asyncio.Event(), asyncio.Event()
    ta = asyncio.create_task(consume(seen_a, ra))
    tb = asyncio.create_task(consume(seen_b, rb))
    await ra.wait()
    await rb.wait()
    assert bus.subscriber_count == 2

    await bus.publish(make_event(
        session_id="s1", source="chat", kind="user", summary="hello"
    ))
    await asyncio.wait_for(asyncio.gather(ta, tb), timeout=1)

    assert len(seen_a) == 1
    assert len(seen_b) == 1
    assert seen_a[0]["summary"] == "hello"


@pytest.mark.asyncio
async def test_subscribe_context_unsubscribes_on_exit(workspace) -> None:
    bus = get_bus()
    assert bus.subscriber_count == 0

    async with bus.subscribe():
        assert bus.subscriber_count == 1

    assert bus.subscriber_count == 0


@pytest.mark.asyncio
async def test_publish_does_not_block_when_subscriber_queue_full(workspace) -> None:
    """A slow subscriber must not stop publishers — overflow events
    are dropped for that subscriber, the rest still flow."""
    bus = EventBroadcaster()
    received: list[dict] = []

    async with bus.subscribe() as q:
        # Fill the queue beyond its capacity.
        for i in range(300):
            await bus.publish(make_event(
                session_id="s1", source="chat", kind="user", summary=f"#{i}"
            ))
        # Drain whatever fits — must be at most maxsize.
        while not q.empty():
            received.append(q.get_nowait())

    # We should have received SOME events, but fewer than 300 (the
    # queue capped out). The exact number depends on timing of
    # asyncio.to_thread — just assert we didn't deadlock.
    assert 0 < len(received) <= 300


# ── synchronous in-process listeners (ADR-049) ──────────────────────

@pytest.mark.asyncio
async def test_listener_invoked_on_publish(workspace) -> None:
    bus = EventBroadcaster()
    seen: list[dict] = []
    bus.add_listener(seen.append)
    await bus.publish(make_event(
        session_id="s1", source="chat", kind="tps_sample", summary="x"
    ))
    assert len(seen) == 1
    assert seen[0]["kind"] == "tps_sample"


def test_add_listener_is_idempotent() -> None:
    bus = EventBroadcaster()
    fn = lambda e: None  # noqa: E731
    bus.add_listener(fn)
    bus.add_listener(fn)
    assert bus.listener_count == 1


def test_remove_listener() -> None:
    bus = EventBroadcaster()
    fn = lambda e: None  # noqa: E731
    bus.add_listener(fn)
    bus.remove_listener(fn)
    assert bus.listener_count == 0
    bus.remove_listener(fn)  # no-op, must not raise


@pytest.mark.asyncio
async def test_listener_exception_is_isolated(workspace) -> None:
    """A raising listener must not break fan-out or the disk append
    (same philosophy as the QueueFull drop)."""
    bus = EventBroadcaster()
    good: list[dict] = []

    def boom(_e):  # noqa: ANN001
        raise RuntimeError("listener bug")

    bus.add_listener(boom)
    bus.add_listener(good.append)
    # Must not raise despite `boom`.
    await bus.publish(make_event(
        session_id="s1", source="chat", kind="user", summary="hi"
    ))
    # The well-behaved listener still ran, and the disk write completed.
    assert len(good) == 1
    assert events_log_path().exists()
    assert len(events_log_path().read_text().splitlines()) == 1


def test_get_bus_is_singleton(workspace) -> None:
    a = get_bus()
    b = get_bus()
    assert a is b


def test_reset_bus_creates_a_fresh_instance(workspace) -> None:
    a = get_bus()
    reset_bus()
    b = get_bus()
    assert a is not b
