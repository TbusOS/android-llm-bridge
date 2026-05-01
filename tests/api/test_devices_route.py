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


# ─── /devices/{serial}/details (DEBT-022 PR-A) ─────────────────────
class _DevinfoTransport(_FakeAdbTransport):
    """Returns a realistic getprop / proc / wm / thermal command tree."""

    async def shell(self, cmd: str, *, timeout: int = 30) -> ShellResult:
        if cmd.startswith("getprop"):
            return ShellResult(
                ok=True,
                exit_code=0,
                stdout=(
                    "[ro.product.model]: [Pixel 7]\n"
                    "[ro.product.brand]: [google]\n"
                    "[ro.boot.soc.product]: [Tensor G2]\n"
                    "[ro.product.cpu.abi]: [arm64-v8a]\n"
                    "[ro.serialno]: [TEST123]\n"
                ),
                stderr="",
                duration_ms=1,
            )
        if cmd.startswith("dumpsys battery"):
            return ShellResult(ok=True, exit_code=0,
                               stdout="  level: 75\n", stderr="", duration_ms=1)
        if cmd.startswith("cat /proc/uptime"):
            return ShellResult(ok=True, exit_code=0, stdout="9999.5 1.0\n",
                               stderr="", duration_ms=1)
        if cmd.startswith("df /data"):
            return ShellResult(ok=True, exit_code=0,
                               stdout="Filesystem 1K Used Avail Use% Mounted\n"
                                      "/dev/x 1000 500 500 50% /data\n",
                               stderr="", duration_ms=1)
        if cmd.startswith("cat /proc/cpuinfo"):
            return ShellResult(ok=True, exit_code=0,
                               stdout="processor\t: 0\n\nprocessor\t: 1\n\n",
                               stderr="", duration_ms=1)
        if cmd.startswith("cat /proc/meminfo"):
            return ShellResult(ok=True, exit_code=0,
                               stdout="MemTotal:    7929164 kB\n"
                                      "MemAvailable:  5500000 kB\n",
                               stderr="", duration_ms=1)
        if cmd.startswith("wm size"):
            return ShellResult(ok=True, exit_code=0,
                               stdout="Physical size: 1080x2400\n",
                               stderr="", duration_ms=1)
        if cmd.startswith("wm density"):
            return ShellResult(ok=True, exit_code=0,
                               stdout="Physical density: 420\n",
                               stderr="", duration_ms=1)
        if cmd.startswith("cat /sys/class/thermal"):
            return ShellResult(ok=True, exit_code=0, stdout="47350\n",
                               stderr="", duration_ms=1)
        if cmd.startswith("cat /sys/devices/system/cpu"):
            return ShellResult(ok=True, exit_code=0, stdout="2802000\n",
                               stderr="", duration_ms=1)
        return ShellResult(ok=False, exit_code=1, stderr="unhandled",
                           duration_ms=0, error_code="ADB_COMMAND_FAILED")


def test_device_details_happy(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "alb.api.devices_route.build_transport",
        lambda **kwargs: _DevinfoTransport(),
    )
    app = create_app()
    with TestClient(app) as c:
        r = c.get("/devices/TEST123/details")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["serial"] == "TEST123"
    assert body["transport"] == "_DevinfoTransport"
    dev = body["device"]
    assert dev["model"] == "Pixel 7"
    assert dev["abi"] == "arm64-v8a"
    assert dev["battery_level"] == 75
    assert dev["uptime_sec"] == 9999
    assert dev["extras"]["soc"] == "Tensor G2"
    assert dev["extras"]["cpu_cores"] == 2
    assert dev["extras"]["cpu_max_khz"] == 2802000
    assert dev["extras"]["ram_total_kb"] == 7929164
    assert dev["extras"]["display"]["size"] == "1080x2400"
    assert dev["extras"]["temp_c"] == pytest.approx(47.35)


def test_device_details_build_transport_failure(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    def _boom(**kwargs: Any):
        raise ValueError("no transport configured")

    monkeypatch.setattr("alb.api.devices_route.build_transport", _boom)
    app = create_app()
    with TestClient(app) as c:
        r = c.get("/devices/TEST/details")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["device"] is None
    assert "ValueError" in body["error"]


def test_device_details_devinfo_failure(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    class _BadShellTransport(_FakeAdbTransport):
        async def shell(self, cmd: str, *, timeout: int = 30) -> ShellResult:
            # getprop fails → devinfo returns ok=False
            return ShellResult(ok=False, exit_code=1, stderr="permission denied",
                               duration_ms=0, error_code="ADB_COMMAND_FAILED")

    monkeypatch.setattr(
        "alb.api.devices_route.build_transport",
        lambda **kwargs: _BadShellTransport(),
    )
    app = create_app()
    with TestClient(app) as c:
        r = c.get("/devices/TEST/details")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["device"] is None
    assert body["error"]
