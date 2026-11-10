"""Tests for the shell capability."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from alb.capabilities.shell import execute
from alb.infra.permissions import PermissionResult
from alb.transport.base import ShellResult


def _mk_transport(
    *,
    perm_behavior: str = "allow",
    perm_reason: str | None = None,
    shell_ok: bool = True,
    stdout: str = "hello",
    stderr: str = "",
    exit_code: int = 0,
    error_code: str | None = None,
) -> AsyncMock:
    t = AsyncMock()
    t.check_permissions = AsyncMock(
        return_value=PermissionResult(
            behavior=perm_behavior,
            reason=perm_reason,
            suggestion="try narrower scope",
            matched_rule="some-rule",
        )
    )
    t.shell = AsyncMock(
        return_value=ShellResult(
            ok=shell_ok,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_ms=10,
            error_code=error_code,
        )
    )
    return t


@pytest.mark.asyncio
async def test_execute_happy_path() -> None:
    t = _mk_transport(stdout="foo\n")
    r = await execute(t, "echo foo")
    assert r.ok
    assert r.data is not None
    assert r.data.stdout == "foo\n"


@pytest.mark.asyncio
async def test_execute_permission_denied() -> None:
    t = _mk_transport(perm_behavior="deny", perm_reason="rm -rf /")
    r = await execute(t, "rm -rf /")
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "PERMISSION_DENIED"
    assert r.error.category == "permission"
    assert "matched_rule" in r.error.details


@pytest.mark.asyncio
async def test_execute_ask_without_allow_dangerous_is_denied() -> None:
    t = _mk_transport(perm_behavior="ask", perm_reason="mount rw")
    r = await execute(t, "mount -o remount,rw /system")
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "PERMISSION_DENIED"
    assert r.error.details.get("behavior") == "ask"


@pytest.mark.asyncio
async def test_execute_ask_with_allow_dangerous_runs() -> None:
    t = _mk_transport(perm_behavior="ask")
    r = await execute(t, "mount -o remount,rw /system", allow_dangerous=True)
    assert r.ok


@pytest.mark.asyncio
async def test_execute_propagates_transport_error() -> None:
    t = _mk_transport(
        shell_ok=False,
        exit_code=1,
        stderr="device offline",
        error_code="DEVICE_OFFLINE",
    )
    r = await execute(t, "ls")
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "DEVICE_OFFLINE"
    assert "Reconnect" in r.error.suggestion  # shell._suggest_for


@pytest.mark.asyncio
async def test_execute_silent_failure_names_exit_code_and_empty_output() -> None:
    """A non-zero exit with nothing on stdout or stderr must not read like
    success: the message names the exit code and says both streams were empty
    (a board-side `su 0 rm -f a b` came back 255 with no output at all)."""
    t = _mk_transport(
        shell_ok=False, exit_code=255, stdout="", stderr="", error_code="ADB_COMMAND_FAILED",
    )
    r = await execute(t, "su 0 rm -f /data/local/tmp/a /data/local/tmp/b")
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "ADB_COMMAND_FAILED"
    assert "255" in r.error.message
    assert "no output" in r.error.message
    assert "success" in r.error.suggestion
    assert "adbd" in r.error.suggestion  # adb-only hint: the device may refuse by text
    assert r.error.details["exit_code"] == 255


@pytest.mark.asyncio
async def test_execute_failure_with_stdout_only_names_exit_code() -> None:
    """stderr empty but stdout not: the message still carries the exit code
    instead of a bare 'Command failed'."""
    t = _mk_transport(
        shell_ok=False, exit_code=2, stdout="usage: foo\n", stderr="", error_code="ADB_COMMAND_FAILED",
    )
    r = await execute(t, "foo")
    assert r.error is not None
    assert "exit code 2" in r.error.message
    assert r.error.details["stdout"] == "usage: foo\n"


@pytest.mark.asyncio
async def test_execute_failure_keeps_stderr_as_message() -> None:
    t = _mk_transport(
        shell_ok=False, exit_code=1, stdout="", stderr="rm: x: Permission denied\n",
        error_code="ADB_COMMAND_FAILED",
    )
    r = await execute(t, "rm x")
    assert r.error is not None
    assert r.error.message == "rm: x: Permission denied"


@pytest.mark.asyncio
async def test_execute_silent_failure_off_adb_has_no_adbd_hint() -> None:
    """The adbd hint is adb-only; a silent non-zero exit over ssh / serial
    still gets the 'not a success' warning, without talking about adbd."""
    t = _mk_transport(
        shell_ok=False, exit_code=1, stdout="", stderr="", error_code="SHELL_NONZERO_EXIT",
    )
    r = await execute(t, "false")
    assert r.error is not None
    assert "exit code 1" in r.error.message and "no output" in r.error.message
    assert "success" in r.error.suggestion
    assert "adbd" not in r.error.suggestion
