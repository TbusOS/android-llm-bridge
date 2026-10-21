"""Hub side of remote agent update (ADR-060): the bundle, the wait for the
agent to come back, the endpoint, the CLI, and `build` in hello."""

from __future__ import annotations

import asyncio
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from alb.api.server import create_app
from alb.cli import bench_cli
from alb.cli.main import app
from alb.remote import agent_update as au
from alb.remote import protocol as p
from alb.remote.flash import FlashEvent, FlashResult
from tests.remote.bench_tables import new_agent_ops

_SRC = Path(__file__).resolve().parents[2] / "clients" / "windows-agent"


@pytest.fixture(autouse=True)
def _ephemeral_forward_port(monkeypatch):
    monkeypatch.setenv("ALB_ADB_FORWARD_PORT", "0")
    monkeypatch.setattr(au, "_POLL_S", 0.01)


# ── the bundle ───────────────────────────────────────────────────────


def _folder(tmp_path: Path) -> Path:
    src = tmp_path / "windows-agent"
    src.mkdir()
    (src / "alb_agent.py").write_bytes(b"print('agent')\n")
    (src / "run-agent.bat").write_bytes(b"@echo off\r\n")
    (src / "README.md").write_text("doc")
    (src / "agent.conf").write_text("token=REAL\n")  # a real config next to the files
    (src / "notes.log").write_text("x")
    (src / "__pycache__").mkdir()
    (src / "__pycache__" / "alb_agent.cpython-311.pyc").write_bytes(b"\0")
    return src


def test_the_bundle_is_the_agent_files_and_never_the_folders_own_config(tmp_path):
    src = _folder(tmp_path)
    build, names = au.make_bundle(src, None, tmp_path / "b.zip")
    assert names == ["README.md", "alb_agent.py", "run-agent.bat"]
    with zipfile.ZipFile(tmp_path / "b.zip") as zf:
        assert sorted(zf.namelist()) == names
        assert zf.read("run-agent.bat") == b"@echo off\r\n", "bytes travel unchanged"
    assert build == au.build_of(b"print('agent')\n")


def test_a_config_is_sent_only_when_passed(tmp_path):
    src = _folder(tmp_path)
    _, names = au.make_bundle(src, "hub_url=ws://h\r\ntoken=t\r\n", tmp_path / "b.zip")
    assert "agent.conf" in names
    with zipfile.ZipFile(tmp_path / "b.zip") as zf:
        assert zf.read("agent.conf") == b"hub_url=ws://h\r\ntoken=t\r\n"


def test_identical_files_make_an_identical_bundle(tmp_path):
    src = _folder(tmp_path)
    au.make_bundle(src, None, tmp_path / "1.zip")
    au.make_bundle(src, None, tmp_path / "2.zip")
    assert (tmp_path / "1.zip").read_bytes() == (tmp_path / "2.zip").read_bytes()


def test_a_folder_without_the_agent_is_refused(tmp_path):
    src = _folder(tmp_path)
    (src / "alb_agent.py").unlink()
    with pytest.raises(ValueError, match=r"alb_agent\.py"):
        au.make_bundle(src, None, tmp_path / "b.zip")


def test_the_repo_folder_bundles_cleanly(tmp_path):
    """The real folder: every file it ships passes the agent's rules."""
    build, names = au.make_bundle(_SRC, None, tmp_path / "b.zip")
    assert "alb_agent.py" in names and "run-agent.bat" in names
    assert build == au.local_build(_SRC)


def test_bundle_dir_defaults_to_this_checkout_and_can_be_overridden(monkeypatch, tmp_path):
    monkeypatch.delenv("ALB_AGENT_BUNDLE_DIR", raising=False)
    assert au.bundle_dir() == _SRC
    monkeypatch.setenv("ALB_AGENT_BUNDLE_DIR", str(tmp_path))
    assert au.bundle_dir() == tmp_path


# ── waiting for the agent to come back ───────────────────────────────


class _Conn:
    def __init__(self, build: str, last_update: dict | None = None, ops: bool = True) -> None:
        self.agent_id = "bench-1"
        self.name = "bench"
        self.build = build
        self.last_update = last_update or {}
        self.op_table = {"agent-update": object()} if ops else {}


class _Registry:
    """current_agent() is the old connection; get() starts returning
    `after` once `after_polls` lookups have passed."""

    def __init__(self, old: _Conn, after: _Conn | None = None, after_polls: int = 2) -> None:
        self.old, self.after, self.left = old, after, after_polls

    def current_agent(self):
        return self.old

    def get(self, _agent_id):
        if self.after is None:
            return self.old
        self.left -= 1
        return self.after if self.left <= 0 else self.old


class _Svc:
    def __init__(self, result: FlashResult) -> None:
        self.result = result
        self.calls: list[tuple] = []

    async def run_op(self, name, params, *, image=None, label="", on_event=None):
        assert image is not None and zipfile.is_zipfile(image)
        self.calls.append((name, params, label))
        return self.result


def _restarting(**extra: Any) -> FlashResult:
    data = {"restarting": True, "update_id": "u1", "from_build": "old", "to_build": "new"}
    return FlashResult(ok=True, rc=0, data={**data, **extra})


async def _run(tmp_path, registry, svc, **kw) -> FlashResult:
    return await au.update_agent(svc, registry, src=_folder(tmp_path), **kw)


async def test_the_new_build_coming_back_is_an_update(tmp_path):
    reg = _Registry(_Conn("old"), _Conn("new", {"id": "u1", "outcome": "pending"}))
    r = await _run(tmp_path, reg, _Svc(_restarting()))
    assert r.ok and r.data["outcome"] == "updated" and r.data["build"] == "new"


async def test_the_old_build_coming_back_rolled_back_is_reported_as_such(tmp_path):
    reg = _Registry(_Conn("old"), _Conn("old", {"id": "u1", "outcome": "rolled_back"}))
    r = await _run(tmp_path, reg, _Svc(_restarting()))
    assert not r.ok and r.code == "AGENT_UPDATE_ROLLED_BACK"
    assert "put build old back" in r.error


async def test_same_agent_file_rolled_back_is_still_told_apart(tmp_path):
    """Only run-agent.bat changed: both builds have the same digest, so the
    build alone cannot say which came back — the update record can."""
    data = {"to_build": "same", "from_build": "same"}
    reg = _Registry(_Conn("same"), _Conn("same", {"id": "u1", "outcome": "rolled_back"}))
    r = await _run(tmp_path, reg, _Svc(_restarting(**data)))
    assert r.code == "AGENT_UPDATE_ROLLED_BACK"


async def test_nobody_coming_back_is_a_timeout(tmp_path):
    reg = _Registry(_Conn("old"))
    r = await _run(tmp_path, reg, _Svc(_restarting()), reconnect_timeout_s=0.1)
    assert not r.ok and r.code == "AGENT_UPDATE_NO_RETURN"


async def test_nothing_to_change_returns_without_waiting(tmp_path):
    done = FlashResult(ok=True, rc=0, stdout="nothing to do", data={"restarting": False})
    r = await _run(tmp_path, _Registry(_Conn("old")), _Svc(done), reconnect_timeout_s=0.0)
    assert r.ok and r.stdout == "nothing to do"


async def test_a_refusal_from_the_agent_passes_through(tmp_path):
    refused = FlashResult(ok=False, code="AGENT_UPDATE_CHECK_FAILED", error="SyntaxError")
    r = await _run(tmp_path, _Registry(_Conn("old")), _Svc(refused))
    assert r.code == "AGENT_UPDATE_CHECK_FAILED"


async def test_an_agent_from_before_remote_update_is_told_to_be_copied_once(tmp_path):
    svc = _Svc(_restarting())
    r = await _run(tmp_path, _Registry(_Conn("", ops=False)), svc)
    assert r.code == "BENCH_AGENT_TOO_OLD" and "by hand once" in r.error
    assert svc.calls == []


async def test_no_agent_no_update(tmp_path):
    class _Empty:
        def current_agent(self):
            return None

    r = await au.update_agent(_Svc(_restarting()), _Empty(), src=_folder(tmp_path))
    assert r.code == "FASTBOOT_UNAVAILABLE"


# ── the agent's answer lost on the way ───────────────────────────────
#
# 2026-10-16 on the bench: the old agent froze (a text selection in its
# console window) right after starting the update helper. Its answer never
# left, the helper killed it after 15 s and the update went through — but
# the CLI reported FLASH_FAILED with "re-flash before rebooting".


def _lost() -> FlashResult:
    return FlashResult(
        ok=False,
        code="FLASH_FAILED",
        error="agent closed the job channel without reporting a result",
        data={"result_lost": True},
    )


async def _run_lost(tmp_path, make_registry, **kw) -> FlashResult:
    src = _folder(tmp_path)
    expect, _ = au.make_bundle(src, None, tmp_path / "probe.zip")
    return await au.update_agent(_Svc(_lost()), make_registry(expect), src=src, **kw)


async def test_a_lost_answer_then_the_new_build_is_an_update(tmp_path):
    r = await _run_lost(
        tmp_path,
        lambda new: _Registry(_Conn("old"), _Conn(new, {"outcome": "updated", "to_build": new})),
    )
    assert r.ok and r.data["outcome"] == "updated" and r.data["result_lost"] is True


async def test_a_lost_answer_then_a_rollback_is_reported_as_such(tmp_path):
    r = await _run_lost(
        tmp_path,
        lambda new: _Registry(
            _Conn("old"),
            _Conn("old", {"outcome": "rolled_back", "from_build": "old", "to_build": new}),
        ),
    )
    assert r.code == "AGENT_UPDATE_ROLLED_BACK"


async def test_a_lost_answer_and_no_restart_says_so(tmp_path):
    r = await _run_lost(tmp_path, lambda _new: _Registry(_Conn("old")), reconnect_timeout_s=0.1)
    assert r.code == "AGENT_UPDATE_NO_RETURN"
    assert "without an answer" in r.error and "alb bench log update" in r.error


async def test_a_job_failure_that_is_not_a_lost_answer_passes_through(tmp_path):
    failed = FlashResult(ok=False, code="FLASH_FAILED", error="channel open failed")
    r = await _run(tmp_path, _Registry(_Conn("old")), _Svc(failed), reconnect_timeout_s=5.0)
    assert r.code == "FLASH_FAILED" and r.error == "channel open failed"


# ── hello carries build and the last update ──────────────────────────


def test_status_shows_the_build_and_the_last_update(monkeypatch):
    monkeypatch.delenv("ALB_AGENT_TOKEN", raising=False)
    hello = p.hello(agent_id="agent-1", name="t", version=1, caps=["adb"], token=None)
    hello["build"] = "abc123def456"
    hello["update"] = {"id": "u1", "outcome": "updated"}
    with TestClient(create_app()) as c:
        with c.websocket_connect("/agent/connect") as ws:
            ws.send_text(p.encode_control(hello))
            assert p.decode_control(ws.receive_text())["verb"] == p.Verb.HELLO_OK.value
            agent = c.get("/agent/status").json()["agents"][0]
    assert agent["build"] == "abc123def456"
    assert agent["last_update"] == {"id": "u1", "outcome": "updated"}


def test_an_old_agent_has_no_build(monkeypatch):
    monkeypatch.delenv("ALB_AGENT_TOKEN", raising=False)
    hello = p.hello(agent_id="agent-1", name="t", version=1, caps=["adb"], token=None)
    with TestClient(create_app()) as c:
        with c.websocket_connect("/agent/connect") as ws:
            ws.send_text(p.encode_control(hello))
            ws.receive_text()
            agent = c.get("/agent/status").json()["agents"][0]
    assert agent["build"] == "" and agent["last_update"] == {}


# ── endpoint ─────────────────────────────────────────────────────────


def test_the_endpoint_streams_and_passes_the_config_text(monkeypatch):
    import alb.api.bench_route as route

    seen: dict[str, Any] = {}

    async def fake_update(svc, registry, *, config_text=None, on_event=None):
        seen["config_text"] = config_text
        on_event(FlashEvent(phase="restart", text="waiting"))
        await asyncio.sleep(0)
        return FlashResult(ok=True, rc=0, data={"outcome": "updated"})

    monkeypatch.setattr(route, "update_agent", fake_update)
    with TestClient(create_app()) as c:
        resp = c.post("/api/bench/agent-update", json={"config_text": "token=t\r\n"})
    lines = [json.loads(x) for x in resp.text.splitlines() if x.strip()]
    assert lines[0]["ev"] == "progress" and lines[0]["phase"] == "restart"
    assert lines[-1]["ev"] == "done" and lines[-1]["data"] == {"outcome": "updated"}
    assert seen["config_text"] == "token=t\r\n"


def test_ops_reports_which_build_this_hub_would_send(monkeypatch):
    import alb.api.bench_route as route

    class _S:
        def agent_summary(self):
            return {"id": "a", "name": "bench", "build": "old"}

        def ops_reported(self):
            return True

        def partition_list(self):
            from alb.remote.bench_board import PartitionList

            return PartitionList([])

        def ops(self):
            return new_agent_ops()

    monkeypatch.setattr(route, "get_flash_service", lambda: _S())
    with TestClient(create_app()) as c:
        body = c.get("/api/bench/ops").json()
    assert body["bundle_build"] == au.local_build(_SRC)


# ── CLI ──────────────────────────────────────────────────────────────

runner = CliRunner()


@pytest.fixture
def hub(monkeypatch):
    state: dict[str, Any] = {"runs": [], "final": {}, "ops": {}}

    def run_job(path, payload=None, render=None, *, base="/api/flash"):
        state["runs"].append((base, path, payload))
        return state["final"]

    def fetch_ops():
        return state["ops"]

    monkeypatch.setattr(bench_cli, "_run_job", run_job)
    monkeypatch.setattr(bench_cli, "_fetch_ops", fetch_ops)
    return state


def test_cli_asks_first_and_sends_nothing_when_declined(hub):
    r = runner.invoke(app, ["bench", "agent-update"], input="n\n")
    assert r.exit_code != 0 and hub["runs"] == []


def test_cli_sends_the_config_bytes_unchanged(hub, tmp_path):
    conf = tmp_path / "agent.conf"
    conf.write_bytes(b"hub_url=ws://h\r\ntoken=t\r\n")
    hub["final"] = {
        "ev": "done",
        "ok": True,
        "duration_s": 9.5,
        "data": {
            "restarting": True,
            "from_build": "old",
            "build": "new",
            "changed": ["alb_agent.py"],
        },
    }
    r = runner.invoke(app, ["bench", "agent-update", "--config", str(conf), "--yes"])
    assert r.exit_code == 0, r.output
    assert hub["runs"] == [
        ("/api/bench", "agent-update", {"config_text": "hub_url=ws://h\r\ntoken=t\r\n"})
    ]
    assert "old -> new" in r.output and "alb_agent.py" in r.output


def test_cli_explains_a_rollback(hub):
    hub["final"] = {
        "ev": "done",
        "ok": False,
        "code": "AGENT_UPDATE_ROLLED_BACK",
        "error": "build new did not reach the hub",
    }
    r = runner.invoke(app, ["bench", "agent-update", "--yes"])
    assert r.exit_code == 1
    assert "AGENT_UPDATE_ROLLED_BACK" in r.output and "alb bench log update" in r.output


def test_ops_says_when_the_bench_runs_an_older_copy(hub):
    hub["ops"] = {
        "agent": {"id": "a", "name": "bench", "build": "old"},
        "bundle_build": "new",
        "reported": True,
        "partitions": [],
        "ops": [],
    }
    r = runner.invoke(app, ["bench", "ops"])
    assert r.exit_code == 0, r.output
    assert "this hub has new" in r.output and "alb bench agent-update" in r.output
