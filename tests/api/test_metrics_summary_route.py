"""Tests for GET /metrics/summary."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alb.api.server import create_app


@pytest.fixture
def workspace(monkeypatch, tmp_path) -> Path:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    return tmp_path


@pytest.fixture
def client(workspace):
    app = create_app()
    with TestClient(app) as c:
        yield c


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _write_samples(
    workspace: Path,
    rates: list[int],
    *,
    session_id: str = "s1",
    age_seconds: int = 30,
) -> Path:
    """Write tps_sample rows to events.jsonl with given rate_per_s values."""
    path = workspace / "events.jsonl"
    ts = (_now() - timedelta(seconds=age_seconds)).isoformat()
    with path.open("a", encoding="utf-8") as f:
        for r in rates:
            row = {
                "ts": ts,
                "session_id": session_id,
                "source": "chat",
                "kind": "tps_sample",
                "summary": f"{r} tok/s",
                "data": {
                    "tokens_window": r,  # 1s window: tokens_window == rate_per_s
                    "window_s": 1.0,
                    "total_tokens": r,
                    "rate_per_s": r,
                },
            }
            f.write(json.dumps(row) + "\n")
    return path


def test_empty_log_returns_zero_summary(client) -> None:
    body = client.get("/metrics/summary").json()
    assert body["ok"] is True
    assert body["sample_count"] == 0
    assert body["total_tokens"] == 0
    assert body["tps"] is None
    assert body["window_s"] == 300


def test_basic_aggregation(client, workspace) -> None:
    _write_samples(workspace, [10, 20, 30, 40, 50])
    body = client.get("/metrics/summary").json()
    assert body["sample_count"] == 5
    assert body["tps"]["mean"] == 30
    assert body["tps"]["min"] == 10
    assert body["tps"]["max"] == 50
    assert body["tps"]["p50"] == 30  # median of 1..5 mapped values
    # total_tokens = sum of tokens_window
    assert body["total_tokens"] == 150


def test_single_sample_percentile_no_crash(client, workspace) -> None:
    """1 sample: all percentiles == that single value."""
    _write_samples(workspace, [42])
    body = client.get("/metrics/summary").json()
    assert body["sample_count"] == 1
    assert body["tps"]["mean"] == 42
    assert body["tps"]["p50"] == 42
    assert body["tps"]["p95"] == 42
    assert body["tps"]["min"] == 42
    assert body["tps"]["max"] == 42


def test_two_sample_percentile(client, workspace) -> None:
    """2 samples: p50 = midpoint, p95 close to upper, no crash."""
    _write_samples(workspace, [10, 30])
    body = client.get("/metrics/summary").json()
    assert body["sample_count"] == 2
    assert body["tps"]["mean"] == 20
    assert body["tps"]["p50"] == 20  # linear interp midpoint
    assert body["tps"]["min"] == 10
    assert body["tps"]["max"] == 30


def test_window_filter(client, workspace) -> None:
    """Old samples (outside window_seconds) must be excluded."""
    _write_samples(workspace, [100, 100, 100], age_seconds=10)
    _write_samples(workspace, [10, 10], age_seconds=600)  # outside default 300s

    body = client.get("/metrics/summary").json()
    assert body["sample_count"] == 3
    assert body["tps"]["mean"] == 100

    body_wide = client.get("/metrics/summary?window_seconds=900").json()
    assert body_wide["sample_count"] == 5


def test_session_id_filter(client, workspace) -> None:
    _write_samples(workspace, [50, 60], session_id="s1")
    _write_samples(workspace, [200, 220], session_id="s2")

    body_all = client.get("/metrics/summary").json()
    assert body_all["sample_count"] == 4

    body_s1 = client.get("/metrics/summary?session_id=s1").json()
    assert body_s1["sample_count"] == 2
    assert body_s1["tps"]["max"] == 60

    body_s2 = client.get("/metrics/summary?session_id=s2").json()
    assert body_s2["sample_count"] == 2
    assert body_s2["tps"]["min"] == 200


def test_percentiles_basic(client, workspace) -> None:
    _write_samples(workspace, list(range(1, 101)))  # 1..100
    body = client.get("/metrics/summary").json()
    assert body["sample_count"] == 100
    # p50 ≈ 50.5, p95 ≈ 95.05, max == 100
    assert 49 <= body["tps"]["p50"] <= 51
    assert 94 <= body["tps"]["p95"] <= 96
    assert body["tps"]["max"] == 100


def test_param_bounds(client) -> None:
    assert client.get("/metrics/summary?window_seconds=5").status_code == 422
    assert client.get("/metrics/summary?window_seconds=99999999").status_code == 422


def test_legacy_sample_without_rate_per_s(client, workspace) -> None:
    """Older tps_sample rows (pre-rate_per_s field) should still aggregate
    via tokens_window / window_s fallback. total_tokens still summed."""
    path = workspace / "events.jsonl"
    ts = (_now() - timedelta(seconds=30)).isoformat()
    row = {
        "ts": ts,
        "session_id": "s1",
        "source": "chat",
        "kind": "tps_sample",
        "summary": "20 tok/s",
        "data": {
            # no rate_per_s — derive from window
            "tokens_window": 5,
            "window_s": 0.25,  # 5/0.25 = 20 tok/s
            "total_tokens": 5,
        },
    }
    path.write_text(json.dumps(row) + "\n")
    body = client.get("/metrics/summary").json()
    assert body["sample_count"] == 1
    assert body["tps"]["mean"] == 20.0
    assert body["total_tokens"] == 5


def test_total_tokens_accepts_float_tokens_window(client, workspace) -> None:
    """If a row has tokens_window as float, total_tokens still aggregates
    (cast to int per response schema)."""
    path = workspace / "events.jsonl"
    ts = (_now() - timedelta(seconds=30)).isoformat()
    row = {
        "ts": ts, "session_id": "s1", "source": "chat",
        "kind": "tps_sample", "summary": "5.5 tok/s",
        "data": {"tokens_window": 5.5, "window_s": 1.0,
                 "rate_per_s": 5.5},
    }
    path.write_text(json.dumps(row) + "\n")
    body = client.get("/metrics/summary").json()
    assert body["sample_count"] == 1
    assert body["total_tokens"] == 5  # int(5.5)


def test_malformed_rows_skipped(client, workspace) -> None:
    path = workspace / "events.jsonl"
    ts = (_now() - timedelta(seconds=30)).isoformat()
    path.write_text(
        "not-json\n"
        + json.dumps({"ts": ts, "kind": "tps_sample",
                      "data": {"rate_per_s": 42, "tokens_window": 42}}) + "\n"
        + json.dumps({"ts": "bad-ts", "kind": "tps_sample",
                      "data": {"rate_per_s": 99}}) + "\n"
        + json.dumps({"ts": ts, "kind": "user", "summary": "ignore me"}) + "\n"
    )
    body = client.get("/metrics/summary").json()
    assert body["sample_count"] == 1
    assert body["tps"]["mean"] == 42
    assert body["total_tokens"] == 42


def test_session_id_empty_string_rejected(client) -> None:
    """Empty session_id should 422 instead of silently filtering to 0."""
    r = client.get("/metrics/summary?session_id=")
    assert r.status_code == 422


def test_endpoint_listed_in_schema(client) -> None:
    paths = [(e["method"], e["path"]) for e in client.get("/api/version").json()["rest"]]
    assert ("GET", "/metrics/summary") in paths
