"""Tests for AdbTransport."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from alb.transport.adb import AdbTransport, parse_devices_output


# ─── Pure parser ───────────────────────────────────────────────────
def test_parse_devices_output_empty() -> None:
    assert parse_devices_output("") == []
    assert parse_devices_output("List of devices attached\n") == []


def test_parse_devices_output_with_state() -> None:
    out = (
        "List of devices attached\n"
        "abc123   device product:foo model:Bar transport_id:3\n"
        "def456   unauthorized\n"
    )
    devs = parse_devices_output(out)
    assert len(devs) == 2
    assert devs[0].serial == "abc123"
    assert devs[0].state == "device"
    assert devs[0].model == "Bar"
    assert devs[0].product == "foo"
    assert devs[1].state == "unauthorized"


# ─── Permission hook (does NOT invoke subprocess) ──────────────────
@pytest.mark.asyncio
async def test_check_permissions_allows_plain_shell() -> None:
    t = AdbTransport()
    r = await t.check_permissions("shell.execute", {"cmd": "ls /sdcard"})
    assert r.behavior == "allow"


@pytest.mark.asyncio
async def test_check_permissions_denies_dangerous() -> None:
    t = AdbTransport()
    r = await t.check_permissions("shell.execute", {"cmd": "rm -rf /sdcard"})
    assert r.behavior == "deny"
    assert r.matched_rule


@pytest.mark.asyncio
async def test_check_permissions_asks_on_system_push() -> None:
    t = AdbTransport()
    r = await t.check_permissions(
        "filesync.push",
        {"local": "/x", "remote": "/system/priv-app/foo"},
    )
    assert r.behavior == "ask"
    assert "/system/" in (r.reason or "")


@pytest.mark.asyncio
async def test_check_permissions_denies_block_device_push() -> None:
    t = AdbTransport()
    r = await t.check_permissions(
        "filesync.push",
        {"local": "/x", "remote": "/dev/block/by-name/boot"},
    )
    assert r.behavior == "deny"


@pytest.mark.asyncio
async def test_check_permissions_asks_reboot_recovery() -> None:
    t = AdbTransport()
    r = await t.check_permissions("power.reboot", {"mode": "recovery"})
    assert r.behavior == "ask"


# ─── Mocked subprocess (validates argv shape + env handling) ───────
class _FakeProc:
    def __init__(self, out: bytes = b"", err: bytes = b"", code: int = 0) -> None:
        self._out = out
        self._err = err
        self.returncode = code

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:  # noqa: A002
        return self._out, self._err


@pytest.mark.asyncio
async def test_shell_passes_serial_and_cmd() -> None:
    recorded: dict[str, object] = {}

    async def fake_exec(*args: str, **kw: object) -> _FakeProc:
        recorded["args"] = args
        recorded["env"] = kw.get("env")
        return _FakeProc(out=b"hello\n")

    t = AdbTransport(serial="abc", server_socket="tcp:localhost:5037")
    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        r = await t.shell("echo hello")

    assert r.ok
    assert r.stdout == "hello\n"
    argv = recorded["args"]
    assert isinstance(argv, tuple)
    assert "-s" in argv and "abc" in argv
    assert "shell" in argv and "echo hello" in argv
    env = recorded["env"]
    assert isinstance(env, dict)
    assert env.get("ADB_SERVER_SOCKET") == "tcp:localhost:5037"


@pytest.mark.asyncio
async def test_shell_classifies_offline_error() -> None:
    async def fake_exec(*args: str, **kw: object) -> _FakeProc:
        return _FakeProc(err=b"error: device offline\n", code=1)

    t = AdbTransport()
    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        r = await t.shell("ls")
    assert not r.ok
    assert r.error_code == "DEVICE_OFFLINE"


@pytest.mark.asyncio
async def test_shell_timeout_returns_structured_error() -> None:
    async def never_returns(*args: str, **kw: object) -> _FakeProc:
        proc = _FakeProc()

        async def slow(input: bytes | None = None) -> tuple[bytes, bytes]:  # noqa: A002
            import asyncio

            await asyncio.sleep(10)
            return b"", b""

        proc.communicate = slow  # type: ignore[method-assign]
        proc.terminate = lambda: None  # type: ignore[method-assign]
        proc.kill = lambda: None  # type: ignore[method-assign]
        proc.wait = AsyncMock(return_value=0)  # type: ignore[method-assign]
        return proc

    t = AdbTransport()
    with patch("asyncio.create_subprocess_exec", side_effect=never_returns):
        r = await t.shell("sleep 30", timeout=0)
    assert not r.ok
    assert r.error_code == "TIMEOUT_SHELL"
