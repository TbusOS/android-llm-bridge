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
