"""Tests for `pstore_pull` (previous boot's crash record) and its CLI rendering.

The fake transport answers `shell` by command prefix and `pull` from an
in-memory map of remote files, and records every shell command so the test
can prove nothing was deleted on the device.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from alb.capabilities.diagnose import (
    _first_panic_line,
    _parse_ls_l,
    pstore_pull,
)
from alb.cli import diagnose_cli
from alb.infra.permissions import PermissionResult
from alb.transport.base import ShellResult


def _ok(stdout: str = "") -> ShellResult:
    return ShellResult(ok=True, exit_code=0, stdout=stdout, duration_ms=2)


def _err(stderr: str, code: str = "ADB_COMMAND_FAILED") -> ShellResult:
    return ShellResult(ok=False, exit_code=1, stderr=stderr, error_code=code, duration_ms=2)


class FakeTransport:
    name = "adb"

    def __init__(
        self,
        shell_responses: dict[str, ShellResult],
        remote_files: dict[str, bytes] | None = None,
        pull_errors: dict[str, str] | None = None,
    ) -> None:
        self.shell_responses = shell_responses
        self.remote_files = remote_files or {}
        self.pull_errors = pull_errors or {}
        self.shell_cmds: list[str] = []
        self.pulled: list[str] = []

    async def check_permissions(self, action: str, input_data: dict[str, Any]) -> PermissionResult:
        return PermissionResult(behavior="allow")

    async def shell(self, cmd: str, **kw: Any) -> ShellResult:
        self.shell_cmds.append(cmd)
        for prefix, result in self.shell_responses.items():
            if cmd.startswith(prefix):
                return result
        return _err("unhandled in test")

    async def pull(self, remote: str, local: Path) -> ShellResult:
        self.pulled.append(remote)
        if remote in self.pull_errors:
            return _err(self.pull_errors[remote])
        if remote not in self.remote_files:
            return _err(f"adb: error: remote object '{remote}' does not exist")
        _put(local, self.remote_files[remote])
        return _ok()


def _put(local: Path, data: bytes) -> None:
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(data)


PSTORE_LS = (
    "total 0\n"
    "-r--r--r-- 1 root root 131072 2026-01-01 00:00 console-ramoops-0\n"
    "-r--r--r-- 1 root root  65536 2026-01-01 00:00 dmesg-ramoops-0\n"
    "-r--r--r-- 1 root root  16384 2026-01-01 00:00 pmsg-ramoops-0\n"
)
CONSOLE = (
    b"[   12.001] init: starting service 'x'\n"
    b"[   13.500] Kernel panic - not syncing: Fatal exception in interrupt\n"
    b"[   13.501] CPU: 0 PID: 1 Comm: init\n"
)
DMESG_RAMOOPS = b"Panic#1 Part1\n<6>[    0.000] Booting Linux\n"
PMSG = b"\x00\x00garbage\xff\n"


def _pstore_fake(**kw: Any) -> FakeTransport:
    return FakeTransport(
        {"ls -l /sys/fs/pstore": _ok(PSTORE_LS)},
        remote_files={
            "/sys/fs/pstore/console-ramoops-0": CONSOLE,
            "/sys/fs/pstore/dmesg-ramoops-0": DMESG_RAMOOPS,
            "/sys/fs/pstore/pmsg-ramoops-0": PMSG,
        },
        **kw,
    )


# ─── pstore present ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_pstore_files_pulled_and_panic_line_found(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    t = _pstore_fake()
    r = await pstore_pull(t, device="abc")
    assert r.ok
    d = r.data
    assert d is not None
    assert d.source == "pstore"
    assert d.pstore_present and not d.last_kmsg_present
    assert [f.name for f in d.files] == ["console-ramoops-0", "dmesg-ramoops-0", "pmsg-ramoops-0"]
    assert [f.size for f in d.files] == [131072, 65536, 16384]  # from ls, not local
    by_name = {f.name: f for f in d.files}
    assert by_name["console-ramoops-0"].first_panic_line == (
        "[   13.500] Kernel panic - not syncing: Fatal exception in interrupt"
    )
    assert by_name["dmesg-ramoops-0"].first_panic_line is None
    assert by_name["pmsg-ramoops-0"].first_panic_line is None  # binary junk: no raise
    assert d.saved_dir is not None
    assert Path(d.saved_dir).is_relative_to(tmp_path / "devices" / "abc" / "pstore")
    assert len(r.artifacts) == 3
    for p in r.artifacts:
        assert p.exists()
    # The record must survive the read: no rm, no truncation, nothing written.
    assert not any(c.lstrip().startswith(("rm", ">", "echo")) for c in t.shell_cmds)
    assert sorted(t.pulled) == sorted(t.remote_files)


@pytest.mark.asyncio
async def test_pstore_one_pull_failure_keeps_the_rest(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    t = _pstore_fake(pull_errors={"/sys/fs/pstore/pmsg-ramoops-0": "remote open failed"})
    r = await pstore_pull(t, device="abc")
    assert r.ok
    assert r.data is not None
    by_name = {f.name: f for f in r.data.files}
    assert by_name["pmsg-ramoops-0"].local_path == ""
    assert by_name["pmsg-ramoops-0"].error == "remote open failed"
    assert by_name["console-ramoops-0"].local_path
    assert len(r.artifacts) == 2


# ─── denied ────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_pstore_listing_denied_says_needs_root() -> None:
    t = FakeTransport({"ls -l /sys/fs/pstore": _err("ls: /sys/fs/pstore: Permission denied")})
    r = await pstore_pull(t, device="abc")
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "ROOT_REQUIRED"
    assert r.error.category == "permission"
    assert "needs root" in r.error.suggestion
    assert "alb adb root" in r.error.suggestion
    assert t.pulled == []


@pytest.mark.asyncio
async def test_pstore_device_offline_is_not_needs_root() -> None:
    t = FakeTransport({"ls -l /sys/fs/pstore": _err("error: device offline", "DEVICE_OFFLINE")})
    r = await pstore_pull(t, device="abc")
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "DEVICE_OFFLINE"


# ─── fallbacks ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_empty_pstore_falls_back_to_last_kmsg(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    last_kmsg = b"<4>[  99.1] Internal error: Oops: 96000005 [#1] PREEMPT SMP\n"
    t = FakeTransport(
        {
            "ls -l /sys/fs/pstore": _ok("total 0\n"),
            "ls /proc/last_kmsg": _ok("/proc/last_kmsg\n"),
        },
        remote_files={"/proc/last_kmsg": last_kmsg},
    )
    r = await pstore_pull(t, device="abc")
    assert r.ok
    d = r.data
    assert d is not None
    assert d.source == "last_kmsg"
    assert d.pstore_present and d.last_kmsg_present
    assert "pstore empty" in d.note and "last_kmsg present" in d.note
    assert len(d.files) == 1
    assert d.files[0].name == "last_kmsg"
    assert d.files[0].size == len(last_kmsg)  # proc reports 0 in ls; sized after pull
    assert d.files[0].first_panic_line is not None
    assert d.files[0].first_panic_line.startswith("<4>[  99.1] Internal error: Oops")
    assert r.artifacts == [Path(d.files[0].local_path)]


@pytest.mark.asyncio
async def test_absent_pstore_falls_back_to_last_kmsg(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    t = FakeTransport(
        {
            "ls -l /sys/fs/pstore": _err("ls: /sys/fs/pstore: No such file or directory"),
            "ls /proc/last_kmsg": _ok("/proc/last_kmsg\n"),
        },
        remote_files={"/proc/last_kmsg": b"Watchdog bark! now = 123\n"},
    )
    r = await pstore_pull(t, device="abc")
    assert r.ok
    assert r.data is not None
    assert r.data.source == "last_kmsg"
    assert not r.data.pstore_present
    assert "pstore absent" in r.data.note
    assert r.data.files[0].first_panic_line == "Watchdog bark! now = 123"


@pytest.mark.asyncio
async def test_nothing_at_all_is_a_clear_answer(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    t = FakeTransport(
        {
            "ls -l /sys/fs/pstore": _err("ls: /sys/fs/pstore: No such file or directory"),
            "ls /proc/last_kmsg": _err("ls: /proc/last_kmsg: No such file or directory"),
        }
    )
    r = await pstore_pull(t, device="abc")
    assert r.ok  # the device answered; "no record" is a finding, not a failure
    d = r.data
    assert d is not None
    assert d.source == "none"
    assert not d.pstore_present and not d.last_kmsg_present
    assert d.files == [] and d.saved_dir is None and r.artifacts == []
    assert "/sys/fs/pstore absent" in d.note and "/proc/last_kmsg absent" in d.note
    assert not (tmp_path / "devices" / "abc" / "pstore").exists() or not any(
        (tmp_path / "devices" / "abc" / "pstore").iterdir()
    )


@pytest.mark.asyncio
async def test_last_kmsg_denied_says_needs_root(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    t = FakeTransport(
        {
            "ls -l /sys/fs/pstore": _err("ls: /sys/fs/pstore: No such file or directory"),
            "ls /proc/last_kmsg": _ok("/proc/last_kmsg\n"),
        },
        pull_errors={"/proc/last_kmsg": "adb: error: failed to copy: Permission denied"},
    )
    r = await pstore_pull(t, device="abc")
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "ROOT_REQUIRED"


@pytest.mark.asyncio
async def test_pstore_listed_but_every_pull_denied_says_needs_root(monkeypatch, tmp_path) -> None:
    """Some builds let the shell user list pstore but not read the files.
    Zero records pulled must not come back as an ok with no artifacts —
    that reads as "no crash"."""
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    denied = "adb: error: failed to copy: Permission denied"
    t = _pstore_fake(
        pull_errors={
            f"/sys/fs/pstore/{n}": denied
            for n in ("console-ramoops-0", "dmesg-ramoops-0", "pmsg-ramoops-0")
        }
    )
    r = await pstore_pull(t, device="abc")
    assert not r.ok and r.error is not None
    assert r.error.code == "ROOT_REQUIRED"
    assert "console-ramoops-0" in r.error.message


@pytest.mark.asyncio
async def test_pstore_listed_but_every_pull_failed_otherwise(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    t = _pstore_fake(
        pull_errors={
            f"/sys/fs/pstore/{n}": "remote open failed"
            for n in ("console-ramoops-0", "dmesg-ramoops-0", "pmsg-ramoops-0")
        }
    )
    r = await pstore_pull(t, device="abc")
    assert not r.ok and r.error is not None
    assert r.error.code == "PSTORE_PULL_FAILED"


@pytest.mark.asyncio
async def test_last_kmsg_pull_failure_is_not_ok(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    t = FakeTransport(
        {
            "ls -l /sys/fs/pstore": _err("ls: /sys/fs/pstore: No such file or directory"),
            "ls /proc/last_kmsg": _ok("/proc/last_kmsg\n"),
        },
        pull_errors={"/proc/last_kmsg": "adb: error: connection reset"},
    )
    r = await pstore_pull(t, device="abc")
    assert not r.ok and r.error is not None
    assert r.error.code == "PSTORE_PULL_FAILED"


def test_first_panic_line_prefers_a_real_panic_over_an_earlier_watchdog_line() -> None:
    """A previous boot's console log carries benign `watchdog:` init lines
    long before the crash; the panic line is the one worth showing."""
    text = (
        "[    1.200] watchdog: watchdog0: watchdog did not stop!\n"
        "[   13.500] Kernel panic - not syncing: Fatal exception\n"
    )
    assert _first_panic_line(text) == "[   13.500] Kernel panic - not syncing: Fatal exception"


# pmsg as a real board left it: binary logger entries (header bytes, a tag,
# a NUL, the message). The second one reads like a crash marker to a text scan.
PMSG_ENTRIES = (
    b"\x00l\xef\x00\xa1\x03j\x01\x03\x9c\x04\x15\x04keystore2\x00"
    b"system/security/keystore2/src/watchdog.rs:319 - Watchdog thread idle -> terminating.\x00"
    b"\x00l\xef\x00\xa2\x03\x04init\x00Kernel panic test string in a log message\x00"
)


def _bytes(path: str) -> bytes:
    return Path(path).read_bytes()


@pytest.mark.asyncio
async def test_a_pmsg_record_is_not_scanned_for_kernel_crash_lines(monkeypatch, tmp_path) -> None:
    """pmsg holds the previous boot's logcat, not kernel text. On a real
    board the scan found keystore's "Watchdog thread idle" and showed it,
    entry-header bytes and all, as the crash."""
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    t = _pstore_fake()
    t.remote_files["/sys/fs/pstore/pmsg-ramoops-0"] = PMSG_ENTRIES
    r = await pstore_pull(t, device="abc")
    assert r.ok and r.data is not None
    pmsg = next(f for f in r.data.files if f.name == "pmsg-ramoops-0")
    assert (pmsg.kind, pmsg.first_panic_line) == ("logcat", None)
    assert pmsg.to_dict()["kind"] == "logcat"
    assert _bytes(pmsg.local_path) == PMSG_ENTRIES, "pulled as is, only not scanned"
    console = next(f for f in r.data.files if f.name == "console-ramoops-0")
    assert console.kind == "kernel" and console.first_panic_line is not None


def test_control_and_undecodable_bytes_are_dropped_from_the_marker_line() -> None:
    text = "[  1.0] ok\n\x1b[2J\x00\ufffd[ 13.5] Kernel panic - not syncing\x07\ufffd\n"
    assert _first_panic_line(text) == "[2J[ 13.5] Kernel panic - not syncing"


# ─── parsers ───────────────────────────────────────────────────────
def test_parse_ls_l_toybox_and_busybox_and_hostile_names() -> None:
    out = (
        "total 8\n"
        "-r--r--r-- 1 root root 131072 2026-01-01 00:00 console-ramoops-0\n"
        "-r--r--r--    1 root     root         4096 Jan  1 00:00 dmesg-ramoops-1\n"
        "-r--r--r-- 1 root root 12 2026-01-01 00:00 ../../etc/evil\n"
        "garbage line\n"
    )
    assert _parse_ls_l(out) == [
        ("console-ramoops-0", 131072),
        ("dmesg-ramoops-1", 4096),
        ("evil", 12),
    ]
    assert _parse_ls_l("") == []
    assert _parse_ls_l("total 0\n") == []


@pytest.mark.parametrize(
    "line",
    [
        "Kernel panic - not syncing: Attempted to kill init!",
        "Internal error: Oops: 96000005 [#1] PREEMPT SMP",
        "Unable to handle kernel NULL pointer dereference at virtual address 0000",
        "watchdog: BUG: soft lockup - CPU#1 stuck for 22s!",
        "Watchdog bite received from secure side",
    ],
)
def test_first_panic_line_matches_every_marker(line: str) -> None:
    text = "[  1.0] boot ok\n" + line + "\n[  2.0] after\n"
    assert _first_panic_line(text) == line


def test_first_panic_line_none_when_clean() -> None:
    assert _first_panic_line("init: starting\nBooting Linux\n") is None


# ─── CLI rendering (alb diag pstore) ───────────────────────────────
# Lives here rather than in a new tests/cli file: the rendering is the only
# CLI surface pstore adds and it is tested end-to-end against the same fake.
runner = CliRunner()


def test_cli_pstore_summary_names_the_panic(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    fake = _pstore_fake()
    monkeypatch.setattr("alb.cli.diagnose_cli.get_transport", lambda *a, **k: fake)
    r = runner.invoke(diagnose_cli.app, ["pstore", "--device", "abc"])
    assert r.exit_code == 0, r.output
    assert "source: pstore" in r.output
    assert "console-ramoops-0" in r.output and "131072 B" in r.output
    assert "Kernel panic - not syncing" in r.output
    assert "(no panic marker)" in r.output
    assert "binary logcat of the previous boot" in r.output and "logcat -L" in r.output
    assert "saved:" in r.output


def test_cli_pstore_denied_tells_how_to_fix(monkeypatch) -> None:
    fake = FakeTransport({"ls -l /sys/fs/pstore": _err("ls: /sys/fs/pstore: Permission denied")})
    monkeypatch.setattr("alb.cli.diagnose_cli.get_transport", lambda *a, **k: fake)
    r = runner.invoke(diagnose_cli.app, ["pstore"])
    assert r.exit_code == 1
    assert "ROOT_REQUIRED" in r.output
    assert "alb adb root" in r.output


def test_cli_pstore_json(monkeypatch, tmp_path) -> None:
    import json

    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    fake = _pstore_fake()
    monkeypatch.setattr("alb.cli.diagnose_cli.get_transport", lambda *a, **k: fake)
    r = runner.invoke(diagnose_cli.app, ["pstore", "--device", "abc"], obj={"json": True})
    assert r.exit_code == 0, r.output
    doc = json.loads(r.output)
    assert doc["ok"] is True
    assert doc["data"]["source"] == "pstore"
    assert len(doc["data"]["files"]) == 3
    assert len(doc["artifacts"]) == 3
