"""Tests for alb.infra.metric_store.MetricStore (ADR-049 read model).

Time is injected via a fake clock so bucketing / pruning / is_warm are
deterministic (no sleeps).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from alb.infra.event_bus import EventBroadcaster, make_event
from alb.infra.metric_store import (
    MetricStore,
    aggregate_rates,
    coerce_rate,
    get_metric_store,
    reset_metric_store,
)


class FakeClock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _tps_event(
    rate: float,
    *,
    backend: str = "ollama",
    session_id: str = "s1",
    tokens: int | None = None,
) -> dict:
    tok = int(rate) if tokens is None else tokens
    return make_event(
        session_id=session_id,
        source="chat",
        kind="tps_sample",
        summary=f"{rate} tok/s",
        data={
            "rate_per_s": rate,
            "tokens_window": tok,
            "window_s": 1.0,
            "total_tokens": tok,
            "backend": backend,
        },
    )


@pytest.fixture
def workspace(monkeypatch, tmp_path) -> Path:
    """Isolate events.jsonl writes (bus.publish appends) to a tmp dir."""
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    return tmp_path


# ── ingest / filtering ──────────────────────────────────────────────

def test_ingest_ignores_non_tps_kinds() -> None:
    store = MetricStore()
    store.ingest(
        make_event(session_id="s", source="chat", kind="user", summary="hi")
    )
    assert store.summary(window_s=300)["sample_count"] == 0


def test_ingest_uses_coerce_rate_fallback() -> None:
    """A tps_sample missing rate_per_s is still ingested by deriving the
    rate from tokens_window / window_s (backward-compat producers)."""
    store = MetricStore(clock=FakeClock())
    ev = make_event(
        session_id="s",
        source="chat",
        kind="tps_sample",
        summary="",
        data={"tokens_window": 10, "window_s": 0.5, "backend": "ollama"},
    )
    store.ingest(ev)
    assert store.summary(window_s=300)["tps"]["max"] == 20.0


# ── summary aggregation ─────────────────────────────────────────────

def test_summary_aggregates_rates() -> None:
    clk = FakeClock(1000.0)
    store = MetricStore(clock=clk)
    for r in (10, 20, 30):
        store.ingest(_tps_event(r))
    s = store.summary(window_s=300)
    assert s["sample_count"] == 3
    assert s["tps"]["mean"] == 20.0
    assert s["tps"]["max"] == 30.0
    assert s["tps"]["min"] == 10.0
    assert s["total_tokens"] == 60


def test_summary_filters_by_session() -> None:
    store = MetricStore(clock=FakeClock())
    store.ingest(_tps_event(10, session_id="a"))
    store.ingest(_tps_event(20, session_id="b"))
    only_a = store.summary(window_s=300, session_id="a")
    assert only_a["sample_count"] == 1
    assert only_a["tps"]["max"] == 10.0
    assert store.summary(window_s=300)["sample_count"] == 2  # cross-session


def test_summary_excludes_out_of_window() -> None:
    clk = FakeClock(1000.0)
    store = MetricStore(capacity_s=10_000, clock=clk)
    store.ingest(_tps_event(10))  # ts=1000
    clk.advance(500)              # now 1500
    store.ingest(_tps_event(20))  # ts=1500
    # window=300 → since=1200 → only the ts=1500 sample qualifies
    s = store.summary(window_s=300)
    assert s["sample_count"] == 1
    assert s["tps"]["max"] == 20.0


# ── throughput_series (sparkline) ───────────────────────────────────

def test_throughput_series_groups_and_buckets() -> None:
    clk = FakeClock(1000.0)
    store = MetricStore(capacity_s=900.0, clock=clk)
    store.ingest(_tps_event(10, backend="ollama"))     # ts=1000
    clk.advance(100)                                    # now 1100
    store.ingest(_tps_event(20, backend="ollama"))      # ts=1100
    store.ingest(_tps_event(5, backend="lmstudio"))     # ts=1100

    series = store.throughput_series(window_s=200, buckets=10, group_by="backend")
    assert set(series) == {"ollama", "lmstudio"}
    o = series["ollama"]
    assert len(o["samples"]) == 10
    assert o["total_tokens"] == 30
    # since = now(1100) - 200 = 900; bucket_s = 20
    # ts=1000 → idx (1000-900)/20 = 5 ; ts=1100 → 10 → clamp 9
    assert o["samples"][5] == 10.0
    assert o["samples"][9] == 20.0
    assert o["samples"][0] == 0.0  # empty bucket → flat baseline
    assert o["tps"]["max"] == 20.0
    assert series["lmstudio"]["samples"][9] == 5.0


def test_throughput_series_empty_when_no_data() -> None:
    store = MetricStore()
    assert store.throughput_series(window_s=300, buckets=15) == {}


def test_throughput_series_unknown_backend_groups_under_empty_key() -> None:
    store = MetricStore(clock=FakeClock())
    store.ingest(_tps_event(7, backend=""))
    series = store.throughput_series(window_s=300, buckets=5)
    assert "" in series
    assert series[""]["tps"]["max"] == 7.0


# ── pruning / capacity ──────────────────────────────────────────────

def test_prune_drops_samples_older_than_capacity() -> None:
    clk = FakeClock(0.0)
    store = MetricStore(capacity_s=100.0, clock=clk)
    store.ingest(_tps_event(10))   # ts=0
    clk.advance(150)               # now 150 → cutoff 50 drops ts=0
    store.ingest(_tps_event(20))   # ts=150
    s = store.summary(window_s=10_000)  # window covers everything still held
    assert s["sample_count"] == 1
    assert s["tps"]["max"] == 20.0


# ── is_warm ─────────────────────────────────────────────────────────

def test_is_warm_transitions_and_capacity_ceiling() -> None:
    clk = FakeClock(1000.0)
    store = MetricStore(capacity_s=900.0, clock=clk)
    assert store.is_warm(300) is False  # never attached / ingested
    store.attach(EventBroadcaster())    # _attached_at = 1000
    assert store.is_warm(300) is False  # 0s elapsed < 300
    clk.advance(300)
    assert store.is_warm(300) is True
    assert store.is_warm(1000) is False  # window > capacity → file fallback


# ── bus lifecycle ───────────────────────────────────────────────────

def test_attach_is_idempotent_per_bus() -> None:
    bus = EventBroadcaster()
    store = MetricStore()
    store.attach(bus)
    store.attach(bus)
    assert bus.listener_count == 1  # no double registration
    store.detach()
    assert bus.listener_count == 0


def test_attach_rebinds_to_new_bus() -> None:
    b1, b2 = EventBroadcaster(), EventBroadcaster()
    store = MetricStore()
    store.attach(b1)
    store.attach(b2)
    assert b1.listener_count == 0  # old bus detached
    assert b2.listener_count == 1


@pytest.mark.asyncio
async def test_ingests_published_bus_events(workspace) -> None:
    bus = EventBroadcaster()
    store = MetricStore()
    store.attach(bus)
    await bus.publish(_tps_event(42, backend="ollama"))
    s = store.summary(window_s=300)
    assert s["sample_count"] == 1
    assert s["tps"]["max"] == 42.0


# ── pure helpers ────────────────────────────────────────────────────

def test_coerce_rate() -> None:
    assert coerce_rate({"rate_per_s": 30}) == 30.0
    assert coerce_rate({"tokens_window": 10, "window_s": 0.5}) == 20.0
    assert coerce_rate({"tokens_window": 10, "window_s": 0}) is None
    assert coerce_rate({}) is None


def test_aggregate_rates_empty_is_null() -> None:
    agg = aggregate_rates([], 0)
    assert agg["tps"] is None
    assert agg["sample_count"] == 0
    assert agg["total_tokens"] == 0


# ── singleton ───────────────────────────────────────────────────────

def test_singleton_get_and_reset() -> None:
    a = get_metric_store()
    assert get_metric_store() is a
    reset_metric_store()
    assert get_metric_store() is not a
