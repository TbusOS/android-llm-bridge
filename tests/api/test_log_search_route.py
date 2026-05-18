"""Tests for GET /api/log/search — historical regex search REST."""

from __future__ import annotations

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


def _put_log(workspace: Path, device: str, name: str, body: str) -> Path:
    logs_dir = workspace / "devices" / device / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    fp = logs_dir / name
    fp.write_text(body, encoding="utf-8")
    return fp


def test_search_finds_matches_with_line_numbers(client, workspace) -> None:
    _put_log(
        workspace,
        "abc123",
        "boot.txt",
        "line one\nERROR: foo broke\nline three\nERROR: bar broke\n",
    )
    r = client.get("/api/log/search?pattern=ERROR&device=abc123")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    data = body["data"]
    assert data["match_count"] == 2
    assert data["pattern"] == "ERROR"
    line_numbers = sorted(m["line_number"] for m in data["matches"])
    assert line_numbers == [2, 4]


def test_search_truncates_at_max(client, workspace) -> None:
    _put_log(
        workspace,
        "abc123",
        "wall.txt",
        "\n".join(f"ERROR {i}" for i in range(50)) + "\n",
    )
    r = client.get("/api/log/search?pattern=ERROR&device=abc123&max=10")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["match_count"] == 10
    assert data["truncated"] is True


def test_search_invalid_regex_returns_envelope(client) -> None:
    r = client.get("/api/log/search?pattern=%5B&device=abc123")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "INVALID_FILTER"


def test_search_unsafe_device_400(client) -> None:
    r = client.get("/api/log/search?pattern=anything&device=has%20space")
    assert r.status_code == 400


def test_search_empty_pattern_422(client) -> None:
    r = client.get("/api/log/search?pattern=&device=abc")
    assert r.status_code == 422


def test_search_no_matches_returns_empty(client, workspace) -> None:
    _put_log(workspace, "abc123", "x.txt", "nothing interesting here\n")
    r = client.get("/api/log/search?pattern=ERROR&device=abc123")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["match_count"] == 0
    assert data["matches"] == []
    assert data["truncated"] is False
