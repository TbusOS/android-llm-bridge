"""Tests for GET /api/info/{panel} — per-panel device info (ARCH-2)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from alb.api.server import create_app
from alb.transport.base import ShellResult

_GETPROP = (
    "[ro.boot.verifiedbootstate]: [green]\n"
    "[ro.boot.avb_version]: [1.2]\n"
    "[ro.boot.veritymode]: [enforcing]\n"
    "[ro.crypto.state]: [encrypted]\n"
    "[ro.crypto.type]: [file]\n"
    "[ro.adb.secure]: [1]\n"
)


class _FakeSecTransport:
    name = "adb"

    async def shell(self, cmd: str, *, timeout: int = 30) -> ShellResult:
        if cmd == "getprop":
            return ShellResult(ok=True, stdout=_GETPROP)
        if "getenforce" in cmd:
            return ShellResult(ok=True, stdout="Enforcing\n")
        if "policyvers" in cmd:
            return ShellResult(ok=True, stdout="33\n")
        return ShellResult(ok=True, stdout="")


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "alb.api.info_route.build_transport",
        lambda **_: _FakeSecTransport(),
    )
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_security_panel_returns_fields(client) -> None:
    r = client.get("/api/info/security?device=abc123")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    data = body["data"]
    assert data["verified_boot_state"] == "green"
    assert data["verity_mode"] == "enforcing"
    assert data["selinux_mode"] == "enforcing"
    assert data["selinux_policy_version"] == "33"
    assert data["adb_secure"] is True


def test_unknown_panel_404(client) -> None:
    r = client.get("/api/info/bogus?device=abc123")
    assert r.status_code == 404


def test_unsafe_device_400(client) -> None:
    r = client.get("/api/info/security?device=has%20space")
    assert r.status_code == 400


def test_info_panel_listed_in_schema(client) -> None:
    body = client.get("/api/version").json()
    paths = [e["path"] for e in body["rest"]]
    assert "/api/info/{panel}" in paths
