"""GET /metrics/summary — windowed aggregation of `tps_sample` events.

Drives the Web UI Dashboard's LLM throughput KPI (DEBT-004 unblock).
Reads `workspace/events.jsonl` (the same source as `/audit`), filters
to `kind == "tps_sample"` within a configurable time window, and
returns mean / p50 / p95 / max / min over the per-sample rates.

Why a separate file from `metrics_route.py`:
    `metrics_route.py` owns `WS /metrics/stream` — live device CPU /
    temp / IO telemetry (a different data source: `MetricsStreamer`
    over the active transport). Mixing them in one file would blur
    the "device metric" vs "LLM throughput" boundary; in particular
    the dependencies are disjoint (we don't need `build_transport`
    here), so a separate router keeps the imports minimal.

Schema (response):

    {
        "ok": true,
        "since": "<ISO 8601, UTC, with offset>",
        "until": "<ISO 8601, UTC, with offset>",
        "window_s": 300,
        "session_id": null | "<sid>",   # echoed verbatim — consumer
                                         # MUST escape before rendering
                                         # to HTML (do not use
                                         # dangerouslySetInnerHTML)
        "tps": {
            "mean": float,
            "p50":  float,
            "p95":  float,
            "max":  float,
            "min":  float
        } | null,                  # null when sample_count == 0
        "total_tokens": int,        # sum of tokens_window across samples
        "sample_count": int,
        "source": "memory" | "file" # which read path answered (ADR-049)
    }

With `?group_by=backend` the shape is instead:

    {
        "ok": true, "since": ..., "until": ..., "window_s": 300,
        "bucket_s": float,          # window_s / buckets
        "group_by": "backend",
        "source": "memory",
        "backends": {               # one entry per backend with activity
            "<name>": {
                "samples": [float],     # mean rate per bucket, oldest→newest
                "total_tokens": int,
                "tps": {mean,p50,p95,max,min} | null,
                "sample_count": int
            }
        }
    }
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query

from alb.infra.event_bus import events_log_path
from alb.infra.metric_store import aggregate_rates, coerce_rate, get_metric_store

router = APIRouter()


def _parse_ts(value: str) -> datetime | None:
    try:
        ts = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _read_tps_samples(
    path: Path,
    *,
    since: datetime,
    until: datetime,
    session_id: str | None,
) -> list[dict[str, Any]]:
    """Stream-read events.jsonl, keep only tps_sample rows in window.

    Pure sync — async callers must wrap in `asyncio.to_thread` per
    L-033 (this endpoint is polled every 30s by the Dashboard; a sync
    full-file scan on the loop freezes every WS stream while it runs).
    """
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("kind") != "tps_sample":
                continue
            if session_id is not None and row.get("session_id") != session_id:
                continue
            ts = _parse_ts(row.get("ts") or "")
            if ts is None or not (since <= ts <= until):
                continue
            out.append(row)
    return out


@router.get("/metrics/summary")
async def metrics_summary(
    window_seconds: int = Query(300, ge=10, le=86_400),
    session_id: str | None = Query(None, min_length=1, max_length=128),
    group_by: str | None = Query(None, pattern="^(backend)$"),
    buckets: int = Query(15, ge=1, le=120),
) -> dict[str, Any]:
    """Aggregate `tps_sample` events over a sliding window.

    `window_seconds` defaults to 5 minutes (300s); allowed range
    10s–24h. `session_id` is optional — when provided the summary only
    covers that session (session detail view); without it the summary
    is cross-session (Dashboard LLM throughput KPI).

    `group_by=backend` (ADR-049) switches to a per-backend bucketed
    *time series* for the dashboard's be-card sparkline: each backend
    gets `samples` (one mean rate per `buckets` time bucket, oldest →
    newest) plus its aggregates.

    Read path: the in-memory MetricStore (read model) serves near-window
    queries in O(window); the durable `events.jsonl` scan (O(file),
    DEBT-008) is the fallback for a cold store or a window beyond the
    store's capacity. The non-breaking `source` field reports which path
    answered.
    """
    until = datetime.now(timezone.utc)
    since = until - timedelta(seconds=window_seconds)
    store = get_metric_store()

    # Per-backend bucketed series — always from the in-memory read model.
    # It is an inherently live near-window view; after a restart it is
    # partial until refilled, rendering as a short/empty spark (fine).
    if group_by == "backend":
        series = store.throughput_series(
            window_s=window_seconds, buckets=buckets, group_by="backend"
        )
        return {
            "ok": True,
            "since": since.isoformat(),
            "until": until.isoformat(),
            "window_s": window_seconds,
            "bucket_s": window_seconds / buckets,
            "group_by": "backend",
            "source": "memory",
            "backends": series,
        }

    # Scalar summary. Fast path = in-memory read model when it fully
    # covers the window; cold start or window > capacity falls back to
    # the durable file scan so the answer stays complete across restarts.
    if store.is_warm(window_seconds):
        agg = store.summary(window_s=window_seconds, session_id=session_id)
        source = "memory"
    else:
        samples = await asyncio.to_thread(
            _read_tps_samples,
            events_log_path(),
            since=since,
            until=until,
            session_id=session_id,
        )
        rates: list[float] = []
        total_tokens = 0
        for s in samples:
            data = s.get("data") or {}
            rate = coerce_rate(data)
            if rate is not None:
                rates.append(rate)
            tw = data.get("tokens_window")
            if isinstance(tw, (int, float)):
                total_tokens += int(tw)
        agg = aggregate_rates(rates, total_tokens)
        source = "file"

    return {
        "ok": True,
        "since": since.isoformat(),
        "until": until.isoformat(),
        "window_s": window_seconds,
        "session_id": session_id,
        "source": source,
        "tps": agg["tps"],
        "total_tokens": agg["total_tokens"],
        "sample_count": agg["sample_count"],
    }
