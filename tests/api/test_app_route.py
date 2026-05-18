"""Tests for /api/app/* — install / uninstall / start / stop / list / info / clear-data."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from alb.api.server import create_app
from alb.capabilities.app import AppInfo, AppListResult
from alb.infra.result import fail, ok
from alb.transport.base import PermissionResult


class _StubTransport:
    name = "adb"

    def __init__(self) -> None:
        self.shell = AsyncMock()
        self.push = AsyncMock()
        self.check_permissions = AsyncMock(
            return_value=PermissionResult(behavior="allow")
        )


@pytest.fixture
def stub_transport():
    return _StubTransport()


@pytest.fixture
def client(monkeypatch, stub_transport, tmp_path):
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(
        "alb.api.app_route.build_transport", lambda **_kw: stub_transport
    )
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_list_returns_packages(monkeypatch, client) -> None:
    async def _list(_t, **_kw):
        return ok(
            data=AppListResult(
                packages=["com.example.a", "com.example.b"],
            ),
            timing_ms=10,
        )

    monkeypatch.setattr("alb.api.app_route.app_cap.list_apps", _list)
    r = client.get("/api/app/list?device=abc")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["count"] == 2
    assert "com.example.a" in body["data"]["packages"]


def test_list_filter_propagates(monkeypatch, client) -> None:
    received: dict[str, Any] = {}

    async def _list(_t, *, filter, include_system):
        received["filter"] = filter
        received["include_system"] = include_system
        return ok(data=AppListResult(packages=[]), timing_ms=1)

    monkeypatch.setattr("alb.api.app_route.app_cap.list_apps", _list)
    client.get("/api/app/list?device=abc&filter=chrome&include_system=true")
    assert received == {"filter": "chrome", "include_system": True}


def test_info_returns_app_info(monkeypatch, client) -> None:
    async def _info(_t, package):
        return ok(
            data=AppInfo(
                package=package,
                version_name="1.2.3",
                version_code="42",
                first_install_time="2026-01-01",
                last_update_time="2026-05-01",
                requested_permissions=["INTERNET", "CAMERA"],
            ),
            timing_ms=5,
        )

    monkeypatch.setattr("alb.api.app_route.app_cap.info", _info)
    r = client.get("/api/app/info?device=abc&package=com.example.a")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["version_name"] == "1.2.3"
    assert "INTERNET" in body["data"]["requested_permissions"]


def test_start_invokes_capability(monkeypatch, client) -> None:
    received: dict[str, Any] = {}

    async def _start(_t, component):
        received["component"] = component
        return ok(data={"started": component}, timing_ms=2)

    monkeypatch.setattr("alb.api.app_route.app_cap.start", _start)
    r = client.post(
        "/api/app/start?device=abc", json={"component": "com.example.a"}
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert received["component"] == "com.example.a"


def test_stop_invokes_capability(monkeypatch, client) -> None:
    received: dict[str, Any] = {}

    async def _stop(_t, package):
        received["package"] = package
        return ok(data={"stopped": package}, timing_ms=2)

    monkeypatch.setattr("alb.api.app_route.app_cap.stop", _stop)
    r = client.post(
        "/api/app/stop?device=abc", json={"package": "com.example.a"}
    )
    assert r.status_code == 200
    assert received["package"] == "com.example.a"


def test_clear_data_invokes_capability(monkeypatch, client) -> None:
    received: dict[str, Any] = {}

    async def _clear(_t, package):
        received["package"] = package
        return ok(data={"cleared": package}, timing_ms=2)

    monkeypatch.setattr("alb.api.app_route.app_cap.clear_data", _clear)
    r = client.post(
        "/api/app/clear-data?device=abc", json={"package": "com.example.a"}
    )
    assert r.status_code == 200


def test_uninstall_passes_flags(monkeypatch, client) -> None:
    received: dict[str, Any] = {}

    async def _u(_t, package, *, keep_data, allow_dangerous):
        received.update(
            package=package, keep_data=keep_data, allow_dangerous=allow_dangerous
        )
        return ok(data={"uninstalled": package}, timing_ms=2)

    monkeypatch.setattr("alb.api.app_route.app_cap.uninstall", _u)
    r = client.post(
        "/api/app/uninstall?device=abc",
        json={
            "package": "com.example.a",
            "keep_data": True,
            "allow_dangerous": True,
        },
    )
    assert r.status_code == 200
    assert received["keep_data"] is True
    assert received["allow_dangerous"] is True


def test_uninstall_invalid_package_returns_envelope(monkeypatch, client) -> None:
    async def _u(_t, package, **_kw):
        return fail(
            code="PACKAGE_NAME_INVALID",
            message="bad name",
            suggestion="use com.x",
            category="input",
        )

    monkeypatch.setattr("alb.api.app_route.app_cap.uninstall", _u)
    r = client.post(
        "/api/app/uninstall?device=abc",
        json={"package": "not-a-package"},
    )
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "PACKAGE_NAME_INVALID"


def test_bad_device_400(client) -> None:
    r = client.get("/api/app/list?device=../etc")
    assert r.status_code == 400


def test_install_streams_apk_to_tempfile(monkeypatch, client, tmp_path: Path) -> None:
    """The route writes the multipart upload to a tempfile then hands
    that path to ``app_cap.install``. Verify the tempfile exists at the
    moment install is called and contains the uploaded bytes."""
    apk_bytes = b"PK\x03\x04" + b"x" * 100  # zip-magic + payload
    seen_path: dict[str, Path] = {}

    async def _install(_t, apk_path, *, replace, grant_runtime, downgrade):
        # Snapshot the tmp file while the route still holds it.
        seen_path["path"] = Path(apk_path)
        seen_path["bytes"] = Path(apk_path).read_bytes()
        seen_path["flags"] = (replace, grant_runtime, downgrade)
        return ok(data={"installed": "ok"}, timing_ms=10)

    monkeypatch.setattr("alb.api.app_route.app_cap.install", _install)

    r = client.post(
        "/api/app/install?device=abc&replace=false&grant_runtime=true",
        files={"apk": ("payload.apk", apk_bytes, "application/vnd.android.package-archive")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert seen_path["bytes"] == apk_bytes
    assert seen_path["flags"] == (False, True, False)
    # Tempfile is unlinked in finally.
    assert not seen_path["path"].exists()


def test_install_rejects_non_apk_filename(client) -> None:
    r = client.post(
        "/api/app/install?device=abc",
        files={"apk": ("evil.exe", b"x", "application/octet-stream")},
    )
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "INVALID_FILENAME"
