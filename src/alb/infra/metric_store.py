"""In-memory materialized read model for periodic metric events (ADR-049).

A process-wide projection of the event bus: registers as a synchronous
in-process listener (`EventBroadcaster.add_listener`) and keeps a rolling
window of recent metric samples. Serves the dashboard's hot-path metric
queries — the cross-backend LLM-throughput KPI and the per-backend
sparkline — in O(window) instead of re-scanning the append-only
`events.jsonl` on every poll (O(whole log); see DEBT-008).

Boundary (ADR-049): `events.jsonl` is the durable WRITE model (audit
history, long-window fallback); MetricStore is the ephemeral READ model
(fast near-window reads). They are decoupled — the store NEVER reads the
file, it only ingests live events. On a process restart the store starts
empty and refills within one window; callers that need completeness across
a restart (or a window longer than the store's capacity) fall back to the
file scan — `is_warm()` tells them which path to take.

Lifecycle: one process-wide instance via `get_metric_store()`, attached to
the bus in the app lifespan (startup) and detached on shutdown.
`attach()` / `detach()` are idempotent so a per-test `create_app()`
re-attaching to a shared bus cannot double-register the listener (which
would double-count every sample). `reset_metric_store()` drops the
singleton for tests (pair with `reset_bus()`).

Extensibility: a new metric kind (terminal cmd-rate, push byte-rate — the
metric_sampler docstring already anticipates these) is one more kind here
plus a query method, NOT a new pipeline (ADR-021 "metric kind" class).
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from alb.infra.event_bus import EventBroadcaster

# Bus event kind this store projects. Narrow on purpose.
_TPS_KIND = "tps_sample"

# How much recent history the read model keeps. Covers the 5-min KPI
# window + 5-min sparkline with headroom; a query for a longer window
# falls back to the file scan (see metrics_summary_route).
DEFAULT_CAPACITY_S = 900.0


@dataclass
class _Sample:
    ts: float        # epoch seconds (ingest time ≈ emit time)
    rate: float      # rate_per_s for this 1 Hz sample
    tokens: int      # tokens_window counted in this sample
    backend: str     # "" when unknown (legacy / pre-ADR-049 producers)
    session_id: str


def coerce_rate(data: dict[str, Any]) -> float | None:
    """rate_per_s, or derive from tokens_window/window_s (backward-compat)."""
    rate = data.get("rate_per_s")
    if isinstance(rate, (int, float)):
        return float(rate)
    tw = data.get("tokens_window")
    ws = data.get("window_s")
    if isinstance(tw, (int, float)) and isinstance(ws, (int, float)) and ws > 0:
        return float(tw) / float(ws)
    return None


def _percentile(sorted_values: list[float], p: float) -> float:
    """Linear-interpolation percentile. p in [0, 100]. Shared by the
    in-memory read model and the file-scan fallback so both paths return
    identical statistics (metrics_summary_route imports this)."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    rank = (p / 100.0) * (len(sorted_values) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = rank - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


def aggregate_rates(rates: list[float], total_tokens: int) -> dict[str, Any]:
    """Summarise per-sample rates → {tps:{mean,p50,p95,max,min}|None,
    total_tokens, sample_count}. Single impl for memory + file paths."""
    rs = sorted(rates)
    if rs:
        tps: dict[str, float] | None = {
            "mean": sum(rs) / len(rs),
            "p50": _percentile(rs, 50),
            "p95": _percentile(rs, 95),
            "max": rs[-1],
            "min": rs[0],
        }
    else:
        tps = None
    return {"tps": tps, "total_tokens": total_tokens, "sample_count": len(rs)}


class MetricStore:
    """Rolling in-memory window of metric samples (see module docstring)."""

    def __init__(
        self,
        *,
        capacity_s: float = DEFAULT_CAPACITY_S,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._capacity_s = capacity_s
        self._clock = clock
        self._samples: deque[_Sample] = deque()
        self._bus: EventBroadcaster | None = None
        self._attached_at: float | None = None

    @property
    def capacity_s(self) -> float:
        return self._capacity_s

    # ── lifecycle ───────────────────────────────────────────────────
    def attach(self, bus: EventBroadcaster) -> None:
        """Register as a listener on `bus`. Idempotent: re-attaching to the
        same bus is a no-op; attaching to a different bus detaches the old
        one first, so the store always projects exactly one bus."""
        if self._bus is bus:
            return
        if self._bus is not None:
            self._bus.remove_listener(self.ingest)
        self._bus = bus
        bus.add_listener(self.ingest)
        if self._attached_at is None:
            self._attached_at = self._clock()

    def detach(self) -> None:
        if self._bus is not None:
            self._bus.remove_listener(self.ingest)
            self._bus = None

    # ── ingestion (sync listener — MUST stay cheap + never raise) ────
    def ingest(self, event: dict[str, Any]) -> None:
        if event.get("kind") != _TPS_KIND:
            return
        data = event.get("data") or {}
        rate = coerce_rate(data)
        if rate is None:
            return
        tokens = data.get("tokens_window")
        now = self._clock()
        self._samples.append(
            _Sample(
                ts=now,
                rate=rate,
                tokens=int(tokens) if isinstance(tokens, (int, float)) else 0,
                backend=str(data.get("backend") or ""),
                session_id=str(event.get("session_id") or ""),
            )
        )
        if self._attached_at is None:
            self._attached_at = now
        self._prune(now)

    def _prune(self, now: float) -> None:
        cutoff = now - self._capacity_s
        s = self._samples
        while s and s[0].ts < cutoff:
            s.popleft()

    # ── queries ─────────────────────────────────────────────────────
    def is_warm(self, window_s: float) -> bool:
        """True when the in-memory window fully covers `window_s` (and it
        fits in capacity). When False the caller falls back to the file
        scan for a complete answer (cold start / window > capacity)."""
        if window_s > self._capacity_s:
            return False
        if self._attached_at is None:
            return False
        return (self._clock() - self._attached_at) >= window_s

    def summary(
        self, *, window_s: float, session_id: str | None = None
    ) -> dict[str, Any]:
        """Aggregate per-sample rates over the window (optionally one
        session). Same shape as the file path's aggregate."""
        now = self._clock()
        since = now - window_s
        rates: list[float] = []
        total_tokens = 0
        for s in self._samples:
            if s.ts < since:
                continue
            if session_id is not None and s.session_id != session_id:
                continue
            rates.append(s.rate)
            total_tokens += s.tokens
        return aggregate_rates(rates, total_tokens)

    def throughput_series(
        self, *, window_s: float, buckets: int, group_by: str = "backend"
    ) -> dict[str, dict[str, Any]]:
        """Per-group bucketed rate series for the sparkline.

        Returns {group_key: {"samples": [rate per bucket, oldest→newest],
        "total_tokens", "tps": {...}|None, "sample_count"}}. A bucket's
        rate is the mean of the per-sample rates whose timestamp lands in
        it; empty buckets are 0.0 so idle stretches render as a flat
        baseline. `group_by` currently supports "backend"."""
        if buckets <= 0:
            buckets = 1
        now = self._clock()
        since = now - window_s
        bucket_s = window_s / buckets if buckets else window_s
        groups: dict[str, dict[str, Any]] = {}
        for s in self._samples:
            if s.ts < since:
                continue
            key = s.backend if group_by == "backend" else s.session_id
            g = groups.get(key)
            if g is None:
                g = {
                    "sum": [0.0] * buckets,
                    "cnt": [0] * buckets,
                    "rates": [],
                    "total_tokens": 0,
                }
                groups[key] = g
            idx = int((s.ts - since) / bucket_s) if bucket_s > 0 else 0
            idx = max(0, min(buckets - 1, idx))
            g["sum"][idx] += s.rate
            g["cnt"][idx] += 1
            g["rates"].append(s.rate)
            g["total_tokens"] += s.tokens
        out: dict[str, dict[str, Any]] = {}
        for key, g in groups.items():
            samples = [
                (g["sum"][i] / g["cnt"][i] if g["cnt"][i] else 0.0)
                for i in range(buckets)
            ]
            agg = aggregate_rates(g["rates"], g["total_tokens"])
            out[key] = {
                "samples": samples,
                "total_tokens": g["total_tokens"],
                "tps": agg["tps"],
                "sample_count": agg["sample_count"],
            }
        return out

    def reset(self) -> None:
        self.detach()
        self._samples.clear()
        self._attached_at = None


_STORE: MetricStore | None = None


def get_metric_store() -> MetricStore:
    """Process-wide MetricStore, lazily created."""
    global _STORE
    if _STORE is None:
        _STORE = MetricStore()
    return _STORE


def reset_metric_store() -> None:
    """Drop the singleton — tests use this (pair with `reset_bus()`)."""
    global _STORE
    if _STORE is not None:
        _STORE.detach()
    _STORE = None
