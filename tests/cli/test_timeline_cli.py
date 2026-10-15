"""`alb timeline capture` / `show` — flag wiring and plain-text rendering.

The app is invoked directly (not through `alb.cli.main`) so these tests do
not depend on the mount line in main.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from alb.cli import timeline_cli
from alb.cli.timeline_cli import app
from alb.infra.result import fail, ok

runner = CliRunner()


# ─── capture ───────────────────────────────────────────────────────
@pytest.fixture
def fake_capture(monkeypatch, tmp_path: Path) -> dict:
    """Replace the capability and the transport factory; record what the
    command passed to them."""
    seen: dict = {"kwargs": None, "transports": []}

    from alb.capabilities.timeline import TimelineSummary

    async def capture(duration_s, **kwargs):
        seen["duration"] = duration_s
        seen["kwargs"] = kwargs
        return ok(
            data=TimelineSummary(
                directory=tmp_path / "cap",
                sources=sorted(kwargs["sources"]),
                lines={s: i for i, s in enumerate(sorted(kwargs["sources"]), start=1)},
                notes=["[alb] dmesg unavailable: stream ended with no output"],
                duration_captured_ms=1234,
            ),
            artifacts=[tmp_path / "cap" / "timeline.jsonl"],
        )

    def get_transport(ctx, *, override=None, device_serial=None):
        seen["transports"].append((override, device_serial))
        if override == "serial" and seen.get("serial_fails"):
            raise typer.BadParameter("SSH transport needs a host")
        return object()

    monkeypatch.setattr(timeline_cli, "capture_timeline", capture)
    monkeypatch.setattr(timeline_cli, "get_transport", get_transport)
    return seen


def test_capture_passes_duration_sources_and_both_transports(fake_capture) -> None:
    r = runner.invoke(app, ["capture", "-d", "7", "--device", "abc", "--logcat-filter", "*:E"])
    assert r.exit_code == 0, r.output
    assert fake_capture["duration"] == 7
    kw = fake_capture["kwargs"]
    assert kw["sources"] == {"logcat", "dmesg", "uart"}
    assert kw["device"] == "abc" and kw["logcat_filter"] == "*:E"
    assert kw["transport"] is not None and kw["serial"] is not None
    assert fake_capture["transports"] == [(None, "abc"), ("serial", None)]
    assert kw["output"] is None  # default: workspace
    # prints the directory and one count per source, plus the notes
    assert "cap" in r.output
    assert "logcat" in r.output and "uart" in r.output and "dmesg" in r.output
    assert "[alb] dmesg unavailable" in r.output
    assert "alb timeline show" in r.output


def test_no_flags_drop_sources_and_skip_unneeded_transports(fake_capture) -> None:
    r = runner.invoke(app, ["capture", "-d", "3", "--no-dmesg", "--no-uart"])
    assert r.exit_code == 0, r.output
    assert fake_capture["kwargs"]["sources"] == {"logcat"}
    assert fake_capture["kwargs"]["serial"] is None
    assert fake_capture["transports"] == [(None, None)]  # serial never built


def test_only_uart_does_not_build_an_adb_transport(fake_capture) -> None:
    r = runner.invoke(app, ["capture", "-d", "3", "--no-logcat", "--no-dmesg"])
    assert r.exit_code == 0, r.output
    assert fake_capture["kwargs"]["transport"] is None
    assert fake_capture["transports"] == [("serial", None)]


def test_serial_that_cannot_be_built_is_a_note_not_an_abort(fake_capture) -> None:
    fake_capture["serial_fails"] = True
    r = runner.invoke(app, ["capture", "-d", "3"])
    assert r.exit_code == 0, r.output
    assert fake_capture["kwargs"]["serial"] is None
    assert "uart: " in r.output and "needs a host" in r.output


def test_all_sources_off_is_a_usage_error(fake_capture) -> None:
    r = runner.invoke(app, ["capture", "--no-logcat", "--no-dmesg", "--no-uart"])
    assert r.exit_code == 2
    assert fake_capture["kwargs"] is None


def test_capture_output_dir_is_forwarded(fake_capture, tmp_path: Path) -> None:
    r = runner.invoke(app, ["capture", "-d", "1", "-o", str(tmp_path / "here")])
    assert r.exit_code == 0, r.output
    assert fake_capture["kwargs"]["output"] == tmp_path / "here"


def test_capture_failure_exits_1(monkeypatch) -> None:
    async def capture(duration_s, **kwargs):
        return fail(code="INVALID_DURATION", message="duration out of range", category="input")

    monkeypatch.setattr(timeline_cli, "capture_timeline", capture)
    monkeypatch.setattr(timeline_cli, "get_transport", lambda ctx, **kw: object())
    r = runner.invoke(app, ["capture", "-d", "0"])
    assert r.exit_code == 1
    assert "INVALID_DURATION" in r.output


# ─── show ──────────────────────────────────────────────────────────
_SAMPLE = [
    {"t": 0.0, "wall": "w", "src": "meta", "ev": "start"},
    {"t": 0.5, "wall": "w", "src": "logcat", "line": "app start"},
    {"t": 1.0, "wall": "w", "src": "uart", "line": "=> ", "partial": True},
    {"t": 1.5, "wall": "w", "src": "dmesg", "line": "[ 1.5] mmc0: error -110"},
    {"t": 2.0, "wall": "w", "src": "uart", "line": "Kernel panic [false]"},
    {"t": 9.0, "wall": "w", "src": "logcat", "line": "late error"},
]


@pytest.fixture
def capture_dir(tmp_path: Path) -> Path:
    (tmp_path / "timeline.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in _SAMPLE), encoding="utf-8"
    )
    return tmp_path


def test_show_prints_merged_lines_in_order_plain_text(capture_dir: Path) -> None:
    r = runner.invoke(app, ["show", str(capture_dir)])
    assert r.exit_code == 0, r.output
    assert r.output.splitlines() == [
        "   +0.500s logcat | app start",
        "   +1.000s uart   | =>  [partial]",
        "   +1.500s dmesg  | [ 1.5] mmc0: error -110",
        "   +2.000s uart   | Kernel panic [false]",  # device text byte-for-byte
        "   +9.000s logcat | late error",
    ]


def test_show_src_and_grep_filters(capture_dir: Path) -> None:
    r = runner.invoke(app, ["show", str(capture_dir), "--src", "uart,dmesg"])
    assert r.exit_code == 0, r.output
    assert [ln.split("|")[0].split()[-1] for ln in r.output.splitlines()] == [
        "uart",
        "dmesg",
        "uart",
    ]

    r = runner.invoke(app, ["show", str(capture_dir), "--grep", "error"])
    assert r.exit_code == 0, r.output
    assert len(r.output.splitlines()) == 2 and "mmc0" in r.output and "late error" in r.output


def test_show_around_window(capture_dir: Path) -> None:
    r = runner.invoke(app, ["show", str(capture_dir), "--around", "1.5", "--window", "0.6"])
    assert r.exit_code == 0, r.output
    assert [ln.split()[0] for ln in r.output.splitlines()] == ["+1.000s", "+1.500s", "+2.000s"]


def test_show_accepts_the_jsonl_path_itself(capture_dir: Path) -> None:
    r = runner.invoke(app, ["show", str(capture_dir / "timeline.jsonl"), "--src", "dmesg"])
    assert r.exit_code == 0, r.output
    assert r.output.splitlines() == ["   +1.500s dmesg  | [ 1.5] mmc0: error -110"]


def test_show_unknown_src_is_a_usage_error(capture_dir: Path) -> None:
    r = runner.invoke(app, ["show", str(capture_dir), "--src", "kmsg"])
    assert r.exit_code == 2
    assert "INVALID_SOURCE" in (r.output + (r.stderr or ""))


def test_show_missing_dir_exits_1(tmp_path: Path) -> None:
    r = runner.invoke(app, ["show", str(tmp_path / "nope")])
    assert r.exit_code == 1
    assert "FILE_NOT_FOUND" in (r.output + (r.stderr or ""))
