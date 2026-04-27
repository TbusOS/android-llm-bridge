"""Tests for GET /sessions."""

from __future__ import annotations

import json
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


def _make_session(
    workspace: Path,
    sid: str,
    *,
    created: str,
    backend: str = "ollama",
    model: str = "qwen2.5:7b",
    device: str | None = None,
    turns: int = 0,
    bad_meta: bool = False,
) -> Path:
    sdir = workspace / "sessions" / sid
    sdir.mkdir(parents=True)
    meta = {
        "session_id": sid,
        "created": created,
        "backend": backend,
        "model": model,
        "device": device,
    }
    if bad_meta:
        (sdir / "meta.json").write_text("not-json{")
    else:
        (sdir / "meta.json").write_text(json.dumps(meta))
    if turns:
        with (sdir / "messages.jsonl").open("w", encoding="utf-8") as f:
            for i in range(turns):
                f.write(json.dumps({"role": "user", "content": f"line {i}"}) + "\n")
    return sdir


def test_empty_workspace_returns_empty_list(client) -> None:
    r = client.get("/sessions")
    assert r.status_code == 200
    body = r.json()
    assert body == {"ok": True, "sessions": []}


def test_lists_sessions_newest_first(client, workspace) -> None:
    _make_session(workspace, "20260101-aaaa", created="2026-01-01T00:00:00+00:00", turns=3)
    _make_session(workspace, "20260427-bbbb", created="2026-04-27T10:00:00+00:00", turns=7)
    _make_session(workspace, "20260315-cccc", created="2026-03-15T12:00:00+00:00", turns=1)

    r = client.get("/sessions")
    assert r.status_code == 200
    sessions = r.json()["sessions"]
    assert [s["session_id"] for s in sessions] == [
        "20260427-bbbb",
        "20260315-cccc",
        "20260101-aaaa",
    ]
    bbbb = sessions[0]
    assert bbbb["turns"] == 7
    assert bbbb["backend"] == "ollama"
    assert bbbb["model"] == "qwen2.5:7b"
    assert bbbb["last_event_ts"] is not None  # mtime of messages.jsonl


def test_limit_param(client, workspace) -> None:
    for i in range(5):
        _make_session(
            workspace, f"2026042{i}-aaaa", created=f"2026-04-2{i}T00:00:00+00:00"
        )
    r = client.get("/sessions?limit=2")
    assert r.status_code == 200
    assert len(r.json()["sessions"]) == 2


def test_limit_out_of_range_rejected(client) -> None:
    assert client.get("/sessions?limit=0").status_code == 422
    assert client.get("/sessions?limit=200").status_code == 422


def test_malformed_meta_tolerated(client, workspace) -> None:
    """A session with broken meta.json should still appear, with empty fields
    and the directory name as session_id."""
    _make_session(
        workspace, "20260427-broken", created="ignored", bad_meta=True, turns=2
    )
    r = client.get("/sessions")
    sessions = r.json()["sessions"]
    assert len(sessions) == 1
    s = sessions[0]
    assert s["session_id"] == "20260427-broken"
    assert s["backend"] == ""
    assert s["model"] == ""
    assert s["turns"] == 2
    assert s["created"] is None


def test_no_messages_file_means_zero_turns(client, workspace) -> None:
    _make_session(workspace, "20260427-empty", created="2026-04-27T00:00:00+00:00")
    r = client.get("/sessions")
    sessions = r.json()["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["turns"] == 0
    assert sessions[0]["last_event_ts"] is None


def test_endpoint_listed_in_schema(client) -> None:
    body = client.get("/api/version").json()
    paths = [(e["method"], e["path"]) for e in body["rest"]]
    assert ("GET", "/sessions") in paths
