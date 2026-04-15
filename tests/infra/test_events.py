"""Tests for the event bus."""

from __future__ import annotations

import pytest

from alb.infra.events import EventBus


@pytest.mark.asyncio
async def test_publish_without_subscribers_is_noop() -> None:
    eb = EventBus()
    await eb.publish("topic", "data")  # should not raise


@pytest.mark.asyncio
async def test_subscribe_and_receive() -> None:
    eb = EventBus()
    received: list[str] = []

    async def handler(ev):  # noqa: ANN001
        received.append(ev.data)

    eb.subscribe("lines", handler)
    await eb.publish("lines", "a")
    await eb.publish("lines", "b")
    assert received == ["a", "b"]


@pytest.mark.asyncio
async def test_unsubscribe() -> None:
    eb = EventBus()
    received: list[str] = []

    async def handler(ev):  # noqa: ANN001
        received.append(ev.data)

    unsub = eb.subscribe("lines", handler)
    await eb.publish("lines", "a")
    unsub()
    await eb.publish("lines", "b")
    assert received == ["a"]


@pytest.mark.asyncio
async def test_bad_handler_does_not_block_others() -> None:
    eb = EventBus()
    received: list[str] = []

    async def bad_handler(ev):  # noqa: ANN001
        raise ValueError("boom")

    async def good_handler(ev):  # noqa: ANN001
        received.append(ev.data)

    eb.subscribe("t", bad_handler)
    eb.subscribe("t", good_handler)
    await eb.publish("t", "x")
    assert received == ["x"]
