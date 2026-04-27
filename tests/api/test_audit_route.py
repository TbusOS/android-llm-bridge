"""Tests for GET /audit."""

from __future__ import annotations

import json
import os
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


def _write_terminal_jsonl(path: Path, lines: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for d in lines:
            f.write(json.dumps(d) + "\n")


def _write_messages_jsonl(path: Path, lines: list[dict], *, mtime: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for d in lines:
            f.write(json.dumps(d) + "\n")
    ts = mtime.timestamp()
    os.utime(path, (ts, ts))


def test_empty_workspace(client) -> None:
    r = client.get("/audit")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["events"] == []
    assert body["since"] < body["until"]


def test_terminal_events_filtered_by_window(client, workspace) -> None:
    now = datetime.now(timezone.utc)
    in_window = (now - timedelta(minutes=5)).isoformat()
    out_of_window = (now - timedelta(hours=2)).isoformat()
    _write_terminal_jsonl(
        workspace / "sessions/sid-A/terminal.jsonl",
        [
            {"ts": in_window, "event": "command", "line": "ls /data"},
            {"ts": out_of_window, "event": "command", "line": "rm /tmp/x"},
            {"ts": in_window, "event": "deny", "line": "rm -rf /", "rule": "fs_destructive"},
        ],
    )

    body = client.get("/audit").json()
    events = body["events"]
    assert [e["kind"] for e in events] == ["command", "deny"] or \
           [e["kind"] for e in events] == ["deny", "command"]
    assert all(e["source"] == "terminal" for e in events)
    assert all(e["ts_approx"] is False for e in events)
    assert all(e["session_id"] == "sid-A" for e in events)
    summaries = " ".join(e["summary"] for e in events)
    assert "ls /data" in summaries
    assert "rm -rf /" in summaries
    assert "fs_destructive" in summaries
    assert "rm /tmp/x" not in summaries  # out-of-window dropped


def test_chat_events_use_file_mtime_with_ts_approx_true(client, workspace) -> None:
    now = datetime.now(timezone.utc)
    recent = now - timedelta(minutes=10)
    _write_messages_jsonl(
        workspace / "sessions/sid-B/messages.jsonl",
        [
            {"role": "user", "content": "hello there"},
            {"role": "assistant", "content": "hi", "tool_calls": [
                {"id": "1", "name": "alb_top", "arguments": {}}]},
            {"role": "tool", "tool_call_id": "1", "name": "alb_top",
             "content": "{cpu: 50}"},
        ],
        mtime=recent,
    )

    events = client.get("/audit").json()["events"]
    assert len(events) == 3
    assert all(e["source"] == "chat" for e in events)
    assert all(e["ts_approx"] is True for e in events)
    kinds = [e["kind"] for e in events]
    assert kinds.count("user") == 1
    assert kinds.count("assistant") == 1
    assert kinds.count("tool") == 1
    summaries = " ".join(e["summary"] for e in events)
    assert "hello there" in summaries
    assert "tool_calls: alb_top" in summaries
    assert "tool result" in summaries


def test_chat_events_dropped_when_file_mtime_outside_window(client, workspace) -> None:
    very_old = datetime.now(timezone.utc) - timedelta(hours=4)
    _write_messages_jsonl(
        workspace / "sessions/sid-stale/messages.jsonl",
        [{"role": "user", "content": "ancient"}],
        mtime=very_old,
    )
    events = client.get("/audit").json()["events"]
    assert events == []


def test_minutes_param_widens_window(client, workspace) -> None:
    """A 4-hour-old event should appear when ?minutes=300 is requested."""
    old_chat = datetime.now(timezone.utc) - timedelta(hours=4)
    _write_messages_jsonl(
        workspace / "sessions/sid-old/messages.jsonl",
        [{"role": "user", "content": "still recent enough"}],
        mtime=old_chat,
    )
    short = client.get("/audit?minutes=30").json()["events"]
    long = client.get("/audit?minutes=300").json()["events"]
    assert short == []
    assert any("still recent enough" in e["summary"] for e in long)


def test_events_sorted_newest_first(client, workspace) -> None:
    now = datetime.now(timezone.utc)
    _write_terminal_jsonl(
        workspace / "sessions/sid-C/terminal.jsonl",
        [
            {"ts": (now - timedelta(minutes=2)).isoformat(),
             "event": "command", "line": "newer"},
            {"ts": (now - timedelta(minutes=20)).isoformat(),
             "event": "command", "line": "older"},
        ],
    )
    events = client.get("/audit").json()["events"]
    assert "newer" in events[0]["summary"]
    assert "older" in events[1]["summary"]
    assert events[0]["ts"] > events[1]["ts"]


def test_malformed_lines_skipped(client, workspace) -> None:
    p = workspace / "sessions/sid-bad/terminal.jsonl"
    p.parent.mkdir(parents=True)
    now = datetime.now(timezone.utc)
    p.write_text(
        "not-json\n"
        + json.dumps({"ts": (now - timedelta(minutes=5)).isoformat(),
                      "event": "command", "line": "good"}) + "\n"
        + json.dumps({"event": "command", "line": "no-ts"}) + "\n"  # no ts
    )
    events = client.get("/audit").json()["events"]
    assert len(events) == 1
    assert "good" in events[0]["summary"]


def test_limit_truncates(client, workspace) -> None:
    now = datetime.now(timezone.utc)
    lines = [
        {"ts": (now - timedelta(minutes=i)).isoformat(),
         "event": "command", "line": f"cmd{i}"}
        for i in range(1, 11)
    ]
    _write_terminal_jsonl(workspace / "sessions/sid-D/terminal.jsonl", lines)
    events = client.get("/audit?limit=3").json()["events"]
    assert len(events) == 3


def test_param_bounds(client) -> None:
    assert client.get("/audit?minutes=0").status_code == 422
    assert client.get("/audit?minutes=99999").status_code == 422
    assert client.get("/audit?limit=0").status_code == 422
    assert client.get("/audit?limit=9999").status_code == 422


def test_endpoint_listed_in_schema(client) -> None:
    paths = [(e["method"], e["path"]) for e in client.get("/api/version").json()["rest"]]
    assert ("GET", "/audit") in paths
