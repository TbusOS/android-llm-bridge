"""`alb bench` — rendering and confirmation (ADR-059)."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from alb.cli import bench_cli
from alb.cli.main import app
from tests.remote.bench_tables import new_agent_ops

runner = CliRunner()


@pytest.fixture
def hub(monkeypatch):
    """Stand-in for alb-api: the op table, and a recorder for runs."""
    state: dict = {"runs": [], "final": {"ev": "done", "ok": True, "duration_s": 0.1}}

    def fetch_ops():
        return {
            "agent": {"id": "a", "name": "bench"},
            "reported": True,
            "partitions": [],
            "ops": [o.as_dict() for o in new_agent_ops().values()],
        }

    def run_job(path, payload=None, render=None, *, base="/api/flash"):
        state["runs"].append((base, path, payload))
        return state["final"]

    monkeypatch.setattr(bench_cli, "_fetch_ops", fetch_ops)
    monkeypatch.setattr(bench_cli, "_run_job", run_job)
    return state


def test_ops_lists_names_parameters_and_flags(hub):
    r = runner.invoke(app, ["bench", "ops"])
    assert r.exit_code == 0, r.output
    assert "set-active" in r.output and "slot=<a|b>" in r.output
    assert "changes device" in r.output


def test_a_mutating_op_asks_first(hub):
    r = runner.invoke(app, ["bench", "run", "erase", "partition=misc"], input="n\n")
    assert r.exit_code != 0
    assert hub["runs"] == [], "declined — nothing may be sent"


def test_yes_skips_the_question_and_sends_named_params(hub):
    r = runner.invoke(app, ["bench", "run", "set-active", "slot=b", "--yes"])
    assert r.exit_code == 0, r.output
    assert hub["runs"] == [("/api/bench", "ops/set-active", {"params": {"slot": "b"}})]


def test_a_read_only_op_does_not_ask(hub):
    r = runner.invoke(app, ["bench", "run", "log-tail", "source=agent", "lines=20"])
    assert r.exit_code == 0, r.output
    assert hub["runs"][0][2] == {"params": {"source": "agent", "lines": "20"}}


def test_malformed_assignment_is_refused(hub):
    r = runner.invoke(app, ["bench", "run", "set-active", "b", "--yes"])
    assert r.exit_code == 2
    assert hub["runs"] == []


def test_usb_names_the_board_and_the_missing_driver(hub):
    hub["final"] = {
        "ev": "done",
        "ok": True,
        "data": {
            "devices": [{}],
            "boards": [
                {
                    "mode": "fastboot",
                    "vid": "abcd",
                    "pid": "0001",
                    "serial": "S1",
                    "driver": "missing",
                    "name": "Android",
                    "hint": "a board is in fastboot but no driver is bound",
                }
            ],
        },
    }
    r = runner.invoke(app, ["bench", "usb"])
    assert r.exit_code == 0, r.output
    assert "fastboot" in r.output and "driver: missing" in r.output
    assert "no driver is bound" in r.output


def test_usb_shows_a_windows_made_id_apart_from_the_serial(hub):
    hub["final"] = {
        "ev": "done",
        "ok": True,
        "data": {
            "devices": [{}],
            "boards": [
                {
                    "mode": "adb",
                    "vid": "2207",
                    "pid": "0006",
                    "serial": "",
                    "windows_id": "6&1A2B3C4D&0&3",
                    "driver": "ok",
                    "name": "ADB Interface",
                    "hint": "",
                }
            ],
        },
    }
    r = runner.invoke(app, ["bench", "usb"])
    assert r.exit_code == 0, r.output
    assert "serial - (windows id 6&1A2B3C4D&0&3)" in r.output


def test_usb_with_no_board_points_at_the_cable(hub):
    hub["final"] = {"ev": "done", "ok": True, "data": {"devices": [{}, {}], "boards": []}}
    r = runner.invoke(app, ["bench", "usb"])
    assert r.exit_code == 0, r.output
    assert "none of the 2 USB device(s)" in r.output and "cable" in r.output


def test_a_failure_uses_the_catalog_advice(hub):
    hub["final"] = {"ev": "done", "ok": False, "code": "BENCH_AGENT_TOO_OLD", "error": ""}
    r = runner.invoke(app, ["bench", "usb"])
    assert r.exit_code == 1
    assert "BENCH_AGENT_TOO_OLD" in r.output and "alb_agent.py" in r.output


def test_log_passes_source_lines_and_grep(hub):
    hub["final"] = {
        "ev": "done",
        "ok": True,
        "stdout": "line a\nline b\n",
        "data": {"source": "agent", "lines": 2, "truncated": True},
    }
    r = runner.invoke(app, ["bench", "log", "agent", "-n", "2", "-g", "line"])
    assert r.exit_code == 0, r.output
    assert hub["runs"][0][2] == {"params": {"source": "agent", "lines": 2, "grep": "line"}}
    assert "line b" in r.output and "older lines exist" in r.output
