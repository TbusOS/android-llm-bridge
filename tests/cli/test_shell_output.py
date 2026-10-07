"""`alb shell` / `alb serial shell` must print device output byte-for-byte.

Before the fix the output went through the rich pretty printer, which
(1) dropped "[false]"-like text as markup tags, (2) inserted newlines at
80 columns when stdout was not a tty, and (3) hid stdout when the device
command exited non-zero. Each test below fails on the old code.
"""

from __future__ import annotations

import io
import json

from rich.console import Console
from typer.testing import CliRunner

from alb.cli import common
from alb.cli.main import app
from alb.infra.permissions import PermissionResult
from alb.transport.base import ShellResult

runner = CliRunner()

BRACKETS = "a=[false] b=[orange] c=[1] d=[_a]\n"
LONG = "k" * 200 + "\n"


class FakeTransport:
    name = "fake"

    def __init__(self, result: ShellResult, behavior: str = "allow") -> None:
        self._result = result
        self._behavior = behavior

    async def check_permissions(self, action, input_data):  # noqa: ANN001
        return PermissionResult(behavior=self._behavior, reason="blocked by test")

    async def shell(self, cmd, timeout=30):  # noqa: ANN001
        return self._result


def _use(monkeypatch, result: ShellResult, behavior: str = "allow") -> None:
    fake = FakeTransport(result, behavior)
    monkeypatch.setattr("alb.cli.main.get_transport", lambda *a, **k: fake)
    monkeypatch.setattr("alb.cli.serial_cli._force_serial", lambda *a, **k: fake)


def test_shell_stdout_is_byte_exact(monkeypatch) -> None:
    _use(monkeypatch, ShellResult(ok=True, stdout=BRACKETS + LONG, stderr=""))
    r = runner.invoke(app, ["shell", "x"])
    assert r.exit_code == 0
    assert r.stdout == BRACKETS + LONG  # no label, no lost "[false]", no wrap
    assert r.stderr == ""


def test_serial_shell_stdout_is_byte_exact(monkeypatch) -> None:
    _use(monkeypatch, ShellResult(ok=True, stdout=BRACKETS + LONG))
    r = runner.invoke(app, ["serial", "shell", "x"])
    assert r.exit_code == 0
    assert r.stdout == BRACKETS + LONG


def test_serial_shell_restores_last_newline(monkeypatch) -> None:
    # serial transport hands back lines without the final "\n"
    _use(monkeypatch, ShellResult(ok=True, stdout="s=[false]\nt"))
    r = runner.invoke(app, ["serial", "shell", "x"])
    assert r.stdout == "s=[false]\nt\n"


def test_adb_shell_does_not_add_newline(monkeypatch) -> None:
    # `printf x` on the device: no newline there, none added here
    _use(monkeypatch, ShellResult(ok=True, stdout="x"))
    r = runner.invoke(app, ["shell", "x"])
    assert r.stdout == "x"


def test_shell_nonzero_exit_keeps_output_and_code(monkeypatch) -> None:
    # e.g. `grep -c nothing file` prints "0" and exits 1 on the device
    _use(
        monkeypatch,
        ShellResult(
            ok=False,
            exit_code=3,
            stdout="0\n",
            stderr="warn [x]\n",
            error_code="ADB_COMMAND_FAILED",
        ),
    )
    r = runner.invoke(app, ["shell", "x"])
    assert r.exit_code == 3
    assert r.stdout == "0\n"
    assert r.stderr == "warn [x]\n"


def test_shell_alb_failure_says_why_and_exits_1(monkeypatch) -> None:
    _use(
        monkeypatch,
        ShellResult(
            ok=False,
            exit_code=-1,
            stderr="adb command timed out after 30s",
            error_code="TIMEOUT_SHELL",
        ),
    )
    r = runner.invoke(app, ["shell", "x"])
    assert r.exit_code == 1
    assert r.stdout == ""
    assert "TIMEOUT_SHELL" in r.stderr and "timed out after 30s" in r.stderr


def test_shell_timeout_keeps_partial_stdout(monkeypatch) -> None:
    # serial transport returns what arrived before the timeout
    _use(
        monkeypatch,
        ShellResult(
            ok=False,
            exit_code=-1,
            stdout="partial [false]\n",
            stderr="no prompt within 30s",
            error_code="TIMEOUT_SHELL",
        ),
    )
    r = runner.invoke(app, ["serial", "shell", "x"])
    assert r.exit_code == 1
    assert r.stdout == "partial [false]\n"
    assert "TIMEOUT_SHELL" in r.stderr


def test_shell_permission_denied_exits_1(monkeypatch) -> None:
    _use(monkeypatch, ShellResult(ok=True, stdout="never"), behavior="deny")
    r = runner.invoke(app, ["shell", "x"])
    assert r.exit_code == 1
    assert r.stdout == ""
    assert "PERMISSION_DENIED" in r.stderr


def test_shell_json_mode_unchanged(monkeypatch) -> None:
    _use(monkeypatch, ShellResult(ok=True, stdout=BRACKETS))
    r = runner.invoke(app, ["--json", "shell", "x"])
    assert r.exit_code == 0
    payload = json.loads(r.stdout)
    assert payload["ok"] is True
    assert payload["data"]["stdout"] == BRACKETS


def test_pretty_printer_keeps_brackets_and_does_not_wrap(monkeypatch) -> None:
    buf = io.StringIO()
    monkeypatch.setattr(common, "console", Console(file=buf, width=80))
    value = "[false] " + "x" * 150
    common._print_data_pretty({"v": value})
    out = buf.getvalue()
    assert value in out
    assert out.count("\n") == 1
