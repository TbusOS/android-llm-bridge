"""Tests for the /app static mount."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alb.api.server import create_app


REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_APP = REPO_ROOT / "docs" / "app"


@pytest.mark.skipif(
    not (DOCS_APP / "index.html").is_file(),
    reason="docs/app not built — run `cd web && npm run build` first",
)
def test_ui_mount_serves_index() -> None:
    app = create_app()
    with TestClient(app) as c:
        r = c.get("/app/")
        assert r.status_code == 200
        # StaticFiles with html=True auto-serves index.html for / paths.
        assert b"<html" in r.content.lower() or b"<!doctype html" in r.content.lower()


def test_api_paths_still_work_regardless_of_mount() -> None:
    # /api/version must keep working whether docs/app exists or not.
    app = create_app()
    with TestClient(app) as c:
        r = c.get("/api/version")
        assert r.status_code == 200
        assert r.json()["version"]


@pytest.mark.skipif(
    not (DOCS_APP / "index.html").is_file(),
    reason="docs/app not built — run `cd web && npm run build` first",
)
def test_spa_fallback_serves_index_for_unknown_route() -> None:
    """Deep-link / refresh on TanStack Router routes must hit index.html
    so the client-side router can resolve. DEBT-014 fix."""
    app = create_app()
    with TestClient(app) as c:
        for route in ("/app/dashboard", "/app/inspect", "/app/sessions/abc"):
            r = c.get(route)
            assert r.status_code == 200, f"{route} returned {r.status_code}"
            body = r.content.lower()
            assert b"<html" in body or b"<!doctype html" in body, (
                f"{route} did not serve HTML shell"
            )


@pytest.mark.skipif(
    not (DOCS_APP / "index.html").is_file(),
    reason="docs/app not built — run `cd web && npm run build` first",
)
def test_spa_fallback_does_not_mask_missing_asset() -> None:
    """A 404 on `/app/assets/missing.js` is a real bug (broken asset
    URL); the SPA fallback must NOT silently rewrite to HTML or it
    becomes a "white page" debugging nightmare."""
    app = create_app()
    with TestClient(app) as c:
        r = c.get("/app/assets/this-file-does-not-exist.js")
        assert r.status_code == 404
        # Same for any extensioned tail.
        r2 = c.get("/app/missing.css")
        assert r2.status_code == 404
        # Multi-segment extension (sourcemap, archive name) — also 404.
        r3 = c.get("/app/foo.bar.baz")
        assert r3.status_code == 404


@pytest.mark.skipif(
    not (DOCS_APP / "index.html").is_file(),
    reason="docs/app not built — run `cd web && npm run build` first",
)
def test_spa_fallback_handles_trailing_slash_and_query() -> None:
    """Edge cases: trailing slash + querystring on SPA routes still
    resolve to index.html so refresh / shared URLs with `?tab=charts`
    survive."""
    app = create_app()
    with TestClient(app) as c:
        # Trailing slash — `path.rsplit("/", 1)[-1]` is "", no dot,
        # falls through to index.html (correct).
        r = c.get("/app/dashboard/")
        assert r.status_code == 200
        assert b"<html" in r.content.lower() or b"<!doctype html" in r.content.lower()
        # Querystring is stripped from the path before routing — the
        # SPA shell still loads, client-side router reads search.
        r2 = c.get("/app/inspect?tab=charts")
        assert r2.status_code == 200
        assert b"<html" in r2.content.lower() or b"<!doctype html" in r2.content.lower()
