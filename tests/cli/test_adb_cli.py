"""`alb adb root | unroot | remount` — the CLI renders what the capability says.

The sub-app is invoked standalone (main.py mounts it as `alb adb`), so
`ctx.obj` is None in the plain case and injected via `obj=` for --json.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from typer.testing import CliRunner

from alb.cli import adb_cli
from alb.infra.permissions import PermissionResult
from alb.transport.base import ShellResult

runner = CliRunner()


def _ok(stdout: str = "", exit_code: int = 0) -> ShellResult:
    return ShellResult(ok=exit_code == 0, exit_code=exit_code, stdout=stdout, duration_ms=1)


class FakeAdb:
    name = "adb"

    def __init__(self, replies: dict[str, ShellResult], uid: str) -> None:
        self.replies = replies
        self.uid = uid
        self.calls: list[list[str]] = []

    async def check_permissions(self, action: str, input_data: dict[str, Any]) -> PermissionResult:
        return PermissionResult(behavior="allow")

    async def _run(self, args: list[str], **kw: Any) -> ShellResult:
        self.calls.append(list(args))
        return self.replies[args[0]]

    async def shell(self, cmd: str, **kw: Any) -> ShellResult:
        self.calls.append(["shell", cmd])
        return _ok(self.uid + "\n")


def _use(monkeypatch: pytest.MonkeyPatch, fake: FakeAdb) -> None:
    monkeypatch.setattr("alb.cli.adb_cli.get_transport", lambda *a, **k: fake)


def test_root_prints_outcome(monkeypatch) -> None:
    fake = FakeAdb({"root": _ok("restarting adbd as root\n")}, uid="0")
    _use(monkeypatch, fake)
    r = runner.invoke(adb_cli.app, ["root"])
    assert r.exit_code == 0, r.output
    assert "became_root" in r.output
    assert "restarting adbd as root" in r.output
    assert fake.calls[0] == ["root"]


def test_root_already_root(monkeypatch) -> None:
    _use(monkeypatch, FakeAdb({"root": _ok("adbd is already running as root\n")}, uid="0"))
    r = runner.invoke(adb_cli.app, ["root"])
    assert r.exit_code == 0, r.output
    assert "already_root" in r.output


def test_root_refused_shows_device_words_and_su_hint(monkeypatch) -> None:
    _use(
        monkeypatch,
        FakeAdb({"root": _ok("adbd cannot run as root in production builds\n")}, uid="2000"),
    )
    r = runner.invoke(adb_cli.app, ["root"])
    assert r.exit_code == 1
    assert "ADB_ROOT_REFUSED" in r.output
    assert "adbd cannot run as root in production builds" in r.output
    assert "su 0 " in r.output


def test_root_json(monkeypatch) -> None:
    _use(monkeypatch, FakeAdb({"root": _ok("restarting adbd as root\n")}, uid="0"))
    r = runner.invoke(adb_cli.app, ["root", "--timeout", "3"], obj={"json": True})
    assert r.exit_code == 0, r.output
    doc = json.loads(r.output)
    assert doc["ok"] is True
    assert doc["data"]["outcome"] == "became_root"
    assert doc["data"]["uid"] == 0


def test_unroot_prints_outcome(monkeypatch) -> None:
    fake = FakeAdb({"unroot": _ok("restarting adbd as non root\n")}, uid="2000")
    _use(monkeypatch, fake)
    r = runner.invoke(adb_cli.app, ["unroot"])
    assert r.exit_code == 0, r.output
    assert "became_non_root" in r.output
    assert fake.calls[0] == ["unroot"]


def test_remount_success_verbatim(monkeypatch) -> None:
    _use(monkeypatch, FakeAdb({"remount": _ok("remount succeeded\n")}, uid="0"))
    r = runner.invoke(adb_cli.app, ["remount"])
    assert r.exit_code == 0, r.output
    assert "remounted" in r.output
    assert "remount succeeded" in r.output


def test_remount_needs_reboot(monkeypatch) -> None:
    out = "Now reboot your device for settings to take effect\n"
    _use(monkeypatch, FakeAdb({"remount": _ok(out, exit_code=11)}, uid="0"))
    r = runner.invoke(adb_cli.app, ["remount"])
    assert r.exit_code == 0, r.output
    assert "needs_reboot" in r.output
    assert "Now reboot your device" in r.output


def test_remount_failure_exits_1_and_never_disables_verity(monkeypatch) -> None:
    fake = FakeAdb(
        {"remount": _ok("remount of the /system partition failed: Read-only file system\n")},
        uid="0",
    )
    _use(monkeypatch, fake)
    r = runner.invoke(adb_cli.app, ["remount"])
    assert r.exit_code == 1
    assert "ADB_REMOUNT_FAILED" in r.output
    assert "Read-only file system" in r.output
    assert "disable-verity" in r.output  # suggested...
    assert fake.calls == [["remount"]]  # ...never run


def test_device_flag_reaches_get_transport(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_get_transport(ctx: Any, *, device_serial: str | None = None, **kw: Any) -> FakeAdb:
        seen["device"] = device_serial
        return FakeAdb({"remount": _ok("remount succeeded\n")}, uid="0")

    monkeypatch.setattr("alb.cli.adb_cli.get_transport", fake_get_transport)
    r = runner.invoke(adb_cli.app, ["remount", "--device", "serial-1"])
    assert r.exit_code == 0, r.output
    assert seen["device"] == "serial-1"
