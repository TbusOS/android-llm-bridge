"""`alb serial break-in`: confirmation, argument parsing, and rendering.

The capability itself is covered in tests/capabilities/test_breakin.py; here
it is replaced by a recorder so the tests only look at what the command does
around it — asks before rebooting, turns escapes into bytes, shows the prompt
line on success and the tail on failure.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from alb.capabilities.breakin import BreakInResult
from alb.cli import serial_cli
from alb.cli.main import app
from alb.infra.result import ErrorInfo, Result, ok

runner = CliRunner()


class FakeSerial:
    name = "serial"


class FakeAdb:
    name = "adb"


def _stopped(log: Path) -> Result[BreakInResult]:
    return ok(
        data=BreakInResult(
            outcome="stopped_at_prompt",
            matched="Hit any key to stop autoboot",
            prompt_line="=>",
            sends=2,
            tail="U-Boot 2024.01\nHit any key to stop autoboot:  3\n=>",
            duration_ms=1234,
            reboot=None,
        ),
        artifacts=[log],
    )


def _never_seen(log: Path) -> Result[BreakInResult]:
    return Result(
        ok=False,
        error=ErrorInfo(
            code="BREAKIN_PATTERN_NOT_SEEN",
            message="no countdown matching 'Hit any key' appeared on the UART within 120s",
            suggestion="Read the tail",
            category="timeout",
            details={
                "outcome": "pattern_never_seen",
                "matched": None,
                "sends": 0,
                "tail": "BL31: v2.9\nStarting kernel ...\n[    0.000000] Booting Linux",
                "reboot": None,
                "artifact": str(log),
            },
        ),
        artifacts=[log],
    )


@pytest.fixture
def wired(monkeypatch, tmp_path: Path):
    """Replace the transports and the capability; return the recorder."""
    state: dict[str, Any] = {
        "calls": [],
        "transports": [],
        "result": _stopped(tmp_path / "attempt.log"),
    }
    serial = FakeSerial()
    adb = FakeAdb()

    def _force_serial(ctx, device):
        return serial

    def _get_transport(ctx, *, override=None, device_serial=None):
        state["transports"].append((override, device_serial))
        return adb

    async def _break_in(transport, **kw):
        state["calls"].append((transport, kw))
        if kw.get("on_ready"):
            kw["on_ready"]()
        return state["result"]

    monkeypatch.setattr(serial_cli, "_force_serial", _force_serial)
    monkeypatch.setattr(serial_cli, "get_transport", _get_transport)
    monkeypatch.setattr(serial_cli, "break_in", _break_in)
    state["serial"] = serial
    state["adb"] = adb
    return state


# ─── safety: --reboot asks ─────────────────────────────────────────


def test_reboot_requires_confirmation(wired) -> None:
    r = runner.invoke(app, ["serial", "break-in", "--reboot", "serial"], input="n\n")
    assert r.exit_code != 0
    assert "about to reboot the board" in r.output
    assert wired["calls"] == [], "declined — the board must not be touched"


def test_reboot_confirmed_with_y_runs(wired) -> None:
    r = runner.invoke(app, ["serial", "break-in", "--reboot", "serial"], input="y\n")
    assert r.exit_code == 0, r.output
    assert len(wired["calls"]) == 1
    assert wired["calls"][0][1]["reboot"] == "serial"


def test_yes_skips_the_question(wired) -> None:
    r = runner.invoke(app, ["serial", "break-in", "--reboot", "serial", "--yes"])
    assert r.exit_code == 0, r.output
    assert "proceed?" not in r.output
    assert wired["calls"][0][1]["reboot"] == "serial"


def test_without_reboot_nothing_is_asked(wired) -> None:
    """No --reboot = the operator resets by hand; there is nothing to confirm,
    and the command must not block on stdin."""
    r = runner.invoke(app, ["serial", "break-in"])
    assert r.exit_code == 0, r.output
    assert "proceed?" not in r.output
    kw = wired["calls"][0][1]
    assert kw["reboot"] is None and kw["reboot_transport"] is None


def test_reboot_adb_builds_the_adb_transport(wired) -> None:
    r = runner.invoke(
        app, ["serial", "break-in", "--reboot", "adb", "--adb-device", "ABC123", "--yes"]
    )
    assert r.exit_code == 0, r.output
    assert wired["transports"] == [("adb", "ABC123")]
    kw = wired["calls"][0][1]
    assert kw["reboot"] == "adb" and kw["reboot_transport"] is wired["adb"]


def test_reboot_serial_does_not_build_an_adb_transport(wired) -> None:
    r = runner.invoke(app, ["serial", "break-in", "--reboot", "serial", "--yes"])
    assert r.exit_code == 0, r.output
    assert wired["transports"] == []


# ─── arguments ─────────────────────────────────────────────────────


def test_send_escapes_reach_the_capability_as_bytes(wired) -> None:
    r = runner.invoke(app, ["serial", "break-in", "--send", "\\x03\\r"])
    assert r.exit_code == 0, r.output
    assert wired["calls"][0][1]["send"] == b"\x03\r"


def test_default_send_is_a_space(wired) -> None:
    runner.invoke(app, ["serial", "break-in"])
    assert wired["calls"][0][1]["send"] == b" "


def test_bad_send_escape_is_a_usage_error(wired) -> None:
    r = runner.invoke(app, ["serial", "break-in", "--send", "\\q"])
    assert r.exit_code == 2
    assert "unknown escape" in r.output
    assert wired["calls"] == []


def test_bad_reboot_mode_is_a_usage_error(wired) -> None:
    r = runner.invoke(app, ["serial", "break-in", "--reboot", "usb"])
    assert r.exit_code == 2
    assert "adb, serial" in r.output
    assert wired["calls"] == []


def test_options_are_passed_through(wired) -> None:
    runner.invoke(
        app,
        [
            "serial",
            "break-in",
            "--expect",
            "autoboot in",
            "--prompt",
            r"boot>\s*$",
            "--timeout",
            "30",
            "--repeat-ms",
            "20",
            "--prompt-wait",
            "5",
        ],
    )
    kw = wired["calls"][0][1]
    assert kw["expect"] == "autoboot in"
    assert kw["prompt"] == r"boot>\s*$"
    assert kw["timeout_s"] == 30
    assert kw["repeat_ms"] == 20
    assert kw["prompt_wait_s"] == 5


# ─── rendering ─────────────────────────────────────────────────────


def test_success_prints_prompt_log_path_and_next_steps(wired, tmp_path: Path) -> None:
    r = runner.invoke(app, ["serial", "break-in"])
    assert r.exit_code == 0, r.output
    assert "[ok] stopped at the bootloader prompt after 2 keypress(es)" in r.output
    assert "'=>'" in r.output
    assert str(tmp_path / "attempt.log") in r.output
    assert "alb serial shell" in r.output and "alb serial send" in r.output


def test_manual_reset_hint_is_printed_once_the_reader_is_up(wired) -> None:
    r = runner.invoke(app, ["serial", "break-in"])
    assert "reset or power-cycle the board now" in r.output


def test_pattern_never_seen_prints_tail_and_exits_1(wired, tmp_path: Path) -> None:
    wired["result"] = _never_seen(tmp_path / "attempt.log")
    r = runner.invoke(app, ["serial", "break-in"])
    assert r.exit_code == 1
    assert "BREAKIN_PATTERN_NOT_SEEN" in r.output
    assert "Starting kernel ..." in r.output and "Booting Linux" in r.output
    assert str(tmp_path / "attempt.log") in r.output


def test_json_success(wired, tmp_path: Path) -> None:
    r = runner.invoke(app, ["--json", "serial", "break-in"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.stdout)
    assert payload["ok"] is True
    assert payload["data"]["outcome"] == "stopped_at_prompt"
    assert payload["data"]["prompt_line"] == "=>"
    assert payload["artifacts"] == [str(tmp_path / "attempt.log")]


def test_json_failure_carries_tail_and_exits_1(wired, tmp_path: Path) -> None:
    wired["result"] = _never_seen(tmp_path / "attempt.log")
    r = runner.invoke(app, ["--json", "serial", "break-in"])
    assert r.exit_code == 1
    payload = json.loads(r.stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "BREAKIN_PATTERN_NOT_SEEN"
    assert "Starting kernel" in payload["error"]["details"]["tail"]
