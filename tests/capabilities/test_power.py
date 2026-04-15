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
async def test_reboot_non_adb_for_recovery() -> None:
    t = _mk_transport(name="ssh")
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
