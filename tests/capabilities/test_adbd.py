"""Tests for the adbd capability (root / unroot / remount).

The fake stands in for AdbTransport at the `_run` level — the only seam the
capability uses — so `adb root` / `adb remount` replies and the `id -u` poll
sequence are both scripted. No subprocess, no device.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from alb.capabilities.adbd import remount, root, unroot
from alb.infra.permissions import Behavior, PermissionResult
from alb.transport.base import ShellResult


def _ok(stdout: str = "", exit_code: int = 0) -> ShellResult:
    return ShellResult(ok=exit_code == 0, exit_code=exit_code, stdout=stdout, duration_ms=3)


def _err(stderr: str, code: str = "ADB_COMMAND_FAILED", exit_code: int = 1) -> ShellResult:
    return ShellResult(ok=False, exit_code=exit_code, stderr=stderr, error_code=code, duration_ms=3)


class FakeAdb:
    """`_run(["root"])` answers from `replies`; `id -u` walks `uid_seq` and
    `pidof adbd` walks `pid_seq` (last entry repeats, so a poll can run past
    the script's end). No `pid_seq` = `pidof` unavailable."""

    name = "adb"

    def __init__(
        self,
        *,
        replies: dict[str, ShellResult] | None = None,
        uid_seq: list[ShellResult] | None = None,
        pid_seq: list[ShellResult] | None = None,
        behavior: Behavior = "allow",
    ) -> None:
        self.replies = replies or {}
        self.uid_seq = list(uid_seq or [_ok("0\n")])
        self.pid_seq = list(pid_seq or [_err("/system/bin/sh: pidof: inaccessible or not found")])
        self.behavior = behavior
        self.calls: list[list[str]] = []

    async def check_permissions(self, action: str, input_data: dict[str, Any]) -> PermissionResult:
        return PermissionResult(behavior=self.behavior, reason="blocked by test")

    # `**kw` swallows timeout= / stdin= — the fake mirrors AdbTransport's call
    # shape without re-declaring its parameters.
    async def _run(self, args: list[str], **kw: Any) -> ShellResult:
        self.calls.append(list(args))
        if args[0] == "shell":
            return await self.shell(args[1])
        return self.replies[args[0]]

    async def shell(self, cmd: str, **kw: Any) -> ShellResult:
        self.calls.append(["shell", cmd])
        if cmd == "pidof adbd":
            return self.pid_seq.pop(0) if len(self.pid_seq) > 1 else self.pid_seq[0]
        if cmd != "id -u":
            return _err("unhandled in test")
        return self.uid_seq.pop(0) if len(self.uid_seq) > 1 else self.uid_seq[0]

    def uid_polls(self) -> int:
        return sum(1 for c in self.calls if c == ["shell", "id -u"])

    def subcommands(self) -> list[list[str]]:
        """adb subcommands (root / unroot / remount), shell calls left out."""
        return [c for c in self.calls if c[0] != "shell"]


RESTARTING = _ok("restarting adbd as root\n")
ALREADY = _ok("adbd is already running as root\n")
REFUSED = _ok("adbd cannot run as root in production builds\n")


# ─── root ──────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_root_becomes_root() -> None:
    t = FakeAdb(replies={"root": RESTARTING}, uid_seq=[_ok("0\n")])
    r = await root(t, poll_sec=0)
    assert r.ok
    assert r.data is not None
    assert r.data.outcome == "became_root"
    assert r.data.uid == 0
    assert r.data.polls == 1
    assert r.data.adbd_output == "restarting adbd as root"
    assert t.subcommands() == [["root"]]


@pytest.mark.asyncio
async def test_root_already_root() -> None:
    t = FakeAdb(replies={"root": ALREADY}, uid_seq=[_ok("0\n")])
    r = await root(t, poll_sec=0)
    assert r.ok
    assert r.data is not None
    assert r.data.outcome == "already_root"
    assert r.data.uid == 0


@pytest.mark.asyncio
async def test_root_refused_on_production_build() -> None:
    t = FakeAdb(replies={"root": REFUSED})
    r = await root(t, poll_sec=0)
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "ADB_ROOT_REFUSED"
    # The device's own words, untouched, and the toybox form of su.
    assert r.error.message == "adbd cannot run as root in production builds"
    assert "su 0 " in r.error.suggestion
    # adbd did not restart, so nothing was waited for.
    assert t.uid_polls() == 0


@pytest.mark.asyncio
async def test_root_refusal_is_recognised_whatever_the_exit_status() -> None:
    """The adb client decides the exit status, not adbd; a refusal that comes
    back non-zero must still say ADB_ROOT_REFUSED with the `su 0` hint, not a
    generic "check alb devices"."""
    t = FakeAdb(replies={"root": _ok("adbd cannot run as root in production builds\n", 1)})
    r = await root(t, poll_sec=0)
    assert not r.ok and r.error is not None
    assert r.error.code == "ADB_ROOT_REFUSED"
    assert "su 0 " in r.error.suggestion
    assert t.uid_polls() == 0


@pytest.mark.asyncio
async def test_root_waits_while_device_is_away() -> None:
    """adbd restart: two polls fail (device gone / offline), third answers."""
    t = FakeAdb(
        replies={"root": RESTARTING},
        uid_seq=[
            _err("error: no devices/emulators found", "DEVICE_NOT_FOUND"),
            _err("error: device offline", "DEVICE_OFFLINE"),
            _ok("0\n"),
        ],
    )
    r = await root(t, poll_sec=0)
    assert r.ok
    assert r.data is not None
    assert r.data.outcome == "became_root"
    assert r.data.polls == 3
    assert t.uid_polls() == 3


@pytest.mark.asyncio
async def test_root_retries_when_old_adbd_still_answers() -> None:
    """Old adb returns before adbd restarts: first `id -u` still says 2000."""
    t = FakeAdb(replies={"root": RESTARTING}, uid_seq=[_ok("2000\n"), _ok("0\n")])
    r = await root(t, poll_sec=0)
    assert r.ok
    assert r.data is not None
    assert r.data.polls == 2
    assert r.data.uid == 0


@pytest.mark.asyncio
async def test_root_never_comes_back_is_a_timeout() -> None:
    t = FakeAdb(
        replies={"root": RESTARTING},
        uid_seq=[_err("error: device offline", "DEVICE_OFFLINE")],
    )
    r = await root(t, timeout=0, poll_sec=0)
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "ADBD_RESTART_TIMEOUT"
    assert r.error.category == "timeout"
    assert t.uid_polls() == 1  # the budget always allows one look


@pytest.mark.asyncio
async def test_root_uid_unchanged_after_budget_is_refusal() -> None:
    t = FakeAdb(replies={"root": RESTARTING}, uid_seq=[_ok("2000\n")])
    r = await root(t, timeout=0, poll_sec=0)
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "ADB_ROOT_REFUSED"
    assert "still 2000" in r.error.message


@pytest.mark.asyncio
async def test_root_adb_command_failure_passes_code_through() -> None:
    t = FakeAdb(replies={"root": _err("error: no devices/emulators found", "DEVICE_NOT_FOUND")})
    r = await root(t, poll_sec=0)
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "DEVICE_NOT_FOUND"


@pytest.mark.asyncio
async def test_root_permission_deny() -> None:
    t = FakeAdb(replies={"root": RESTARTING}, behavior="deny")
    r = await root(t, poll_sec=0)
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "PERMISSION_DENIED"
    assert t.calls == []  # nothing reached adb


@pytest.mark.asyncio
async def test_root_needs_an_adb_transport() -> None:
    class Serial:
        name = "serial"

    r = await root(Serial(), poll_sec=0)  # type: ignore[arg-type]
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "TRANSPORT_NOT_SUPPORTED"


@pytest.mark.asyncio
async def test_root_through_hybrid_uses_the_adb_member() -> None:
    adb = FakeAdb(replies={"root": RESTARTING}, uid_seq=[_ok("0\n")])

    class Ssh:
        name = "ssh"

    class Hybrid:
        name = "hybrid"
        primary = Ssh()
        alternates: ClassVar[list[FakeAdb]] = [adb]

        async def check_permissions(
            self, action: str, input_data: dict[str, Any]
        ) -> PermissionResult:
            return PermissionResult(behavior="allow")

    r = await root(Hybrid(), poll_sec=0)  # type: ignore[arg-type]
    assert r.ok
    assert adb.subcommands() == [["root"]]


# ─── unroot ────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_unroot_becomes_non_root_after_reconnect() -> None:
    t = FakeAdb(
        replies={"unroot": _ok("restarting adbd as non root\n")},
        uid_seq=[_err("error: device offline", "DEVICE_OFFLINE"), _ok("2000\n")],
    )
    r = await unroot(t, poll_sec=0)
    assert r.ok
    assert r.data is not None
    assert r.data.outcome == "became_non_root"
    assert r.data.uid == 2000
    assert r.data.polls == 2
    assert t.subcommands() == [["unroot"]]


@pytest.mark.asyncio
async def test_unroot_already_non_root() -> None:
    t = FakeAdb(replies={"unroot": _ok("adbd not running as root\n")}, uid_seq=[_ok("2000\n")])
    r = await unroot(t, poll_sec=0)
    assert r.ok
    assert r.data is not None
    assert r.data.outcome == "already_non_root"


@pytest.mark.asyncio
async def test_unroot_still_root_after_budget_fails() -> None:
    t = FakeAdb(replies={"unroot": _ok("restarting adbd as non root\n")}, uid_seq=[_ok("0\n")])
    r = await unroot(t, timeout=0, poll_sec=0)
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "ADBD_RESTART_TIMEOUT"


UNROOTING = _ok("restarting adbd as non root\n")


@pytest.mark.asyncio
async def test_unroot_on_a_build_that_keeps_adbd_root_says_so_at_once() -> None:
    """adbd restarted (new pid) and came back as root: some builds decide
    adbd's uid themselves. That answer is final — no waiting out the budget,
    and no "reboot and retry", which changes nothing there."""
    t = FakeAdb(
        replies={"unroot": UNROOTING},
        uid_seq=[_ok("0\n")],
        pid_seq=[_ok("1200\n"), _ok("1388\n")],
    )
    r = await unroot(t, timeout=5, poll_sec=0.2)
    assert not r.ok and r.error is not None
    assert r.error.code == "ADB_UNROOT_NO_EFFECT"
    assert r.error.category == "device"
    assert "1200 → 1388" in r.error.message
    assert "Nothing to retry" in r.error.suggestion and "su 2000" in r.error.suggestion
    assert "Reboot" not in r.error.suggestion
    assert (r.error.details["adbd_pid_before"], r.error.details["adbd_pid_after"]) == (1200, 1388)
    # one answer, then the new adbd's own: its uid is final, polling on is wasted time
    assert t.uid_polls() == 2


@pytest.mark.asyncio
async def test_unroot_with_adbd_never_restarted_is_a_restart_timeout() -> None:
    t = FakeAdb(replies={"unroot": UNROOTING}, uid_seq=[_ok("0\n")], pid_seq=[_ok("1200\n")])
    r = await unroot(t, timeout=0, poll_sec=0)
    assert not r.ok and r.error is not None
    assert r.error.code == "ADBD_RESTART_TIMEOUT"
    assert "did not restart (pid 1200 unchanged" in r.error.message
    assert "Reboot" in r.error.suggestion


@pytest.mark.asyncio
async def test_root_waits_through_the_old_adbd_by_its_pid() -> None:
    """The old adbd (same pid) still answers 2000 for a moment: not final,
    the poll goes on until the new one answers 0."""
    t = FakeAdb(
        replies={"root": RESTARTING},
        uid_seq=[_ok("2000\n"), _ok("0\n")],
        pid_seq=[_ok("1200\n"), _ok("1200\n")],
    )
    r = await root(t, poll_sec=0)
    assert r.ok, r.error
    assert r.data is not None and r.data.outcome == "became_root" and r.data.polls == 2


@pytest.mark.asyncio
async def test_root_refused_by_a_restarted_adbd_ends_the_poll_at_once() -> None:
    t = FakeAdb(
        replies={"root": RESTARTING},
        uid_seq=[_ok("2000\n")],
        pid_seq=[_ok("1200\n"), _ok("1388\n")],
    )
    r = await root(t, timeout=5, poll_sec=0.2)
    assert not r.ok and r.error is not None
    assert r.error.code == "ADB_ROOT_REFUSED"
    assert "adbd restarted, pid 1200 → 1388" in r.error.message
    assert t.uid_polls() == 2


@pytest.mark.parametrize(
    "verb, stale, fresh, outcome",
    [(root, "2000", "0", "became_root"), (unroot, "0", "2000", "became_non_root")],
)
@pytest.mark.asyncio
async def test_a_restart_between_the_uid_and_pid_reads_is_not_misread(
    verb: Any, stale: str, fresh: str, outcome: str
) -> None:
    """The old adbd answers `id -u`, then restarts before `pidof` runs: the
    pid is new but that uid was the old daemon's. The new daemon is asked
    again and its answer stands — reporting a refusal here would be false."""
    reply = RESTARTING if verb is root else UNROOTING
    t = FakeAdb(
        replies={verb.__name__: reply},
        uid_seq=[_ok(stale + "\n"), _ok(fresh + "\n")],
        pid_seq=[_ok("1200\n"), _ok("1388\n")],
    )
    r = await verb(t, timeout=5, poll_sec=0.2)
    assert r.ok, r.error
    assert r.data is not None and r.data.outcome == outcome


@pytest.mark.parametrize("pidof", [_ok("1200 1201\n"), _ok(""), _err("error: closed")])
@pytest.mark.asyncio
async def test_an_unreadable_adbd_pid_falls_back_to_the_plain_report(pidof: ShellResult) -> None:
    """Two pids, nothing, or an error: whether adbd restarted is unknown, so
    neither "no effect" nor "never restarted" may be claimed."""
    t = FakeAdb(replies={"unroot": UNROOTING}, uid_seq=[_ok("0\n")], pid_seq=[pidof])
    r = await unroot(t, timeout=0, poll_sec=0)
    assert not r.ok and r.error is not None
    assert r.error.code == "ADBD_RESTART_TIMEOUT"
    assert "could not be read" in r.error.message


# ─── remount ───────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_remount_success_passes_output_verbatim() -> None:
    t = FakeAdb(replies={"remount": _ok("remount succeeded\n")})
    r = await remount(t)
    assert r.ok
    assert r.data is not None
    assert r.data.outcome == "remounted"
    assert r.data.output == "remount succeeded\n"
    assert t.calls == [["remount"]]


@pytest.mark.asyncio
async def test_remount_needs_reboot_even_with_nonzero_exit() -> None:
    # fs_mgr_remount exits MUST_REBOOT (11) on this path.
    out = "Disabling verity for /system\nNow reboot your device for settings to take effect\n"
    t = FakeAdb(replies={"remount": _ok(out, exit_code=11)})
    r = await remount(t)
    assert r.ok
    assert r.data is not None
    assert r.data.outcome == "needs_reboot"
    assert r.data.output == out


@pytest.mark.asyncio
async def test_remount_failure_is_reported_not_fixed() -> None:
    # Old adb exits 0 and leaves the bad news in the text.
    out = (
        "dm_verity is enabled on the system partition.\n"
        'Use "adb disable-verity" to disable verity.\n'
        "remount of the /system partition failed: Read-only file system\n"
    )
    t = FakeAdb(replies={"remount": _ok(out)})
    r = await remount(t)
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "ADB_REMOUNT_FAILED"
    assert "Read-only file system" in r.error.message
    assert "disable-verity" in r.error.suggestion
    assert "alb adb root" in r.error.suggestion
    # Suggested in text only — never issued.
    assert ["disable-verity"] not in t.calls
    assert t.calls == [["remount"]]


@pytest.mark.asyncio
async def test_remount_not_root_is_failure() -> None:
    t = FakeAdb(replies={"remount": _err('Not running as root. Try "adb root" first.')})
    r = await remount(t)
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "ADB_REMOUNT_FAILED"
    assert "adb root" in r.error.message


@pytest.mark.asyncio
async def test_remount_device_gone_keeps_transport_code() -> None:
    t = FakeAdb(replies={"remount": _err("error: device offline", "DEVICE_OFFLINE")})
    r = await remount(t)
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "DEVICE_OFFLINE"
