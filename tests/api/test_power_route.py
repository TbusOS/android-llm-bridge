"""Tests for /api/power/* — battery / reboot / sleep-wake REST surface."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from alb.api.server import create_app
from alb.capabilities.power import BatteryInfo, RebootResult
from alb.infra.result import fail, ok
from alb.transport.base import PermissionResult


class _StubTransport:
    """Bare-bones transport: only the methods power_cap touches."""

    name = "adb"

    def __init__(self, *, shell_stdout: str = "", shell_ok: bool = True) -> None:
        self.shell = AsyncMock()
        self.shell.return_value = type(
            "_R",
            (),
            {
                "ok": shell_ok,
                "exit_code": 0,
                "stdout": shell_stdout,
                "stderr": "",
                "duration_ms": 5,
                "error_code": None,
            },
        )()
        self.reboot = AsyncMock(
            return_value=type(
                "_R",
                (),
                {
                    "ok": True,
                    "exit_code": 0,
                    "stdout": "",
                    "stderr": "",
                    "duration_ms": 10,
                    "error_code": None,
                },
            )()
        )
        self.check_permissions = AsyncMock(
            return_value=PermissionResult(behavior="allow")
        )


@pytest.fixture
def stub_transport():
    return _StubTransport()


@pytest.fixture
def client(monkeypatch, stub_transport):
    monkeypatch.setattr(
        "alb.api.power_route.build_transport", lambda **_kw: stub_transport
    )
    app = create_app()
    with TestClient(app) as c:
        yield c


# ─── /battery ──────────────────────────────────────────────────────
def test_battery_returns_parsed_payload(monkeypatch, stub_transport) -> None:
    """`alb.capabilities.power.battery` is the canonical parser; the
    route is a thin pass-through. Just verify the envelope matches."""
    async def _battery(_t):
        return ok(
            data=BatteryInfo(
                level=85,
                scale=100,
                health="Good",
                status="Charging",
                plugged="AC",
                temperature_deci_c=275,
                voltage_mv=4123,
            ),
            timing_ms=12,
        )

    monkeypatch.setattr("alb.api.power_route.power_cap.battery", _battery)
    monkeypatch.setattr(
        "alb.api.power_route.build_transport", lambda **_kw: stub_transport
    )
    app = create_app()
    with TestClient(app) as c:
        r = c.get("/api/power/battery?device=abc123")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["level"] == 85
    assert body["data"]["temperature_celsius"] == 27.5
    assert body["timing_ms"] == 12


def test_battery_bad_device_400(monkeypatch) -> None:
    """Unsafe serial (path traversal / control chars) is rejected at 400
    before reaching transport init."""
    app = create_app()
    with TestClient(app) as c:
        r = c.get("/api/power/battery?device=../etc")
    assert r.status_code == 400
    assert "invalid device" in r.json()["detail"].lower()


def test_battery_failure_returns_envelope(monkeypatch, stub_transport) -> None:
    async def _battery(_t):
        return fail(
            code="ADB_COMMAND_FAILED",
            message="device offline",
            suggestion="reconnect",
            category="transport",
        )

    monkeypatch.setattr("alb.api.power_route.power_cap.battery", _battery)
    monkeypatch.setattr(
        "alb.api.power_route.build_transport", lambda **_kw: stub_transport
    )
    app = create_app()
    with TestClient(app) as c:
        r = c.get("/api/power/battery?device=abc")
    assert r.status_code == 200  # envelope-style, not HTTP error
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "ADB_COMMAND_FAILED"


# ─── /reboot ───────────────────────────────────────────────────────
def test_reboot_normal_happy_path(monkeypatch, stub_transport) -> None:
    async def _reboot(*_a, **_kw):
        return ok(data=RebootResult(mode="normal", wait_boot_ms=2400), timing_ms=2500)

    monkeypatch.setattr("alb.api.power_route.power_cap.reboot", _reboot)
    monkeypatch.setattr(
        "alb.api.power_route.build_transport", lambda **_kw: stub_transport
    )
    app = create_app()
    with TestClient(app) as c:
        r = c.post("/api/power/reboot?device=abc", json={"mode": "normal"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["mode"] == "normal"
    assert body["data"]["wait_boot_ms"] == 2400


def test_reboot_invalid_mode_returns_envelope(monkeypatch, stub_transport) -> None:
    """Unknown mode bubbles up through the capability's INVALID_FILTER err."""
    app = create_app()
    with TestClient(app) as c:
        r = c.post("/api/power/reboot?device=abc", json={"mode": "bogus"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "INVALID_FILTER"


def test_reboot_dangerous_mode_requires_allow_flag(monkeypatch, stub_transport) -> None:
    """recovery/bootloader/fastboot/sideload need allow_dangerous=true."""
    stub_transport.check_permissions = AsyncMock(
        return_value=PermissionResult(
            behavior="ask",
            reason="dangerous reboot",
            suggestion="Pass allow_dangerous=true",
        )
    )
    app = create_app()
    with TestClient(app) as c:
        r = c.post(
            "/api/power/reboot?device=abc",
            json={"mode": "recovery", "allow_dangerous": False},
        )
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "PERMISSION_DENIED"


# ─── /sleep-wake ───────────────────────────────────────────────────
def test_sleep_wake_runs_one_cycle(monkeypatch, stub_transport) -> None:
    async def _sleep_wake(_t, *, cycles, hold_sec):
        return ok(
            data={
                "cycles": cycles,
                "records": [{"cycle": 1, "duration_ms": int(hold_sec * 1000)}],
                "total_ms": hold_sec * 1000,
            },
            timing_ms=hold_sec * 1000,
        )

    monkeypatch.setattr("alb.api.power_route.power_cap.sleep_wake_test", _sleep_wake)
    monkeypatch.setattr(
        "alb.api.power_route.build_transport", lambda **_kw: stub_transport
    )
    app = create_app()
    with TestClient(app) as c:
        r = c.post(
            "/api/power/sleep-wake?device=abc",
            json={"cycles": 1, "hold_sec": 2},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["cycles"] == 1


def test_sleep_wake_validates_cycles_range() -> None:
    """Pydantic rejects cycles > 1000 with 422."""
    app = create_app()
    with TestClient(app) as c:
        r = c.post(
            "/api/power/sleep-wake?device=abc",
            json={"cycles": 99999, "hold_sec": 1},
        )
    assert r.status_code == 422


def test_transport_init_failure_returns_envelope_b_not_503(monkeypatch) -> None:
    """Architecture ADR (REST envelope 三态约定 b): build_transport
    failure → 200 + ok=false envelope, NOT HTTPException(503)."""
    def _boom(**_kw: Any):
        raise RuntimeError("adb server unreachable")

    monkeypatch.setattr("alb.api.power_route.build_transport", _boom)
    app = create_app()
    with TestClient(app) as c:
        r = c.get("/api/power/battery?device=abc")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["transport"] is None
    assert body["error"]["code"] == "TRANSPORT_INIT_FAILED"
    assert body["device"] == "abc"
