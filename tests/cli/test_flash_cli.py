"""`alb flash` failure rendering (ADR-056).

The bug these lock (seen on real hardware 2026-08-07): the agent reported
`FASTBOOT_NO_DEVICE` — exactly the right code — and the CLI printed
"fastboot failed" next to it. The code was right and the message pointed
nowhere, which leaves the reader in the same place as a code pointing at the
wrong subsystem: go find out yourself.
"""

from __future__ import annotations

import json

import httpx
import pytest
from typer.testing import CliRunner

from alb.cli import flash_cli
from alb.cli.flash_cli import _explain


def test_generic_tool_wording_is_replaced_by_the_catalog_sentence():
    message, advice = _explain("FASTBOOT_NO_DEVICE", "fastboot failed")
    assert "fastboot failed" not in message
    assert "No device is visible to fastboot" in message
    assert "fastboot" in advice and advice  # a remedy, not just a restatement


def test_a_specific_message_from_the_agent_wins():
    """When the agent DID have something to say, do not overwrite it — it
    saw the failure and this side did not."""
    message, advice = _explain("FLASH_FAILED", "FAILED (remote: 'partition table doesn't exist')")
    assert "partition table" in message
    assert advice  # the remedy still comes from the catalog


@pytest.mark.parametrize("blank", ["", "   ", "unknown", "failed", "error"])
def test_empty_ish_messages_fall_back(blank):
    message, _ = _explain("FASTBOOT_BUSY", blank)
    assert "Another flash job" in message


def test_unknown_code_does_not_invent_advice():
    message, advice = _explain("NOT_A_REAL_CODE", "something went sideways")
    assert message == "something went sideways"
    assert advice == ""


def test_no_code_and_no_message_still_says_something():
    message, advice = _explain("", "")
    assert message == "unknown failure"
    assert advice == ""


@pytest.mark.parametrize(
    "code",
    [
        "FASTBOOT_UNAVAILABLE",
        "FASTBOOT_BUSY",
        "FASTBOOT_NO_DEVICE",
        "FLASH_IMAGE_CORRUPT",
        "FLASH_PARTITION_REJECTED",
        "FLASH_OEM_REJECTED",
        "FLASH_FAILED",
        "FLASH_AGENT_NO_SPACE",
        "FLASH_PLAN_REJECTED",
        "FLASH_PLAN_CHANGED",
    ],
)
def test_every_flash_code_carries_a_remedy(code):
    """A code with no advice is half a diagnosis. Locks the catalog too, not
    just the renderer."""
    message, advice = _explain(code, "")
    assert message and advice
    assert advice != message


# ── alb flash plan ──────────────────────────────────────────────────────

_ROWS = [
    {
        "line": 1,
        "action": "setvar",
        "partition": "",
        "file": "",
        "key": "model",
        "value": "model-x",
        "size": 0,
        "md5": "",
        "sha256": "",
        "skip": "--skip-action setvar",
        "status": "pending",
    },
    {
        "line": 2,
        "action": "flash",
        "partition": "loader_a",
        "file": "loader.elf",
        "key": "",
        "value": "",
        "size": 0,
        "md5": "",
        "sha256": "",
        "skip": "--skip loader_a",
        "status": "pending",
    },
    {
        "line": 3,
        "action": "flash",
        "partition": "boot_a",
        "file": "boot.img",
        "key": "",
        "value": "",
        "size": 300,
        "md5": "a" * 32,
        "sha256": "b" * 64,
        "skip": "",
        "status": "pending",
    },
    {
        "line": 4,
        "action": "flash",
        "partition": "boot_b",
        "file": "boot.img",
        "key": "",
        "value": "",
        "size": 300,
        "md5": "a" * 32,
        "sha256": "b" * 64,
        "skip": "",
        "status": "pending",
    },
]


def _plan_body(problems=()):
    return {
        "v": 1,
        "ok": not problems,
        "manifest": "images/board-x/flash.txt",
        "fingerprint": "f" * 64,
        "problems": list(problems),
        "total": 4,
        "run": 2,
        "skipped": 2,
        "rows": _ROWS,
    }


def _hub(monkeypatch, *, problems=(), fail_line=0):
    calls: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        calls.append((request.url.path, body))
        if request.url.path == "/api/flash/plan":
            return httpx.Response(200, json=_plan_body(problems))
        rows = [dict(r, status="skipped" if r["skip"] else "ok") for r in _ROWS]
        done = {
            "ev": "done",
            "ok": True,
            "code": "",
            "error": "",
            "failed_line": 0,
            "total": 4,
            "ran": 2,
            "skipped": 2,
            "not_run": 0,
            "duration_s": 1.5,
            "artifacts": "",
            "rows": rows,
        }
        if fail_line:
            rows[3]["status"] = "failed"
            done.update(
                ok=False,
                code="FLASH_FAILED",
                error="FAILED (remote: 'write failed')",
                failed_line=fail_line,
                ran=2,
            )
        step = {
            "ev": "progress",
            "phase": "step",
            "done": 1,
            "total": 2,
            "text": "line 3: flash boot_a <- boot.img",
        }
        text = json.dumps(step) + "\n" + json.dumps(done) + "\n"
        return httpx.Response(200, text=text, headers={"content-type": "application/x-ndjson"})

    real = httpx.Client
    monkeypatch.setattr(
        flash_cli.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    )
    return calls


def _invoke(args, input_=None):
    return CliRunner().invoke(flash_cli.app, ["plan", *args], input=input_)


def test_dry_run_prints_every_row_and_runs_nothing(monkeypatch):
    calls = _hub(monkeypatch)
    r = _invoke(
        ["images/board-x/flash.txt", "--skip", "loader_a", "--skip-action", "setvar", "--dry-run"]
    )
    assert r.exit_code == 0, r.output
    assert [path for path, _ in calls] == ["/api/flash/plan"]
    assert calls[0][1] == {
        "manifest": "images/board-x/flash.txt",
        "skip": ["loader_a"],
        "skip_actions": ["setvar"],
    }
    assert "skip (--skip loader_a)" in r.output
    assert "a" * 32 in r.output, "md5 shown for comparison with the vendor listing"
    assert "nothing was run" in r.output


def test_a_plan_with_problems_exits_1_and_lists_them(monkeypatch):
    calls = _hub(monkeypatch, problems=["line 2: file 'loader.elf' not found next to the manifest"])
    r = _invoke(["images/board-x/flash.txt", "--yes"])
    assert r.exit_code == 1
    assert "FLASH_PLAN_REJECTED" in r.output and "line 2: file" in r.output
    assert [path for path, _ in calls] == ["/api/flash/plan"], "never asked to run"


def test_declining_the_confirmation_runs_nothing(monkeypatch):
    calls = _hub(monkeypatch)
    r = _invoke(["images/board-x/flash.txt"], input_="n\n")
    assert r.exit_code != 0
    assert [path for path, _ in calls] == ["/api/flash/plan"]


def test_a_confirmed_run_sends_the_fingerprint_it_showed(monkeypatch):
    calls = _hub(monkeypatch)
    r = _invoke(["images/board-x/flash.txt", "--skip", "loader_a", "--yes", "--reboot-wait", "45"])
    assert r.exit_code == 0, r.output
    path, body = calls[1]
    assert path == "/api/flash/plan/run"
    assert body["fingerprint"] == "f" * 64 and body["reboot_wait_s"] == 45
    assert body["skip"] == ["loader_a"]
    assert "[1/2] line 3: flash boot_a <- boot.img" in r.output
    assert "[ok] plan finished · 4 rows · ran 2 · skipped 2" in r.output
    assert "skipped line 2: flash loader_a <- loader.elf (--skip loader_a)" in r.output


def test_a_failed_row_is_named_and_exits_1(monkeypatch):
    _hub(monkeypatch, fail_line=4)
    r = _invoke(["images/board-x/flash.txt", "--yes"])
    assert r.exit_code == 1
    assert "[fail] line 4 flash boot_b <- boot.img: FLASH_FAILED" in r.output
    assert "write failed" in r.output


def test_a_plan_refusal_detail_is_printed_one_problem_per_line(capsys):
    resp = httpx.Response(
        409, json={"detail": {"code": "FLASH_PLAN_CHANGED", "problems": ["one", "two"]}}
    )
    flash_cli._print_rejection(resp)
    err = capsys.readouterr().err
    assert "FLASH_PLAN_CHANGED" in err
    assert "  - one\n  - two" in err


# ── status (ADR-063): which board's list applies, or why nothing may be written ──


def _status_hub(monkeypatch, body: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/flash/status"
        return httpx.Response(200, json=body)

    real = httpx.Client
    monkeypatch.setattr(
        flash_cli.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    )


_READY = {"available": True, "busy": False, "job": ""}


def test_status_names_the_chosen_board_and_its_list(monkeypatch):
    _status_hub(
        monkeypatch,
        {**_READY, "partitions": ["boot_a", "misc"], "partitions_problem": "", "board": "board-a"},
    )
    r = CliRunner().invoke(flash_cli.app, ["status"])
    assert r.exit_code == 0
    assert "[ok] ready" in r.output
    assert "board board-a" in r.output and "boot_a, misc" in r.output


def test_status_fails_when_no_partition_may_be_written(monkeypatch):
    """`[ok] ready` alone was a lie here (seen 2026-10-22): devices and getvar
    worked, and every write was refused with FLASH_BOARD_NOT_LISTED."""
    problem = "the agent keeps a partition list per board (board-a) and no bench board is chosen"
    _status_hub(
        monkeypatch, {**_READY, "partitions": [], "partitions_problem": problem, "board": None}
    )
    r = CliRunner().invoke(flash_cli.app, ["status"])
    assert r.exit_code == 1
    assert "no partition may be written" in r.output and problem in r.output
    assert "[ok]" not in r.output


def test_status_with_one_list_for_all_boards_reads_as_before(monkeypatch):
    _status_hub(
        monkeypatch, {**_READY, "partitions": ["cfg"], "partitions_problem": "", "board": None}
    )
    r = CliRunner().invoke(flash_cli.app, ["status"])
    assert r.exit_code == 0
    assert "partitions cfg" in r.output and "board" not in r.output


def test_status_prints_why_flashing_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    why = {
        "code": "FASTBOOT_UNAVAILABLE",
        "cause": "no_agent",
        "message": "no agent connected",
        "suggestion": "start the agent on the bench host, or run `alb bench agent-start`",
    }
    _status_hub(monkeypatch, {"available": False, "unavailable": why, "busy": False, "job": ""})
    r = CliRunner().invoke(flash_cli.app, ["status"])
    assert r.exit_code == 1
    assert "FASTBOOT_UNAVAILABLE: no agent connected" in r.output
    assert "alb bench agent-start" in r.output
    # the old wording blamed fastboot_path, which is the wrong fix here
    assert "fastboot_path" not in r.output
