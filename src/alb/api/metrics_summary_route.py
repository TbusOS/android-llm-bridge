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
        "sample_count": int
    }
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query

from alb.infra.event_bus import events_log_path

router = APIRouter()


def _parse_ts(value: str) -> datetime | None:
    try:
        ts = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _percentile(sorted_values: list[float], p: float) -> float:
    """Linear-interpolation percentile. p in [0, 100]."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    rank = (p / 100.0) * (len(sorted_values) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = rank - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


def _read_tps_samples(
    path: Path,
    *,
    since: datetime,
    until: datetime,
    session_id: str | None,
) -> list[dict[str, Any]]:
    """Stream-read events.jsonl, keep only tps_sample rows in window."""
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
) -> dict[str, Any]:
    """Aggregate `tps_sample` events over a sliding window.

    `window_seconds` defaults to 5 minutes (300s); allowed range
    10s–24h. `session_id` is optional — when provided the summary
    only covers that session (useful for a session detail view).
    Without it the summary is cross-session (used by the Dashboard's
    LLM throughput KPI).
    """
    until = datetime.now(timezone.utc)
    since = until - timedelta(seconds=window_seconds)

    samples = _read_tps_samples(
        events_log_path(),
        since=since,
        until=until,
        session_id=session_id,
    )

    rates: list[float] = []
    total_tokens = 0
    for s in samples:
        data = s.get("data") or {}
        rate = data.get("rate_per_s")
        if rate is None:
            # Backward-compat: derive from tokens_window / window_s
            tw = data.get("tokens_window")
            ws = data.get("window_s")
            if isinstance(tw, (int, float)) and isinstance(ws, (int, float)) and ws > 0:
                rate = tw / ws
        if isinstance(rate, (int, float)):
            rates.append(float(rate))
        tw = data.get("tokens_window")
        if isinstance(tw, (int, float)):
            # Both int and float accepted (rate fallback above is symmetric);
            # cast to int for the cumulative sum since total_tokens is
            # documented as int in the response schema.
            total_tokens += int(tw)

    rates.sort()
    if rates:
        tps = {
            "mean": sum(rates) / len(rates),
            "p50": _percentile(rates, 50),
            "p95": _percentile(rates, 95),
            "max": rates[-1],
            "min": rates[0],
        }
    else:
        tps = None

    return {
        "ok": True,
        "since": since.isoformat(),
        "until": until.isoformat(),
        "window_s": window_seconds,
        "session_id": session_id,
        "tps": tps,
        "total_tokens": total_tokens,
        "sample_count": len(rates),
    }
