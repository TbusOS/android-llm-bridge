"""`alb flash tools` / `alb flash tool` (ADR-068): what reaches the hub and
what the reader is told.

The arguments after `--` must reach the hub exactly as typed — options
included, since they are the vendor tool's options, not alb's. The result
must say who judged it and where the files the tool wrote were kept.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from alb.cli import flash_cli


def _hub(
    monkeypatch: pytest.MonkeyPatch, done: dict[str, Any], progress: Sequence[str] = ()
) -> list[tuple[str, Any]]:
    calls: list[tuple[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, json.loads(request.content or b"{}")))
        lines = [
            json.dumps({"ev": "progress", "phase": "tool", "done": 0, "total": 0, "text": t})
            for t in progress
        ]
        lines.append(json.dumps({"ev": "done", "duration_s": 1.5, **done}))
        return httpx.Response(200, text="\n".join(lines) + "\n")

    real = httpx.Client
    # flash_cli calls httpx.Client through the module, so patching it here is what it sees
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    )
    return calls


_OK = {
    "ok": True,
    "rc": 0,
    "stdout": "all done",
    "artifacts": "/ws/flash/tool-run-vt-board-a-v1-2026-11-10T10-00-00",
    "data": {
        "judged_by": "exit_code+ok_pattern",
        "highlights": ["part system: ok"],
        "files": [
            {"name": "tool.log", "size": 2048, "sent": True, "why": ""},
            {"name": "dump.bin", "size": 9 * 1024 * 1024, "sent": False, "why": "over 4 MiB"},
        ],
        "files_dir": "/ws/flash/tool-run-vt-board-a-v1-2026-11-10T10-00-00/files",
    },
}


def test_arguments_after_double_dash_reach_the_hub_as_typed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _hub(monkeypatch, _OK, progress=["writing", "all done"])
    r = CliRunner().invoke(
        flash_cli.app,
        ["tool", "vt", "--pkg", "board-a/v1", "--yes", "--", "-p", "{com}", "--pkg", "x y", "-y"],
    )
    assert r.exit_code == 0, r.output
    assert calls == [
        (
            "/api/bench/ops/tool-run",
            {
                "params": {
                    "tool": "vt",
                    "args": ["-p", "{com}", "--pkg", "x y", "-y"],
                    "pkg": "board-a/v1",
                }
            },
        )
    ]
    out = r.output
    assert "  tool: writing" in out
    assert "judged by: exit_code+ok_pattern" in out
    assert "part system: ok" in out
    assert "kept in /ws/flash/tool-run-vt-board-a-v1-2026-11-10T10-00-00/files" in out
    assert "tool.log (2.0 KiB)" in out
    assert "dump.bin (9.0 MiB) — not brought back: over 4 MiB" in out
    assert "timeline.jsonl" in out
    assert out.count("all done") == 1, "the streamed lines are not printed a second time"


def test_without_yes_the_command_line_is_shown_and_confirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _hub(monkeypatch, _OK)
    r = CliRunner().invoke(flash_cli.app, ["tool", "vt", "--", "-f", "a b.img"], input="n\n")
    assert r.exit_code != 0
    assert "about to run vt -f 'a b.img' on the bench" in r.output
    assert calls == []


def test_a_failure_says_who_judged_and_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    done = {
        "ok": False,
        "rc": 0,
        "code": "BENCH_TOOL_FAILED",
        "error": "vt: exit code 0, but no line matched tool_ok.vt (all done)",
        "stdout": "writing",
        "data": {"judged_by": "ok_pattern_missing", "files": []},
    }
    _hub(monkeypatch, done, progress=["writing"])
    r = CliRunner().invoke(flash_cli.app, ["tool", "vt", "--yes"])
    assert r.exit_code == 1
    assert "[fail] BENCH_TOOL_FAILED: vt: exit code 0, but no line matched" in r.output
    assert "judged by: ok_pattern_missing" in r.output
    assert "try:" in r.output


def test_a_refusal_before_the_tool_ran_shows_its_words(monkeypatch: pytest.MonkeyPatch) -> None:
    done = {
        "ok": False,
        "code": "BENCH_TOOL_NO_DEVICE",
        "error": "no USB device 1a2b:3c4d (tool_usb.vt) is attached",
        "stdout": "",
    }
    _hub(monkeypatch, done)
    r = CliRunner().invoke(flash_cli.app, ["tool", "vt", "--yes"])
    assert r.exit_code == 1 and "BENCH_TOOL_NO_DEVICE" in r.output and "1a2b:3c4d" in r.output


def test_tools_lists_settings_device_and_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    done = {
        "ok": True,
        "data": {
            "tools": [
                {
                    "name": "vt",
                    "exe": "D:\\tools\\vt.exe",
                    "root": "D:\\flash",
                    "cwd": "package",
                    "timeout_s": 1500,
                    "ok": "all done",
                    "fail": "FAILED",
                    "note": "",
                    "usb": ["1a2b:3c4d"],
                    "attached": [],
                    "usage": "hold the key\nthen plug in",
                },
                {
                    "name": "plain",
                    "exe": "D:\\tools\\p.exe",
                    "root": "D:\\p",
                    "cwd": "job",
                    "timeout_s": 60,
                    "ok": "",
                    "fail": "",
                    "note": "",
                    "usb": [],
                    "usage": "",
                },
            ],
            "problems": ["tool gone: tool_exe D:\\x.exe is not a file — not offered"],
        },
    }
    calls = _hub(monkeypatch, done)
    r = CliRunner().invoke(flash_cli.app, ["tools"])
    assert r.exit_code == 0, r.output
    assert calls[0][0] == "/api/bench/ops/tool-list"
    out = r.output
    assert "vt  D:\\tools\\vt.exe" in out
    assert "runs in the package directory" in out and "time limit 1500s" in out
    assert "success: exit code 0 and /all done/ · failure: /FAILED/" in out
    assert "USB 1a2b:3c4d: not attached (is the board in download mode?)" in out
    assert "      hold the key\n      then plug in" in out
    assert "success: exit code 0 only" in out and "USB: not checked" in out
    assert "[warn] tool gone" in out


def test_tools_on_a_bench_without_any_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    _hub(
        monkeypatch,
        {"ok": False, "code": "BENCH_OP_UNKNOWN", "error": "agent agent-1 has no op 'tool-list'"},
    )
    r = CliRunner().invoke(flash_cli.app, ["tools"])
    assert r.exit_code == 1 and "BENCH_OP_UNKNOWN" in r.output
