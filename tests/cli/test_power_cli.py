"""`alb power reboot`: when adb finds no device and nothing named the
transport or device, the reboot goes over serial — and adb's error is still
printed, because someone has to find out why adb sees nothing (2026-10-23)."""

from __future__ import annotations

from typer.testing import CliRunner

from alb.cli.main import app
from alb.infra.permissions import PermissionResult
from alb.transport.base import ShellResult

runner = CliRunner()

NOT_FOUND = ShellResult(
    ok=False, exit_code=1, stderr="error: no devices/emulators found",
    error_code="DEVICE_NOT_FOUND",
)


class _Fake:
    def __init__(self, name: str, result: ShellResult) -> None:
        self.name = name
        self.result = result
        self.calls: list[str] = []

    async def check_permissions(self, action, input_data):
        return PermissionResult(behavior="allow")

    async def reboot(self, mode: str = "normal") -> ShellResult:
        self.calls.append(mode)
        return self.result


def _use(monkeypatch, *, serial: _Fake | None) -> _Fake:
    adb = _Fake("adb", NOT_FOUND)
    monkeypatch.setattr("alb.cli.power_cli.get_transport", lambda *a, **k: adb)
    seen: dict = {}

    def fallback_for(**kw):
        seen.update(kw)
        return serial

    monkeypatch.setattr("alb.cli.power_cli.serial_fallback_for", fallback_for)
    adb.seen_fallback_args = seen  # type: ignore[attr-defined]
    return adb


def test_reboot_falls_back_and_prints_adb_error(monkeypatch) -> None:
    ser = _Fake("serial", ShellResult(ok=True))
    _use(monkeypatch, serial=ser)
    r = runner.invoke(app, ["power", "reboot", "bootloader", "--no-wait-boot"])
    assert r.exit_code == 0, r.output
    assert ser.calls == ["bootloader"]
    assert "found no device" in r.stderr
    assert "DEVICE_NOT_FOUND" in r.stderr
    assert "no devices/emulators found" in r.stderr


def test_reboot_without_fallback_fails_with_adb_error(monkeypatch) -> None:
    adb = _use(monkeypatch, serial=None)
    r = runner.invoke(
        app, ["power", "reboot", "bootloader", "--no-wait-boot", "--device", "abc123"]
    )
    assert r.exit_code == 1
    assert "DEVICE_NOT_FOUND" in r.stdout  # print_result reports errors on stdout
    assert "found no device" not in r.stderr
    # The CLI passes what the user chose, so the factory can say no.
    assert adb.seen_fallback_args["device_serial"] == "abc123"
