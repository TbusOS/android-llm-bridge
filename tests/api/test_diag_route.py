"""Tests for /api/diag/* — bugreport / anr / tombstone + artifacts list."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from alb.api.server import create_app
from alb.capabilities.diagnose import BugreportResult, PullBundleResult
from alb.infra.result import fail, ok
from alb.transport.base import PermissionResult


class _StubTransport:
    name = "adb"

    def __init__(self) -> None:
        self.shell = AsyncMock()
        self.pull = AsyncMock()
        self.check_permissions = AsyncMock(
            return_value=PermissionResult(behavior="allow")
        )


@pytest.fixture
def stub_transport():
    return _StubTransport()


@pytest.fixture
def workspace(monkeypatch, tmp_path) -> Path:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    return tmp_path


@pytest.fixture
def client(monkeypatch, stub_transport, workspace):
    monkeypatch.setattr(
        "alb.api.diag_route.build_transport", lambda **_kw: stub_transport
    )
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_bugreport_happy_path(monkeypatch, client, workspace) -> None:
    async def _br(_t, **_kw):
        return ok(
            data=BugreportResult(
                zip_path=str(workspace / "br.zip"),
                txt_path=None,
                duration_ms=12000,
            ),
            timing_ms=12000,
        )

    monkeypatch.setattr("alb.api.diag_route.diag_cap.bugreport", _br)
    r = client.post("/api/diag/bugreport?device=abc")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["zip_path"].endswith("br.zip")
    assert body["data"]["duration_ms"] == 12000


def test_bugreport_failure_envelope(monkeypatch, client) -> None:
    async def _br(_t, **_kw):
        return fail(
            code="BUGREPORT_FAILED",
            message="device offline",
            suggestion="check adb",
            category="transport",
        )

    monkeypatch.setattr("alb.api.diag_route.diag_cap.bugreport", _br)
    r = client.post("/api/diag/bugreport?device=abc")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "BUGREPORT_FAILED"


def test_anr_pull_passes_clear_after_flag(monkeypatch, client) -> None:
    received: dict = {}

    async def _anr(_t, *, clear_after, device):
        received["clear_after"] = clear_after
        received["device"] = device
        return ok(
            data=PullBundleResult(kind="anr", count=2, files=["/tmp/a", "/tmp/b"]),
            timing_ms=50,
        )

    monkeypatch.setattr("alb.api.diag_route.diag_cap.anr_pull", _anr)
    r = client.post("/api/diag/anr?device=abc", json={"clear_after": True})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["count"] == 2
    assert received["clear_after"] is True
    assert received["device"] == "abc"


def test_tombstone_limit_validates(client) -> None:
    r = client.post("/api/diag/tombstone?device=abc", json={"limit": 99999})
    assert r.status_code == 422


def test_tombstone_happy(monkeypatch, client) -> None:
    async def _tomb(_t, *, limit, device):
        return ok(
            data=PullBundleResult(
                kind="tombstones",
                count=limit,
                files=[f"/tmp/tomb_{i}" for i in range(limit)],
            ),
            timing_ms=80,
        )

    monkeypatch.setattr("alb.api.diag_route.diag_cap.tombstone_pull", _tomb)
    r = client.post("/api/diag/tombstone?device=abc", json={"limit": 3})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["count"] == 3
    assert len(body["data"]["files"]) == 3


def test_bad_device_400(client) -> None:
    r = client.post("/api/diag/bugreport?device=../etc")
    assert r.status_code == 400


def test_bad_device_detail_does_not_echo_input(client) -> None:
    """5/25 sec audit MID-1 regression: HTTPException detail must not
    echo the rejected serial back (control chars / unicode RTL /
    quotes pre-`is_safe_device` could become reflected-XSS via any
    future consumer rendering detail unsafely)."""
    sneaky = "<script>alert(1)</script>"
    r = client.post(f"/api/diag/bugreport?device={sneaky}")
    assert r.status_code == 400
    body = r.json()
    assert body["detail"] == "invalid device serial"
    assert sneaky not in body["detail"]

    r2 = client.get(f"/api/diag/artifacts?device={sneaky}")
    assert r2.status_code == 400
    body2 = r2.json()
    assert body2["detail"] == "invalid device serial"
    assert sneaky not in body2["detail"]


def test_artifacts_lists_existing_bundles(client, workspace) -> None:
    """Pre-populate workspace with bugreport + anr bundle + tombstone bundle,
    then verify the listing endpoint enumerates them."""
    br_dir = workspace / "devices" / "abc" / "bugreports"
    br_dir.mkdir(parents=True)
    (br_dir / "2026-05-18-bugreport.zip").write_bytes(b"zip")

    anr_dir = workspace / "devices" / "abc" / "anr" / "2026-05-18T10"
    anr_dir.mkdir(parents=True)
    (anr_dir / "trace_a.txt").write_text("anr a")
    (anr_dir / "trace_b.txt").write_text("anr b")

    tomb_dir = workspace / "devices" / "abc" / "tombstones" / "2026-05-18T11"
    tomb_dir.mkdir(parents=True)
    (tomb_dir / "tombstone_00").write_text("tomb")

    r = client.get("/api/diag/artifacts?device=abc")
    assert r.status_code == 200
    data = r.json()["data"]
    assert len(data["bugreports"]) == 1
    assert data["bugreports"][0]["name"] == "2026-05-18-bugreport.zip"
    # `path` must be workspace-relative (POSIX) so the frontend can
    # build /workspace/files/download/<path> anchors. Absolute server
    # paths would leak filesystem layout and break the download URL.
    assert data["bugreports"][0]["path"] == (
        "devices/abc/bugreports/2026-05-18-bugreport.zip"
    )
    assert len(data["anr"]) == 1
    assert data["anr"][0]["count"] == 2
    assert data["anr"][0]["path"] == "devices/abc/anr/2026-05-18T10"
    assert data["anr"][0]["files"][0]["path"].startswith(
        "devices/abc/anr/2026-05-18T10/trace_"
    )
    assert len(data["tombstones"]) == 1
    assert data["tombstones"][0]["count"] == 1


def test_artifacts_empty_when_no_workspace_dir(client) -> None:
    r = client.get("/api/diag/artifacts?device=neverexisted")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["bugreports"] == []
    assert data["anr"] == []
    assert data["tombstones"] == []


def test_transport_init_failure_returns_envelope_b_not_503(monkeypatch, tmp_path) -> None:
    """Architecture ADR (REST envelope 三态约定 b): build_transport
    failure → 200 + ok=false envelope, NOT HTTPException(503)."""
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))

    def _boom(**_kw: Any):
        raise RuntimeError("adb server unreachable")

    monkeypatch.setattr("alb.api.diag_route.build_transport", _boom)
    app = create_app()
    with TestClient(app) as c:
        r = c.post("/api/diag/bugreport?device=abc")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["transport"] is None
    assert body["error"]["code"] == "TRANSPORT_INIT_FAILED"
    assert body["device"] == "abc"
