"""Tests for GET /audit — driven by workspace/events.jsonl."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

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


def _write_events(workspace: Path, events: list[dict[str, Any]]) -> Path:
    """Append events to workspace/events.jsonl, creating it if needed."""
    path = workspace / "events.jsonl"
    with path.open("a", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
    return path


def _now() -> datetime:
    return datetime.now(timezone.utc)


def test_empty_log_returns_no_events(client) -> None:
    r = client.get("/audit")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["events"] == []
    assert body["since"] < body["until"]


def test_window_filters_old_events(client, workspace) -> None:
    now = _now()
    in_window = (now - timedelta(minutes=5)).isoformat()
    out_of_window = (now - timedelta(hours=2)).isoformat()
    _write_events(
        workspace,
        [
            {"ts": in_window, "session_id": "s1", "source": "terminal",
             "kind": "command", "summary": "$ ls /data"},
            {"ts": out_of_window, "session_id": "s1", "source": "terminal",
             "kind": "command", "summary": "$ rm /tmp/x"},
            {"ts": in_window, "session_id": "s1", "source": "terminal",
             "kind": "deny", "summary": "deny: rm -rf / (fs_destructive)"},
        ],
    )
    body = client.get("/audit").json()
    summaries = [e["summary"] for e in body["events"]]
    assert any("ls /data" in s for s in summaries)
    assert any("rm -rf /" in s for s in summaries)
    assert not any("rm /tmp/x" in s for s in summaries)
    assert all(e["ts_approx"] is False for e in body["events"])


def test_chat_and_terminal_events_coexist(client, workspace) -> None:
    now = _now()
    ts = (now - timedelta(minutes=2)).isoformat()
    _write_events(
        workspace,
        [
            {"ts": ts, "session_id": "chat-1", "source": "chat",
             "kind": "user", "summary": "find battery drain"},
            {"ts": ts, "session_id": "chat-1", "source": "chat",
             "kind": "tool_call_start", "summary": "tool_calls: alb_top"},
            {"ts": ts, "session_id": "term-1", "source": "terminal",
             "kind": "command", "summary": "$ uptime"},
        ],
    )
    body = client.get("/audit").json()
    assert len(body["events"]) == 3
    sources = {e["source"] for e in body["events"]}
    assert sources == {"chat", "terminal"}


def test_minutes_param_widens_window(client, workspace) -> None:
    now = _now()
    old = (now - timedelta(hours=4)).isoformat()
    _write_events(
        workspace,
        [
            {"ts": old, "session_id": "s1", "source": "chat",
             "kind": "user", "summary": "still recent enough"},
        ],
    )
    short = client.get("/audit?minutes=30").json()["events"]
    long = client.get("/audit?minutes=300").json()["events"]
    assert short == []
    assert any("still recent enough" in e["summary"] for e in long)


def test_events_sorted_newest_first(client, workspace) -> None:
    now = _now()
    _write_events(
        workspace,
        [
            {"ts": (now - timedelta(minutes=2)).isoformat(),
             "session_id": "s1", "source": "terminal",
             "kind": "command", "summary": "newer"},
            {"ts": (now - timedelta(minutes=20)).isoformat(),
             "session_id": "s1", "source": "terminal",
             "kind": "command", "summary": "older"},
        ],
    )
    events = client.get("/audit").json()["events"]
    assert events[0]["summary"] == "newer"
    assert events[1]["summary"] == "older"
    assert events[0]["ts"] > events[1]["ts"]


def test_malformed_rows_skipped(client, workspace) -> None:
    """A bad JSONL row or a row with missing/unparseable ts is dropped."""
    path = workspace / "events.jsonl"
    now = _now()
    valid = json.dumps({
        "ts": (now - timedelta(minutes=5)).isoformat(),
        "session_id": "s1", "source": "terminal",
        "kind": "command", "summary": "good",
    })
    no_ts = json.dumps({
        "session_id": "s1", "source": "terminal",
        "kind": "command", "summary": "no ts",
    })
    bad_ts = json.dumps({
        "ts": "not-a-date", "session_id": "s1", "source": "terminal",
        "kind": "command", "summary": "bad ts",
    })
    path.write_text(f"not-json\n{valid}\n{no_ts}\n{bad_ts}\n")
    events = client.get("/audit").json()["events"]
    assert len(events) == 1
    assert events[0]["summary"] == "good"


def test_limit_truncates(client, workspace) -> None:
    now = _now()
    _write_events(
        workspace,
        [
            {"ts": (now - timedelta(minutes=i)).isoformat(),
             "session_id": "s1", "source": "terminal",
             "kind": "command", "summary": f"cmd{i}"}
            for i in range(1, 11)
        ],
    )
    events = client.get("/audit?limit=3").json()["events"]
    assert len(events) == 3


def test_param_bounds(client) -> None:
    assert client.get("/audit?minutes=0").status_code == 422
    assert client.get("/audit?minutes=99999").status_code == 422
    assert client.get("/audit?limit=0").status_code == 422
    assert client.get("/audit?limit=9999").status_code == 422


def test_default_fields_filled_in(client, workspace) -> None:
    """A minimal row that only has ts still produces a usable event with
    sensible defaults for missing keys."""
    now = _now()
    _write_events(
        workspace,
        [
            {"ts": (now - timedelta(minutes=1)).isoformat()},
        ],
    )
    body = client.get("/audit").json()
    assert len(body["events"]) == 1
    e = body["events"][0]
    assert e["session_id"] == ""
    assert e["source"] == "system"
    assert e["kind"] == "unknown"
    assert e["summary"] == ""
    assert e["ts_approx"] is False


def test_endpoint_listed_in_schema(client) -> None:
    paths = [(e["method"], e["path"]) for e in client.get("/api/version").json()["rest"]]
    assert ("GET", "/audit") in paths
