"""Tests for GET /devices."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from alb.api.server import create_app
from alb.transport.adb import AdbDevice
from alb.transport.base import ShellResult, Transport


class _FakeAdbTransport(Transport):
    """Stub with a real-ish devices() that returns two AdbDevice rows."""

    name = "adb"

    def __init__(self, devs: list[AdbDevice] | None = None) -> None:
        self._devs = devs if devs is not None else [
            AdbDevice(serial="emu-5554", state="device", product="sdk_phone", model="Pixel"),
            AdbDevice(serial="aaaabbbb", state="offline"),
        ]

    async def devices(self) -> list[AdbDevice]:
        return list(self._devs)

    async def shell(self, cmd: str, *, timeout: int = 30) -> ShellResult:
        return ShellResult(ok=True)

    async def stream_read(self, source: str, **kwargs: Any):  # noqa: ANN001
        if False:
            yield b""

    async def push(self, local, remote):  # noqa: ANN001
        return ShellResult(ok=True)

    async def pull(self, remote, local):  # noqa: ANN001
        return ShellResult(ok=True)

    async def reboot(self, mode: str = "normal") -> ShellResult:
        return ShellResult(ok=True)

    async def health(self) -> dict[str, Any]:
        return {"ok": True}


class _NoDevicesTransport(Transport):
    """Simulates ssh / serial — Transport without a .devices() method."""

    name = "ssh"

    async def shell(self, cmd: str, *, timeout: int = 30) -> ShellResult:
        return ShellResult(ok=True)

    async def stream_read(self, source: str, **kwargs: Any):  # noqa: ANN001
        if False:
            yield b""

    async def push(self, local, remote):  # noqa: ANN001
        return ShellResult(ok=True)

    async def pull(self, remote, local):  # noqa: ANN001
        return ShellResult(ok=True)

    async def reboot(self, mode: str = "normal") -> ShellResult:
        return ShellResult(ok=True)

    async def health(self) -> dict[str, Any]:
        return {"ok": True}


class _ExplodingDevicesTransport(_FakeAdbTransport):
    async def devices(self) -> list[AdbDevice]:
        raise RuntimeError("adb server not reachable")


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "alb.api.devices_route.build_transport",
        lambda **kwargs: _FakeAdbTransport(),
    )
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_lists_two_devices(client) -> None:
    r = client.get("/devices")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["transport"] == "_FakeAdbTransport"
    assert [d["serial"] for d in body["devices"]] == ["emu-5554", "aaaabbbb"]
    first = body["devices"][0]
    assert first["state"] == "device"
    assert first["product"] == "sdk_phone"
    assert first["model"] == "Pixel"


def test_transport_without_devices_method_returns_empty(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "alb.api.devices_route.build_transport",
        lambda **kwargs: _NoDevicesTransport(),
    )
    app = create_app()
    with TestClient(app) as c:
        body = c.get("/devices").json()
    assert body["ok"] is True
    assert body["transport"] == "_NoDevicesTransport"
    assert body["devices"] == []


def test_devices_call_failure_reported_inline(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "alb.api.devices_route.build_transport",
        lambda **kwargs: _ExplodingDevicesTransport(),
    )
    app = create_app()
    with TestClient(app) as c:
        r = c.get("/devices")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["devices"] == []
    assert "RuntimeError" in body["error"]
    assert "adb server not reachable" in body["error"]


def test_build_transport_failure_reported_inline(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    def _boom(**kwargs: Any):
        raise ValueError("Unknown transport: xyz")

    monkeypatch.setattr("alb.api.devices_route.build_transport", _boom)
    app = create_app()
    with TestClient(app) as c:
        r = c.get("/devices")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["transport"] is None
    assert "ValueError" in body["error"]


def test_endpoint_listed_in_schema(client) -> None:
    body = client.get("/api/version").json()
    paths = [(e["method"], e["path"]) for e in body["rest"]]
    assert ("GET", "/devices") in paths
