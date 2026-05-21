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


def test_search_returns_pattern_timeout_envelope(
    client, workspace, monkeypatch,
) -> None:
    """Security HIGH#8: when the scanner reports ``timed_out=True`` (or
    the outer ``asyncio.wait_for`` fires), the route returns a
    ``PATTERN_TIMEOUT`` envelope rather than hanging the request.

    Forces the deadline branch by stubbing ``_scan_files_for_pattern``
    to return ``(matches=[], truncated=False, timed_out=True)`` —
    avoids depending on real wall-clock timing inside the unit suite.
    A separate slow integration test exercises the real ``asyncio.
    wait_for`` cancellation path against a true ReDoS payload."""
    _put_log(workspace, "abc123", "x.txt", "anything\n")

    def _stub_scan(*_a, **_kw):
        return [], False, True  # matches, truncated, timed_out

    monkeypatch.setattr(
        "alb.capabilities.logging._scan_files_for_pattern", _stub_scan
    )
    r = client.get("/api/log/search?pattern=anything&device=abc123")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "PATTERN_TIMEOUT"
    assert body["error"]["category"] == "timeout"


def test_search_outer_wait_for_timeout_returns_envelope(
    client, workspace, monkeypatch,
) -> None:
    """Belt-and-suspenders: if ``asyncio.wait_for`` itself fires (the
    inner deadline check missed because of a single hanging
    ``re.search`` C-call), the route still returns PATTERN_TIMEOUT
    rather than 500."""
    import asyncio

    _put_log(workspace, "abc123", "x.txt", "anything\n")

    async def _hang(*_a, **_kw):
        # Simulate a stuck C-call: never returns.  asyncio.wait_for
        # raises TimeoutError after _SEARCH_TIMEOUT_S + 0.5s slack.
        await asyncio.sleep(60)
        return [], False, False

    # Make the search loop hang inside to_thread so wait_for trips.
    monkeypatch.setattr(
        "alb.capabilities.logging.asyncio.to_thread",
        lambda fn, *a, **kw: _hang(),
    )
    monkeypatch.setattr(
        "alb.capabilities.logging._SEARCH_TIMEOUT_S", 0.05,
    )
    r = client.get("/api/log/search?pattern=anything&device=abc123")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "PATTERN_TIMEOUT"
    assert body["error"]["category"] == "timeout"
