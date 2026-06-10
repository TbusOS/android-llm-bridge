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


# Meta endpoints intentionally NOT in REST_ENDPOINTS: /api/version is
# documented (it IS the contract); these two are its plumbing aliases.
_SCHEMA_DOC_EXEMPT = {"/api/ping", "/api/schema"}


def test_mounted_routes_are_documented(client) -> None:
    """Reverse parity (AR9-1 第十轮): every mounted route must appear in
    REST_ENDPOINTS / WS_ENDPOINTS / the explicit exempt set.

    The forward direction (documented → mounted) is covered above; this
    direction is what let 17 endpoints from the 5/18 batch ship without
    contract documentation while CI stayed green for 14+ days. The
    schema docstring promises clients can feature-detect via these
    lists, so "mounted but undocumented" is a contract bug.
    """
    import re

    from fastapi.routing import APIRoute, APIWebSocketRoute

    def _norm(path: str) -> str:
        # FastAPI route paths carry converter suffixes ({path:path});
        # the documented contract uses the bare {param} form.
        return re.sub(r"\{([^}:]+):[^}]+\}", r"{\1}", path)

    documented_rest = {e["path"] for e in REST_ENDPOINTS}
    documented_ws = {w["path"] for w in WS_ENDPOINTS}

    for route in client.app.routes:
        if isinstance(route, APIWebSocketRoute):
            assert _norm(route.path) in documented_ws, (
                f"WS {route.path} is mounted but missing from "
                "schema.WS_ENDPOINTS — document it (it IS the contract)"
            )
        elif isinstance(route, APIRoute):
            path = _norm(route.path)
            if path in _SCHEMA_DOC_EXEMPT:
                continue
            assert path in documented_rest, (
                f"{sorted(route.methods)} {path} is mounted but "
                "missing from schema.REST_ENDPOINTS — document it "
                "(it IS the contract)"
            )
        # Mount (static /app UI) and Starlette built-ins (openapi/docs)
        # are not APIRoute/APIWebSocketRoute — skipped implicitly.


def test_schema_lists_consistent_constants() -> None:
    # Sanity: the data that the server would serialize matches the
    # module-level constants.
    assert any(e["path"] == "/health" for e in REST_ENDPOINTS)
    assert any(w["path"] == "/chat/ws" for w in WS_ENDPOINTS)


# ─── MID-8 retroactive verification (functional audit 2026-05-02) ───
# Audit claim: "WS endpoints lack heartbeat — proxy idle-killed
# connections appear hung to user with no ping/pong feedback."
# 2026-05-05 retroactive verify: uvicorn defaults `ws_ping_interval=
# 20.0` and `ws_ping_timeout=20.0` (RFC 6455 control-frame ping/pong),
# and alb-api never overrides these. So every WS connection has a
# 20s heartbeat at the protocol layer — well below typical proxy
# idle-kill thresholds (60-300s). MID-8 was a virtual finding.
# This test locks in the behavior so a future `main()` refactor
# can't silently disable heartbeats by overriding ws_ping_*.


def test_alb_api_main_does_not_override_uvicorn_ws_ping(monkeypatch) -> None:
    """alb-api must rely on uvicorn's default 20s ws_ping_interval +
    ws_ping_timeout. If a future refactor sets `ws_ping_interval=None`
    or `0` we want to fail loudly here, not in a prod incident."""
    captured: dict = {}

    def _fake_run(target: str, **kwargs):
        captured["target"] = target
        captured["kwargs"] = kwargs

    import uvicorn
    monkeypatch.setattr(uvicorn, "run", _fake_run)
    monkeypatch.setenv("ALB_API_HOST", "127.0.0.1")  # silence the 0.0.0.0 banner

    from alb.api.server import main
    main()

    # If ANY ws_ping_* override appears here, uvicorn's defaults are
    # being clobbered. Default = 20.0s for both.
    assert "ws_ping_interval" not in captured["kwargs"], (
        "main() should leave ws_ping_interval at uvicorn default (20s) — "
        f"got {captured['kwargs'].get('ws_ping_interval')!r}"
    )
    assert "ws_ping_timeout" not in captured["kwargs"], (
        "main() should leave ws_ping_timeout at uvicorn default (20s) — "
        f"got {captured['kwargs'].get('ws_ping_timeout')!r}"
    )

    # Defensive: also verify the real uvicorn Config defaults are still
    # 20s — if a future uvicorn upgrade flipped these to None, we'd
    # silently regress. Lock the framework version contract too.
    sig = uvicorn.Config.__init__.__defaults__ or ()
    # Unfortunately Config has many positional defaults; use signature
    # introspection instead:
    import inspect
    params = inspect.signature(uvicorn.Config.__init__).parameters
    assert params["ws_ping_interval"].default == 20.0, (
        f"uvicorn ws_ping_interval default changed to "
        f"{params['ws_ping_interval'].default!r}; revisit MID-8 "
        "verification — alb-api may now need to set the value explicitly."
    )
    assert params["ws_ping_timeout"].default == 20.0, (
        f"uvicorn ws_ping_timeout default changed to "
        f"{params['ws_ping_timeout'].default!r}; revisit MID-8 verification."
    )
