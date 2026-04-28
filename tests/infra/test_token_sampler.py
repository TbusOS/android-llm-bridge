"""Tests for alb.infra.metric_sampler.TokenSampler."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from alb.infra.event_bus import (
    EventBroadcaster,
    events_log_path,
    get_bus,
    reset_bus,
)
from alb.infra.metric_sampler import TokenSampler


@pytest.fixture
def workspace(monkeypatch, tmp_path) -> Path:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    reset_bus()
    yield tmp_path
    reset_bus()


def _read_events(workspace: Path) -> list[dict]:
    p = events_log_path()
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def test_init_validates_interval() -> None:
    with pytest.raises(ValueError):
        TokenSampler(session_id="x", interval_s=0)
    with pytest.raises(ValueError):
        TokenSampler(session_id="x", interval_s=-0.1)


@pytest.mark.asyncio
async def test_observe_and_close_publishes_final_sample(workspace) -> None:
    sampler = TokenSampler(session_id="s1", interval_s=0.05)
    sampler.start()
    sampler.observe(10)
    sampler.observe(5)
    await sampler.close()

    events = _read_events(workspace)
    samples = [e for e in events if e["kind"] == "tps_sample"]
    assert samples, "expected at least one tps_sample after close()"
    last = samples[-1]
    # Total should reflect everything observed
    assert last["data"]["total_tokens"] == 15
    assert last["session_id"] == "s1"
    assert last["data"]["window_s"] == 0.05
    # rate_per_s present and consistent with tokens_window / window_s
    assert "rate_per_s" in last["data"]


@pytest.mark.asyncio
async def test_summary_reports_rate_not_raw_count(workspace) -> None:
    """summary should be `rate_per_s tok/s`, not raw window count, so it
    is unit-correct regardless of interval_s."""
    sampler = TokenSampler(session_id="s1", interval_s=0.05)
    sampler.start()
    sampler.observe(5)  # in 0.05s window → 100 tok/s
    await sampler.close()
    events = _read_events(workspace)
    samples = [e for e in events if e["kind"] == "tps_sample"
               and e["data"]["tokens_window"] == 5]
    assert samples
    # rate = 5 / 0.05 = 100
    assert samples[0]["data"]["rate_per_s"] == 100
    assert samples[0]["summary"] == "100 tok/s"


@pytest.mark.asyncio
async def test_periodic_flush_publishes_each_interval(workspace) -> None:
    sampler = TokenSampler(session_id="s1", interval_s=0.02)
    sampler.start()
    # Feed tokens over ~3 windows
    for _ in range(3):
        sampler.observe(4)
        await asyncio.sleep(0.05)  # >= 1 tick + headroom for CI jitter
    await sampler.close()

    events = _read_events(workspace)
    samples = [e for e in events if e["kind"] == "tps_sample"]
    # We slept 3 ticks + final-flush on close → at least 3 samples
    assert len(samples) >= 3
    # Cumulative total should reach 12 by the final sample
    assert samples[-1]["data"]["total_tokens"] == 12


@pytest.mark.asyncio
async def test_zero_token_window_skipped_during_periodic_flush(workspace) -> None:
    """Periodic flush with no tokens accumulated must NOT publish (saves
    disk + WS noise during quiet stretches). close() still emits a final
    sample even when zero (so consumers see the session ended)."""
    sampler = TokenSampler(session_id="s1", interval_s=0.02)
    sampler.start()
    await asyncio.sleep(0.10)  # 5 ticks with zero observes
    await sampler.close()
    events = _read_events(workspace)
    samples = [e for e in events if e["kind"] == "tps_sample"]
    # Only the final-flush sample (force=True), not 5 periodic empties
    assert len(samples) == 1
    assert samples[0]["data"]["tokens_window"] == 0
    assert samples[0]["data"]["total_tokens"] == 0


@pytest.mark.asyncio
async def test_close_is_idempotent(workspace) -> None:
    sampler = TokenSampler(session_id="s1", interval_s=0.05)
    sampler.start()
    sampler.observe(1)
    await sampler.close()
    await sampler.close()  # second call must not raise / re-flush
    events = _read_events(workspace)
    samples = [e for e in events if e["kind"] == "tps_sample"]
    # Final flush emitted exactly once; tokens_window includes the buffered 1
    assert any(s["data"]["tokens_window"] == 1 for s in samples)


@pytest.mark.asyncio
async def test_start_is_idempotent(workspace) -> None:
    sampler = TokenSampler(session_id="s1", interval_s=0.02)
    sampler.start()
    sampler.start()  # must not spawn a second task
    sampler.observe(2)
    await asyncio.sleep(0.05)
    await sampler.close()
    events = _read_events(workspace)
    samples = [e for e in events if e["kind"] == "tps_sample"]
    # Don't assert exact count; just verify total tokens not double-counted
    assert samples[-1]["data"]["total_tokens"] == 2


@pytest.mark.asyncio
async def test_observe_after_close_is_noop(workspace) -> None:
    sampler = TokenSampler(session_id="s1", interval_s=0.05)
    sampler.start()
    await sampler.close()
    sampler.observe(99)  # must be ignored
    events = _read_events(workspace)
    samples = [e for e in events if e["kind"] == "tps_sample"]
    # All emitted samples should have total_tokens == 0
    assert all(s["data"]["total_tokens"] == 0 for s in samples)


@pytest.mark.asyncio
async def test_observe_before_start_is_dropped(workspace) -> None:
    """Pre-start observe drops tokens (lifecycle contract: must start
    before accepting input)."""
    sampler = TokenSampler(session_id="s1", interval_s=0.05)
    sampler.observe(5)  # dropped, no buffering
    assert sampler.total_tokens == 0
    sampler.start()
    sampler.observe(3)
    await sampler.close()
    events = _read_events(workspace)
    samples = [e for e in events if e["kind"] == "tps_sample"]
    assert samples and samples[-1]["data"]["total_tokens"] == 3


@pytest.mark.asyncio
async def test_close_cancels_periodic_task(workspace) -> None:
    sampler = TokenSampler(session_id="s1", interval_s=0.05)
    sampler.start()
    assert sampler.is_running
    await sampler.close()
    assert not sampler.is_running


@pytest.mark.asyncio
async def test_publish_failure_does_not_break_sampler(monkeypatch, workspace) -> None:
    """If bus.publish raises, observe()/close() must not bubble it up
    (best-effort contract). A WARNING is logged but no exception escapes."""
    bus = EventBroadcaster()

    async def _bad_publish(event):  # noqa: ANN001
        raise RuntimeError("bus dead")

    monkeypatch.setattr(bus, "publish", _bad_publish)

    sampler = TokenSampler(session_id="s1", bus=bus, interval_s=0.05)
    sampler.start()
    sampler.observe(5)
    await asyncio.sleep(0.07)
    await sampler.close()  # must not raise


@pytest.mark.asyncio
async def test_no_start_no_publish(workspace) -> None:
    """close() before start() must be a no-op (no flush, no events)."""
    sampler = TokenSampler(session_id="s1", interval_s=0.05)
    await sampler.close()
    events = _read_events(workspace)
    samples = [e for e in events if e["kind"] == "tps_sample"]
    assert samples == []


@pytest.mark.asyncio
async def test_total_tokens_property(workspace) -> None:
    sampler = TokenSampler(session_id="s1", interval_s=0.1)
    assert sampler.total_tokens == 0
    # observe before start is dropped (lifecycle contract)
    sampler.observe(3)
    assert sampler.total_tokens == 0
    sampler.start()
    sampler.observe(3)
    sampler.observe(2)
    assert sampler.total_tokens == 5
    await sampler.close()


@pytest.mark.asyncio
async def test_observe_with_zero_or_negative(workspace) -> None:
    sampler = TokenSampler(session_id="s1", interval_s=0.1)
    sampler.start()
    sampler.observe(0)
    sampler.observe(-3)
    assert sampler.total_tokens == 0
    await sampler.close()


@pytest.mark.asyncio
async def test_observe_caps_runaway_input(workspace) -> None:
    """A single observe(huge_n) must be capped to OBSERVE_MAX (defensive
    against malformed backends pushing one giant chunk)."""
    from alb.infra.metric_sampler import OBSERVE_MAX

    sampler = TokenSampler(session_id="s1", interval_s=0.1)
    sampler.start()
    sampler.observe(OBSERVE_MAX * 5)
    assert sampler.total_tokens == OBSERVE_MAX
    await sampler.close()


def test_interval_from_env(monkeypatch) -> None:
    monkeypatch.setenv("ALB_TPS_SAMPLE_INTERVAL_S", "0.5")
    s = TokenSampler(session_id="s1")
    assert s._interval_s == 0.5

    monkeypatch.setenv("ALB_TPS_SAMPLE_INTERVAL_S", "not-a-number")
    s2 = TokenSampler(session_id="s2")
    assert s2._interval_s == 1.0  # falls back to default

    monkeypatch.setenv("ALB_TPS_SAMPLE_INTERVAL_S", "-1.0")
    s3 = TokenSampler(session_id="s3")
    assert s3._interval_s == 1.0  # negative → default
