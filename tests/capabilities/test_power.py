"""Tests for power capability."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from alb.capabilities.power import battery, reboot, _parse_battery
from alb.infra.permissions import PermissionResult
from alb.transport.base import ShellResult


def _mk_transport(
    *,
    name: str = "adb",
    perm_behavior: str = "allow",
    shell_ok: bool = True,
    shell_stdout: str = "",
    reboot_ok: bool = True,
) -> AsyncMock:
    t = AsyncMock()
    t.name = name
    t.check_permissions = AsyncMock(
        return_value=PermissionResult(behavior=perm_behavior, reason="x", suggestion="y")
    )
    t.reboot = AsyncMock(
        return_value=ShellResult(
            ok=reboot_ok,
            exit_code=0 if reboot_ok else 1,
            stdout="", stderr="" if reboot_ok else "failed",
            duration_ms=5,
        )
    )
    t.shell = AsyncMock(
        return_value=ShellResult(
            ok=shell_ok,
            exit_code=0 if shell_ok else 1,
            stdout=shell_stdout, stderr="",
            duration_ms=5,
        )
    )
    return t


@pytest.mark.asyncio
async def test_reboot_invalid_mode() -> None:
    t = _mk_transport()
    r = await reboot(t, "warp-speed")
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "INVALID_FILTER"


@pytest.mark.asyncio
async def test_reboot_ask_without_allow() -> None:
    t = _mk_transport(perm_behavior="ask")
    r = await reboot(t, "recovery", wait_boot=False)
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_reboot_ask_with_allow() -> None:
    t = _mk_transport(perm_behavior="ask")
    r = await reboot(t, "recovery", wait_boot=False, allow_dangerous=True)
    assert r.ok


@pytest.mark.asyncio
async def test_reboot_bootloader_over_serial_goes_to_transport() -> None:
    """Non-normal modes are no longer adb-only: the transport decides."""
    t = _mk_transport(name="serial")
    r = await reboot(t, "bootloader", wait_boot=False)
    assert r.ok
    t.reboot.assert_awaited_once_with("bootloader")


@pytest.mark.asyncio
async def test_reboot_transport_refusal_is_reported() -> None:
    t = _mk_transport(name="ssh")
    t.reboot = AsyncMock(
        return_value=ShellResult(
            ok=False, exit_code=-1, stderr="not supported",
            error_code="TRANSPORT_NOT_SUPPORTED",
        )
    )
    r = await reboot(t, "recovery", wait_boot=False)
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "TRANSPORT_NOT_SUPPORTED"


@pytest.mark.asyncio
async def test_reboot_normal_no_wait_happy() -> None:
    t = _mk_transport()
    r = await reboot(t, "normal", wait_boot=False)
    assert r.ok
    assert r.data is not None
    assert r.data.mode == "normal"
    assert r.data.wait_boot_ms is None


@pytest.mark.asyncio
async def test_battery_parses_dumpsys() -> None:
    dumpsys_sample = """Current Battery Service state:
  AC powered: false
  USB powered: true
  status: 2
  health: 2
  level: 85
  scale: 100
  voltage: 4321
  temperature: 280
"""
    t = _mk_transport(shell_stdout=dumpsys_sample)
    r = await battery(t)
    assert r.ok
    assert r.data is not None
    assert r.data.level == 85
    assert r.data.voltage_mv == 4321
    assert r.data.temperature_deci_c == 280


def test_parse_battery_handles_missing_fields() -> None:
    info = _parse_battery("level: 50\nscale: 100\n")
    assert info.level == 50
    assert info.scale == 100
    assert info.voltage_mv == -1


# ─── adb finds no device → serial fallback (2026-10-23) ─────────────
def _not_found() -> ShellResult:
    return ShellResult(
        ok=False, exit_code=1, stderr="error: no devices/emulators found",
        error_code="DEVICE_NOT_FOUND",
    )


@pytest.mark.asyncio
async def test_reboot_falls_back_to_serial_and_keeps_adb_error() -> None:
    adb = _mk_transport(name="adb")
    adb.reboot = AsyncMock(return_value=_not_found())
    ser = _mk_transport(name="serial")
    r = await reboot(adb, "bootloader", wait_boot=False, serial_fallback=ser)
    assert r.ok, r.error
    ser.reboot.assert_awaited_once_with("bootloader")
    assert r.data is not None
    assert r.data.fallback == {
        "from": "adb",
        "error_code": "DEVICE_NOT_FOUND",
        "message": "error: no devices/emulators found",
    }
    assert r.data.to_dict()["fallback"]["error_code"] == "DEVICE_NOT_FOUND"


@pytest.mark.asyncio
async def test_reboot_without_fallback_reports_adb_error() -> None:
    adb = _mk_transport(name="adb")
    adb.reboot = AsyncMock(return_value=_not_found())
    r = await reboot(adb, "bootloader", wait_boot=False)
    assert not r.ok
    assert r.error is not None and r.error.code == "DEVICE_NOT_FOUND"


@pytest.mark.asyncio
async def test_reboot_other_adb_errors_do_not_fall_back() -> None:
    """Only "no device" moves to serial: an offline / unauthorized device is
    still THE device, and rebooting whatever is on the console instead would
    hide that."""
    adb = _mk_transport(name="adb")
    adb.reboot = AsyncMock(return_value=ShellResult(
        ok=False, exit_code=1, stderr="error: device offline",
        error_code="DEVICE_OFFLINE",
    ))
    ser = _mk_transport(name="serial")
    r = await reboot(adb, "bootloader", wait_boot=False, serial_fallback=ser)
    assert not r.ok
    assert r.error is not None and r.error.code == "DEVICE_OFFLINE"
    ser.reboot.assert_not_awaited()


@pytest.mark.asyncio
async def test_reboot_fallback_failure_reports_both_errors() -> None:
    adb = _mk_transport(name="adb")
    adb.reboot = AsyncMock(return_value=_not_found())
    ser = _mk_transport(name="serial")
    ser.reboot = AsyncMock(return_value=ShellResult(
        ok=False, exit_code=-1, stderr="no bytes from the console",
        error_code="BOARD_UNREACHABLE",
    ))
    r = await reboot(adb, "bootloader", wait_boot=False, serial_fallback=ser)
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "BOARD_UNREACHABLE"
    assert "no devices/emulators found" in r.error.message
    assert "no bytes from the console" in r.error.message
    assert r.error.details["fallback"]["error_code"] == "DEVICE_NOT_FOUND"
