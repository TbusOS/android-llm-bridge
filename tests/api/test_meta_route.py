"""Tests for /api/version and /api/ping."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from alb.api.schema import API_VERSION, REST_ENDPOINTS, WS_ENDPOINTS
from alb.api.server import create_app


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_api_version_shape(client) -> None:
    r = client.get("/api/version")
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == API_VERSION
    assert "alb_version" in body
    assert isinstance(body["rest"], list)
    assert isinstance(body["ws"], list)
    assert body["reference"].endswith(".md")


def test_api_version_lists_expected_endpoints(client) -> None:
    body = client.get("/api/version").json()
    paths = [e["path"] for e in body["rest"]]
    # Critical endpoints the UI depends on
    for expected in ("/health", "/chat", "/playground/chat", "/api/version"):
        assert expected in paths, f"/api/version is missing {expected}"

    ws_paths = [w["path"] for w in body["ws"]]
    for expected in ("/chat/ws", "/playground/chat/ws", "/metrics/stream", "/terminal/ws"):
        assert expected in ws_paths


def test_api_version_ws_messages_documented(client) -> None:
    body = client.get("/api/version").json()
    for ws in body["ws"]:
        assert ws["messages"], f"{ws['path']} has no message documentation"


def test_api_ping(client) -> None:
    r = client.get("/api/ping")
    assert r.status_code == 200
    assert r.json()["v"] == API_VERSION


def test_api_schema_alias(client) -> None:
    # /api/schema should return the same as /api/version
    a = client.get("/api/version").json()
    b = client.get("/api/schema").json()
    assert a == b


def test_schema_endpoints_match_registered_routes(client) -> None:
    # Any REST endpoint documented in the schema should actually respond
    # (rules out typos in schema.py).
    body = client.get("/api/version").json()
    for e in body["rest"]:
        if e["method"] != "GET":
            continue
        if "{" in e["path"]:
            continue  # path-param endpoints need arguments
        r = client.get(e["path"])
        # Either 200 or 502 (Ollama unreachable in tests) is acceptable;
        # a 404 means the endpoint is documented but not mounted.
        assert r.status_code != 404, f"{e['path']} is in schema but returns 404"


def test_schema_lists_consistent_constants() -> None:
    # Sanity: the data that the server would serialize matches the
    # module-level constants.
    assert any(e["path"] == "/health" for e in REST_ENDPOINTS)
    assert any(w["path"] == "/chat/ws" for w in WS_ENDPOINTS)
