"""Tests for HybridTransport routing."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from alb.infra.permissions import PermissionResult
from alb.transport.base import ShellResult, Transport
from alb.transport.hybrid import HybridTransport


# ─── Test doubles ────────────────────────────────────────────────────


class FakeTransport(Transport):
    """Minimal Transport impl that records which method was called."""

    def __init__(
        self,
        name: str,
        *,
        supports_boot_log: bool = False,
        supports_recovery: bool = False,
    ) -> None:
        self.name = name  # override class-level attr per instance
        self.supports_boot_log = supports_boot_log
        self.supports_recovery = supports_recovery
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def _record(self, op: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((op, args, kwargs))

    async def shell(self, cmd: str, *, timeout: int = 30) -> ShellResult:
        self._record("shell", cmd, timeout=timeout)
        return ShellResult(ok=True, stdout=f"{self.name}:shell", exit_code=0)

    async def stream_read(self, source: str, **kwargs: Any) -> AsyncIterator[bytes]:
        self._record("stream_read", source, **kwargs)
        for chunk in (f"{self.name}:{source}:a".encode(), f"{self.name}:{source}:b".encode()):
            yield chunk

    async def push(self, local: Path, remote: str) -> ShellResult:
        self._record("push", local, remote)
        return ShellResult(ok=True, stdout=f"{self.name}:push", exit_code=0)

    async def pull(self, remote: str, local: Path) -> ShellResult:
        self._record("pull", remote, local)
        return ShellResult(ok=True, stdout=f"{self.name}:pull", exit_code=0)

    async def forward(self, local_port: int, remote_port: int) -> ShellResult:
        self._record("forward", local_port, remote_port)
        return ShellResult(ok=True, stdout=f"{self.name}:forward", exit_code=0)

    async def reboot(self, mode: str = "normal") -> ShellResult:
        self._record("reboot", mode)
        return ShellResult(ok=True, stdout=f"{self.name}:reboot:{mode}", exit_code=0)

    async def check_permissions(
        self, action: str, input_data: dict[str, Any]
    ) -> PermissionResult:
        self._record("check_permissions", action, input_data)
        return PermissionResult(behavior="allow", reason=f"{self.name}:ok")

    async def health(self) -> dict[str, Any]:
        return {"name": self.name, "reachable": True}


@pytest.fixture
def hybrid_all() -> tuple[HybridTransport, FakeTransport, FakeTransport, FakeTransport]:
    """Hybrid with adb primary + ssh + serial alternates."""
    adb = FakeTransport("adb", supports_recovery=True)
    ssh = FakeTransport("ssh")
    ser = FakeTransport("serial", supports_boot_log=True)
    h = HybridTransport(primary=adb, alternates=[ssh, ser])
    return h, adb, ssh, ser


# ─── Construction ────────────────────────────────────────────────────


def test_requires_primary() -> None:
    with pytest.raises(ValueError):
        HybridTransport(primary=None)  # type: ignore[arg-type]


def test_aggregates_capability_flags() -> None:
    adb = FakeTransport("adb", supports_recovery=True)
    ser = FakeTransport("serial", supports_boot_log=True)
    h = HybridTransport(primary=adb, alternates=[ser])
    assert h.supports_recovery is True
    assert h.supports_boot_log is True


def test_aggregates_capability_flags_partial() -> None:
    adb = FakeTransport("adb", supports_recovery=True)
    ssh = FakeTransport("ssh")
    h = HybridTransport(primary=adb, alternates=[ssh])
    assert h.supports_recovery is True
    assert h.supports_boot_log is False  # neither has it


# ─── pick_for: stream_read ───────────────────────────────────────────


def test_pick_stream_logcat_prefers_adb(hybrid_all) -> None:
    h, adb, ssh, ser = hybrid_all
    assert h.pick_for("stream_read", "logcat") is adb


def test_pick_stream_logcat_fallback_to_ssh() -> None:
    # Hybrid without adb — logcat should go to ssh
    ssh = FakeTransport("ssh")
    ser = FakeTransport("serial", supports_boot_log=True)
    h = HybridTransport(primary=ssh, alternates=[ser])
    assert h.pick_for("stream_read", "logcat") is ssh


def test_pick_stream_logcat_skips_serial() -> None:
    # Only serial present — logcat has no candidate (serial is not in logcat order)
    ser = FakeTransport("serial", supports_boot_log=True)
    h = HybridTransport(primary=ser)
    assert h.pick_for("stream_read", "logcat") is None


def test_pick_stream_uart_requires_serial(hybrid_all) -> None:
    h, adb, ssh, ser = hybrid_all
    assert h.pick_for("stream_read", "uart") is ser


def test_pick_stream_uart_no_serial_returns_none() -> None:
    adb = FakeTransport("adb")
    ssh = FakeTransport("ssh")
    h = HybridTransport(primary=adb, alternates=[ssh])
    assert h.pick_for("stream_read", "uart") is None


def test_pick_stream_dmesg_prefers_adb_but_falls_back(hybrid_all) -> None:
    h, adb, ssh, ser = hybrid_all
    assert h.pick_for("stream_read", "dmesg") is adb


def test_pick_stream_unknown_source_returns_primary(hybrid_all) -> None:
    h, adb, ssh, ser = hybrid_all
    assert h.pick_for("stream_read", "something-new") is adb


# ─── pick_for: push / pull ───────────────────────────────────────────


def test_pick_push_prefers_ssh(hybrid_all) -> None:
    h, adb, ssh, ser = hybrid_all
    assert h.pick_for("push") is ssh
    assert h.pick_for("pull") is ssh


def test_pick_push_falls_back_to_adb_when_no_ssh() -> None:
    adb = FakeTransport("adb")
    ser = FakeTransport("serial")
    h = HybridTransport(primary=adb, alternates=[ser])
    assert h.pick_for("push") is adb


def test_pick_push_denies_when_only_serial() -> None:
    ser = FakeTransport("serial")
    h = HybridTransport(primary=ser)
    assert h.pick_for("push") is None


# ─── pick_for: forward ───────────────────────────────────────────────


def test_pick_forward_prefers_adb(hybrid_all) -> None:
    h, adb, ssh, ser = hybrid_all
    assert h.pick_for("forward") is adb


def test_pick_forward_denies_serial_only() -> None:
    ser = FakeTransport("serial")
    h = HybridTransport(primary=ser)
    assert h.pick_for("forward") is None


# ─── pick_for: reboot ────────────────────────────────────────────────


def test_pick_reboot_recovery_requires_adb(hybrid_all) -> None:
    h, adb, ssh, ser = hybrid_all
    assert h.pick_for("reboot", "recovery") is adb
    assert h.pick_for("reboot", "bootloader") is adb
    assert h.pick_for("reboot", "fastboot") is adb
    assert h.pick_for("reboot", "sideload") is adb


def test_pick_reboot_recovery_no_adb_returns_none() -> None:
    ssh = FakeTransport("ssh")
    h = HybridTransport(primary=ssh)
    assert h.pick_for("reboot", "recovery") is None


def test_pick_reboot_normal_uses_primary(hybrid_all) -> None:
    h, adb, ssh, ser = hybrid_all
    assert h.pick_for("reboot", "normal") is adb


# ─── pick_for: shell + unknown ──────────────────────────────────────


def test_pick_shell_uses_primary(hybrid_all) -> None:
    h, adb, ssh, ser = hybrid_all
    assert h.pick_for("shell", "ls /sdcard") is adb


def test_pick_unknown_op_uses_primary(hybrid_all) -> None:
    h, adb, ssh, ser = hybrid_all
    assert h.pick_for("something-novel") is adb


# ─── End-to-end method dispatch ──────────────────────────────────────


@pytest.mark.asyncio
async def test_shell_dispatches_to_primary(hybrid_all) -> None:
    h, adb, ssh, ser = hybrid_all
    r = await h.shell("ls /sdcard")
    assert r.ok and r.stdout == "adb:shell"
    assert adb.calls[0][0] == "shell"
    assert ssh.calls == [] and ser.calls == []


@pytest.mark.asyncio
async def test_stream_read_logcat_dispatches_to_adb(hybrid_all) -> None:
    h, adb, ssh, ser = hybrid_all
    chunks = [c async for c in h.stream_read("logcat")]
    assert chunks == [b"adb:logcat:a", b"adb:logcat:b"]
    assert adb.calls[0][0] == "stream_read"


@pytest.mark.asyncio
async def test_stream_read_uart_dispatches_to_serial(hybrid_all) -> None:
    h, adb, ssh, ser = hybrid_all
    chunks = [c async for c in h.stream_read("uart")]
    assert chunks == [b"serial:uart:a", b"serial:uart:b"]


@pytest.mark.asyncio
async def test_stream_read_no_route_raises() -> None:
    adb = FakeTransport("adb")
    h = HybridTransport(primary=adb)  # no serial
    with pytest.raises(NotImplementedError):
        async for _ in h.stream_read("uart"):
            pass


@pytest.mark.asyncio
async def test_push_routes_to_ssh(hybrid_all) -> None:
    h, adb, ssh, ser = hybrid_all
    r = await h.push(Path("/tmp/a"), "/data/local/tmp/")
    assert r.ok and r.stdout == "ssh:push"
    assert ssh.calls[0][0] == "push"


@pytest.mark.asyncio
async def test_push_no_route_returns_error() -> None:
    ser = FakeTransport("serial")
    h = HybridTransport(primary=ser)
    r = await h.push(Path("/tmp/a"), "/data/")
    assert r.ok is False
    assert r.error_code == "TRANSPORT_NOT_SUPPORTED"


@pytest.mark.asyncio
async def test_reboot_recovery_routes_to_adb(hybrid_all) -> None:
    h, adb, ssh, ser = hybrid_all
    r = await h.reboot("recovery")
    assert r.ok and r.stdout == "adb:reboot:recovery"


@pytest.mark.asyncio
async def test_reboot_recovery_without_adb_fails() -> None:
    ssh = FakeTransport("ssh")
    h = HybridTransport(primary=ssh)
    r = await h.reboot("recovery")
    assert r.ok is False
    assert r.error_code == "TRANSPORT_NOT_SUPPORTED"


# ─── Permissions + health ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_check_permissions_delegates_to_primary(hybrid_all) -> None:
    h, adb, ssh, ser = hybrid_all
    r = await h.check_permissions("shell.execute", {"cmd": "ls"})
    assert r.behavior == "allow"
    assert r.reason == "adb:ok"
    assert adb.calls[0][0] == "check_permissions"
    # alternates must NOT be consulted
    assert ssh.calls == [] and ser.calls == []


@pytest.mark.asyncio
async def test_health_aggregates_all(hybrid_all) -> None:
    h, adb, ssh, ser = hybrid_all
    snap = await h.health()
    assert snap["transport"] == "hybrid"
    assert snap["primary"] == "adb"
    assert set(snap["alternates"]) == {"ssh", "serial"}
    assert set(snap["sub"].keys()) == {"adb", "ssh", "serial"}
    assert all(s["reachable"] for s in snap["sub"].values())
