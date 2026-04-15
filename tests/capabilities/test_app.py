"""Tests for app capability."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from alb.capabilities.app import (
    _classify_install_error,
    _grep_first,
    _grep_permissions,
    info,
    list_apps,
    start,
    stop,
    uninstall,
)
from alb.infra.permissions import PermissionResult
from alb.transport.base import ShellResult


def _mk_transport(
    *,
    name: str = "adb",
    perm_behavior: str = "allow",
    shell_responses: dict[str, ShellResult] | None = None,
) -> AsyncMock:
    t = AsyncMock()
    t.name = name
    t.check_permissions = AsyncMock(
        return_value=PermissionResult(behavior=perm_behavior)
    )
    responses = shell_responses or {}

    async def shell(cmd: str, timeout: int = 30) -> ShellResult:
        for prefix, resp in responses.items():
            if cmd.startswith(prefix):
                return resp
        return ShellResult(ok=False, exit_code=1, stderr="unhandled",
                           duration_ms=0, error_code="ADB_COMMAND_FAILED")

    t.shell = shell
    return t


# ─── Helpers ───────────────────────────────────────────────────────
def test_grep_first() -> None:
    assert _grep_first("versionName=1.2.3 extra", r"versionName=([^\s]+)") == "1.2.3"
    assert _grep_first("no match here", r"foo=(\d+)") is None


def test_classify_install_error() -> None:
    assert (
        _classify_install_error("Failure [INSTALL_FAILED_OLDER_SDK]")
        == "INSTALL_FAILED_OLDER_SDK"
    )
    assert _classify_install_error("random") == "APP_INSTALL_FAILED"


def test_grep_permissions_basic() -> None:
    sample = """Packages:
  Package [com.x] (hash):
    requested permissions:
      android.permission.INTERNET
      android.permission.CAMERA: granted=true
      android.permission.LOCATION
Next section:
"""
    perms = _grep_permissions(sample)
    assert "android.permission.INTERNET" in perms
    assert "android.permission.CAMERA" in perms


# ─── uninstall ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_uninstall_invalid_package() -> None:
    t = _mk_transport()
    r = await uninstall(t, "not a package")
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "PACKAGE_NAME_INVALID"


@pytest.mark.asyncio
async def test_uninstall_ask() -> None:
    t = _mk_transport(perm_behavior="ask")
    r = await uninstall(t, "com.example.app")
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_uninstall_happy() -> None:
    t = _mk_transport(shell_responses={
        "pm uninstall": ShellResult(
            ok=True, exit_code=0, stdout="Success\n", stderr="", duration_ms=5
        ),
    })
    r = await uninstall(t, "com.example.app", allow_dangerous=True)
    assert r.ok


@pytest.mark.asyncio
async def test_uninstall_not_installed() -> None:
    t = _mk_transport(shell_responses={
        "pm uninstall": ShellResult(
            ok=True, exit_code=0,
            stdout="Failure [not installed for 0]\n",
            stderr="", duration_ms=5,
        ),
    })
    r = await uninstall(t, "com.x.y", allow_dangerous=True)
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "APP_NOT_INSTALLED"


# ─── start / stop ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_start_with_activity() -> None:
    t = _mk_transport(shell_responses={
        "am start": ShellResult(
            ok=True, exit_code=0, stdout="Starting:", stderr="", duration_ms=5
        ),
    })
    r = await start(t, "com.example/.Main")
    assert r.ok


@pytest.mark.asyncio
async def test_start_with_package_invalid() -> None:
    t = _mk_transport()
    r = await start(t, "no-dot-no-activity")
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "PACKAGE_NAME_INVALID"


@pytest.mark.asyncio
async def test_stop_happy() -> None:
    t = _mk_transport(shell_responses={
        "am force-stop": ShellResult(
            ok=True, exit_code=0, stdout="", stderr="", duration_ms=5
        ),
    })
    r = await stop(t, "com.example.app")
    assert r.ok


# ─── list / info ───────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_list_apps_filters() -> None:
    t = _mk_transport(shell_responses={
        "pm list packages": ShellResult(
            ok=True, exit_code=0,
            stdout="package:com.one\npackage:com.two.demo\npackage:com.three.demo\n",
            stderr="", duration_ms=5,
        ),
    })
    r = await list_apps(t, filter="demo")
    assert r.ok
    assert r.data is not None
    assert sorted(r.data.packages) == ["com.three.demo", "com.two.demo"]


@pytest.mark.asyncio
async def test_info_happy() -> None:
    sample = """Packages:
  Package [com.example] (abc):
    versionName=1.2.3 versionCode=45
    firstInstallTime=2026-04-01
    lastUpdateTime=2026-04-15
    requested permissions:
      android.permission.INTERNET
"""
    t = _mk_transport(shell_responses={
        "dumpsys package": ShellResult(
            ok=True, exit_code=0, stdout=sample, stderr="", duration_ms=5
        ),
    })
    r = await info(t, "com.example")
    assert r.ok
    assert r.data is not None
    assert r.data.version_name == "1.2.3"
    assert r.data.version_code == "45"


@pytest.mark.asyncio
async def test_info_not_installed() -> None:
    t = _mk_transport(shell_responses={
        "dumpsys package": ShellResult(
            ok=True, exit_code=0,
            stdout="Unable to find package: com.x\n",
            stderr="", duration_ms=5,
        ),
    })
    r = await info(t, "com.x")
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "APP_NOT_INSTALLED"
