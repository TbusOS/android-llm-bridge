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


# ─── /devices/{serial}/system (DEBT-022 PR-B) ──────────────────────
class _SystemTransport(_FakeAdbTransport):
    """Realistic shell command tree for device_system()."""

    async def shell(self, cmd: str, *, timeout: int = 30) -> ShellResult:
        if cmd.startswith("getprop"):
            return ShellResult(
                ok=True,
                exit_code=0,
                stdout=(
                    "[ro.product.model]: [Pixel 7]\n"
                    "[ro.boot.soc.product]: [Tensor G2]\n"
                ),
                stderr="",
                duration_ms=1,
            )
        if cmd.startswith("ls -la /dev/block/by-name"):
            return ShellResult(
                ok=True,
                exit_code=0,
                stdout=(
                    "lrwxrwxrwx 1 root root 21 Jan 1 boot -> /dev/block/mmcblk0p20\n"
                    "lrwxrwxrwx 1 root root 21 Jan 1 system -> /dev/block/mmcblk0p21\n"
                ),
                stderr="",
                duration_ms=1,
            )
        if cmd.startswith("cat /proc/mounts"):
            return ShellResult(
                ok=True,
                exit_code=0,
                stdout=(
                    "/dev/block/mmcblk0p20 / ext4 ro,seclabel 0 0\n"
                    "tmpfs /dev tmpfs rw,seclabel 0 0\n"
                ),
                stderr="",
                duration_ms=1,
            )
        if cmd.startswith("cat /proc/partitions"):
            return ShellResult(
                ok=True,
                exit_code=0,
                stdout=(
                    "major minor  #blocks  name\n"
                    " 179        0  61071360 mmcblk0\n"
                    " 179       20    65536 mmcblk0p20\n"
                ),
                stderr="",
                duration_ms=1,
            )
        if cmd.startswith("cat /proc/meminfo"):
            return ShellResult(
                ok=True, exit_code=0,
                stdout="MemTotal:    7929164 kB\nMemFree:    3000000 kB\n",
                stderr="", duration_ms=1,
            )
        if cmd.startswith("df /data"):
            return ShellResult(
                ok=True, exit_code=0,
                stdout=(
                    "Filesystem 1K-blocks Used Avail Use% Mounted\n"
                    "/dev/x 1000000 500000 500000 50% /data\n"
                ),
                stderr="", duration_ms=1,
            )
        if cmd.startswith("ip -o addr"):
            return ShellResult(
                ok=True, exit_code=0,
                stdout=(
                    "2: wlan0    inet 192.168.1.10/24 brd 192.168.1.255 scope global\n"
                    "2: wlan0    inet6 fe80::1/64 scope link\n"
                ),
                stderr="", duration_ms=1,
            )
        if cmd.startswith("dumpsys battery"):
            return ShellResult(
                ok=True, exit_code=0,
                stdout=(
                    "Current Battery Service state:\n"
                    "  level: 75\n"
                    "  scale: 100\n"
                    "  status: 2\n"
                ),
                stderr="", duration_ms=1,
            )
        if cmd.startswith("for d in /sys/class/thermal"):
            return ShellResult(
                ok=True, exit_code=0,
                stdout="thermal_zone0|cpu|47350\nthermal_zone1|battery|31200\n",
                stderr="", duration_ms=1,
            )
        return ShellResult(ok=False, exit_code=1, stderr="unhandled",
                           duration_ms=0, error_code="ADB_COMMAND_FAILED")


def test_device_system_happy(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "alb.api.devices_route.build_transport",
        lambda **kwargs: _SystemTransport(),
    )
    app = create_app()
    with TestClient(app) as c:
        r = c.get("/devices/TEST/system")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    sys = body["system"]
    assert sys["props"]["ro.boot.soc.product"] == "Tensor G2"
    assert any(p["name"] == "boot" for p in sys["partitions"])
    assert any(m["mount_point"] == "/" for m in sys["mounts"])
    assert any(b["name"] == "mmcblk0p20" for b in sys["block_devices"])
    assert sys["meminfo"]["MemTotal"] == 7929164
    assert "/data" in sys["storage"]
    assert any(n["iface"] == "wlan0" and n.get("ipv4") for n in sys["network"])
    assert sys["battery"]["level"] == "75"
    assert any(t["zone"] == "thermal_zone0" and t["temp_c"] == "47.4" for t in sys["thermal"])


def test_device_system_partial_failure_still_returns_data(monkeypatch, tmp_path) -> None:
    """If one collector fails, others still populate."""
    monkeypatch.chdir(tmp_path)

    class _MostFailTransport(_FakeAdbTransport):
        async def shell(self, cmd: str, *, timeout: int = 30) -> ShellResult:
            if cmd.startswith("getprop"):
                return ShellResult(ok=True, exit_code=0,
                                   stdout="[ro.product.model]: [Test]\n",
                                   stderr="", duration_ms=1)
            return ShellResult(ok=False, exit_code=1, stderr="",
                               duration_ms=0, error_code="ADB_COMMAND_FAILED")

    monkeypatch.setattr("alb.api.devices_route.build_transport",
                        lambda **kwargs: _MostFailTransport())
    app = create_app()
    with TestClient(app) as c:
        body = c.get("/devices/TEST/system").json()
    assert body["ok"] is True
    assert body["system"]["props"]["ro.product.model"] == "Test"
    assert body["system"]["partitions"] == []
    assert body["system"]["meminfo"] == {}


def test_device_system_getprop_failure_returns_inline(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    class _NoPropsTransport(_FakeAdbTransport):
        async def shell(self, cmd: str, *, timeout: int = 30) -> ShellResult:
            return ShellResult(ok=False, exit_code=1, stderr="device offline",
                               duration_ms=0, error_code="ADB_COMMAND_FAILED")

    monkeypatch.setattr("alb.api.devices_route.build_transport",
                        lambda **kwargs: _NoPropsTransport())
    app = create_app()
    with TestClient(app) as c:
        body = c.get("/devices/TEST/system").json()
    assert body["ok"] is False
    assert body["system"] is None
    assert body["error"]


def test_device_system_endpoint_listed_in_schema(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("alb.api.devices_route.build_transport",
                        lambda **kwargs: _SystemTransport())
    app = create_app()
    with TestClient(app) as c:
        body = c.get("/api/version").json()
    paths = [(e["method"], e["path"]) for e in body["rest"]]
    assert ("GET", "/devices/{serial}/system") in paths


# ─── /devices/{serial}/screenshot + /ui-dump (PR-G) ────────────────
def test_device_screenshot_happy(monkeypatch, tmp_path) -> None:
    """Endpoint reads the PNG file from disk and inlines it as base64."""
    from alb.capabilities.ui import ScreenshotData
    from alb.infra.result import ok as ok_result

    png_file = tmp_path / "snap.png"
    png_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"FAKEDATA" * 4)

    async def fake_screenshot(transport, *, device=None, **kwargs):
        return ok_result(
            data=ScreenshotData(
                path=str(png_file),
                device_path="/sdcard/alb-screenshot-x.png",
                size_bytes=png_file.stat().st_size,
                width=1080,
                height=2400,
            )
        )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("alb.api.devices_route.screenshot", fake_screenshot)
    monkeypatch.setattr(
        "alb.api.devices_route.build_transport",
        lambda **_: _FakeAdbTransport(),
    )
    app = create_app()
    with TestClient(app) as c:
        body = c.post("/devices/TEST/screenshot").json()
    assert body["ok"] is True
    s = body["screenshot"]
    assert s["filename"] == "snap.png"
    assert s["width"] == 1080
    assert s["height"] == 2400
    assert s["png_base64"]
    # Base64 round-trip yields the original bytes back.
    import base64 as b64
    assert b64.b64decode(s["png_base64"]).startswith(b"\x89PNG")


def test_device_screenshot_capability_failure_inline(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    from alb.infra.result import fail as fail_result

    async def fake_screenshot(transport, **kwargs):
        return fail_result(code="SCREENCAP_FAILED", message="device asleep")

    monkeypatch.setattr("alb.api.devices_route.screenshot", fake_screenshot)
    monkeypatch.setattr(
        "alb.api.devices_route.build_transport",
        lambda **_: _FakeAdbTransport(),
    )
    app = create_app()
    with TestClient(app) as c:
        body = c.post("/devices/TEST/screenshot").json()
    assert body["ok"] is False
    assert body["screenshot"] is None
    assert "device asleep" in body["error"]


def test_device_screenshot_build_transport_failure(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    def _boom(**_):
        raise RuntimeError("no transport configured")

    monkeypatch.setattr("alb.api.devices_route.build_transport", _boom)
    app = create_app()
    with TestClient(app) as c:
        body = c.post("/devices/TEST/screenshot").json()
    assert body["ok"] is False
    assert "RuntimeError" in body["error"]


def test_device_ui_dump_happy(monkeypatch, tmp_path) -> None:
    from alb.capabilities.ui import UIDumpData, UINode
    from alb.infra.result import ok as ok_result

    root = UINode(
        index=0, class_name="android.widget.FrameLayout",
        resource_id="", text="", content_desc="",
        bounds=(0, 0, 1080, 2400),
        clickable=False, enabled=True, focused=False, selected=False,
        package="com.example",
    )

    async def fake_ui_dump(transport, **kwargs):
        return ok_result(
            data=UIDumpData(
                path=str(tmp_path / "ui.xml"),
                device_path="/sdcard/window_dump.xml",
                size_bytes=512,
                root=root,
                top_activity="com.example/.MainActivity",
                package_name="com.example",
                node_count=1,
            )
        )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("alb.api.devices_route.ui_dump", fake_ui_dump)
    monkeypatch.setattr(
        "alb.api.devices_route.build_transport",
        lambda **_: _FakeAdbTransport(),
    )
    app = create_app()
    with TestClient(app) as c:
        body = c.post("/devices/TEST/ui-dump").json()
    assert body["ok"] is True
    d = body["ui_dump"]
    assert d["top_activity"] == "com.example/.MainActivity"
    assert d["node_count"] == 1
    assert d["root"]["class"] == "android.widget.FrameLayout"


def test_device_ui_dump_capability_failure_inline(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    from alb.infra.result import fail as fail_result

    async def fake_ui_dump(transport, **kwargs):
        return fail_result(code="UIAUTOMATOR_FAILED", message="device locked")

    monkeypatch.setattr("alb.api.devices_route.ui_dump", fake_ui_dump)
    monkeypatch.setattr(
        "alb.api.devices_route.build_transport",
        lambda **_: _FakeAdbTransport(),
    )
    app = create_app()
    with TestClient(app) as c:
        body = c.post("/devices/TEST/ui-dump").json()
    assert body["ok"] is False
    assert body["ui_dump"] is None
    assert "device locked" in body["error"]


def test_pr_g_endpoints_listed_in_schema(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "alb.api.devices_route.build_transport",
        lambda **_: _FakeAdbTransport(),
    )
    app = create_app()
    with TestClient(app) as c:
        body = c.get("/api/version").json()
    paths = [(e["method"], e["path"]) for e in body["rest"]]
    assert ("POST", "/devices/{serial}/screenshot") in paths
    assert ("POST", "/devices/{serial}/ui-dump") in paths
