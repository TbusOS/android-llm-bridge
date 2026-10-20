"""Starting the bench agent over ssh (ADR-061): what is sent to the bench
host, how its answer is read, the wait for the agent, the endpoint, the CLI."""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from alb.api.server import create_app
from alb.cli import bench_cli
from alb.cli.main import app
from alb.infra.errors import lookup
from alb.infra.process import ProcessResult
from alb.remote import agent_start as st
from alb.remote.flash import FlashEvent, FlashResult


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setenv("ALB_ADB_FORWARD_PORT", "0")
    monkeypatch.setenv(st.SSH_HOST_ENV, "bench")
    monkeypatch.delenv(st.TASK_ENV, raising=False)
    monkeypatch.setattr(st, "_POLL_S", 0.01)
    monkeypatch.setattr(st, "MIN_WAIT_S", 0.05)


class _Conn:
    def __init__(self, name: str = "bench-a", build: str = "b1") -> None:
        self.name, self.build, self.agent_id = name, build, f"id-{name}"


class _Registry:
    """current_agent() is None until `after_polls` lookups have passed."""

    def __init__(self, conn: _Conn | None = None, after_polls: int = 0) -> None:
        self.conn, self.left = conn, after_polls

    def current_agent(self):
        if self.conn is None:
            return None
        self.left -= 1
        return self.conn if self.left < 0 else None


def _out(*lines: str) -> str:
    return "\r\n".join(lines) + "\r\n"  # Windows line ends, as ssh delivers them


class _Runner:
    """Answers each ssh call with the next canned result; records the argv."""

    def __init__(self, *results: ProcessResult) -> None:
        self.results, self.calls = list(results), []

    async def __call__(
        self,
        *argv: str,
        timeout: float = 30.0,  # noqa: ASYNC109 — name fixed by process.run, which it stands in for
    ) -> ProcessResult:
        self.calls.append(list(argv))
        return self.results.pop(0)

    def script(self, i: int) -> str:
        return base64.b64decode(self.calls[i][-1]).decode("utf-16-le")


def _ok(stdout: str) -> ProcessResult:
    return ProcessResult(exit_code=0, stdout=stdout, stderr="", duration_ms=5)


_STARTED = _out(
    r"alb-start: dir=D:\agent",
    "alb-start: state=Ready",
    "alb-start: action=started",
    "alb-start: after=Running",
    "alb-start: last-result=267009",
)
_TAIL = _out(
    r"alb-start: dir=D:\agent",
    r"alb-start: log=D:\agent\logs\agent.log",
    "alb-log: 11:08:57 INFO alb-agent: starting",
    "alb-log: 11:08:59 WARNING alb-agent: session ended: [WinError 1225]",
)


def _run(reg: _Registry, runner: _Runner, **kw: Any) -> FlashResult:
    return asyncio.run(st.start_agent(reg, runner=runner, **kw))


# ── what goes to the bench host ──────────────────────────────────────


def test_the_host_is_an_ssh_argument_after_the_options_and_never_a_prompt():
    argv = st.ssh_argv("bench", "'x'")
    assert argv[:5] == ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    assert argv[5:7] == ["--", "bench"]
    assert argv[7:11] == ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand"]


def test_the_script_travels_encoded_and_comes_back_byte_for_byte():
    script = st.start_script("alb-agent")
    assert base64.b64decode(st.encode_ps(script)).decode("utf-16-le") == script
    assert "$name = 'alb-agent'" in script and "$_.Exception.Message" in script


def test_a_running_task_is_left_alone_before_any_start():
    """IgnoreNew would drop a second start anyway; the script must not try."""
    script = st.start_script("alb-agent")
    assert script.index("-eq 'Running'") < script.index("Start-ScheduledTask")
    assert "exit 0" in script[script.index("-eq 'Running'") : script.index("Start-ScheduledTask")]


# ── reading the answer ───────────────────────────────────────────────


def test_markers_are_read_by_key_and_flags_keep_the_rest_of_the_line():
    marks, log = st.parse_output(
        _out(
            r"alb-start: dir=C:\a=b",
            "alb-start: start-failed Access is denied. code=5",
            r"alb-start: no-log D:\agent\logs\agent.log",
            "alb-log: one = two",
            "noise from the shell",
        )
    )
    assert marks == {
        "dir": r"C:\a=b",
        "start-failed": "Access is denied. code=5",
        "no-log": r"D:\agent\logs\agent.log",
    }
    assert log == ["one = two"]


# ── outcomes ─────────────────────────────────────────────────────────


def test_a_connected_agent_means_no_ssh_at_all():
    runner = _Runner()
    r = _run(_Registry(_Conn()), runner)
    assert r.ok and r.data["outcome"] == "already_connected" and runner.calls == []


def test_without_a_configured_host_nothing_is_run(monkeypatch):
    monkeypatch.delenv(st.SSH_HOST_ENV)
    runner = _Runner()
    r = _run(_Registry(), runner)
    assert r.code == "AGENT_START_NOT_CONFIGURED" and runner.calls == []


@pytest.mark.parametrize(
    ("env", "value"),
    [
        (st.SSH_HOST_ENV, "-oProxyCommand=evil"),  # would be read as an ssh option
        (st.SSH_HOST_ENV, "bench host"),
        (st.TASK_ENV, "alb'; Remove-Item C:\\x; '"),  # would break out of the quotes
    ],
)
def test_a_value_that_could_change_what_runs_is_refused(monkeypatch, env, value):
    monkeypatch.setenv(env, value)
    runner = _Runner()
    r = _run(_Registry(), runner)
    assert r.code == "AGENT_START_NOT_CONFIGURED" and runner.calls == []


def test_started_and_connected(monkeypatch):
    monkeypatch.setenv(st.TASK_ENV, "my-agent")
    runner = _Runner(_ok(_STARTED))
    events: list[FlashEvent] = []
    r = _run(_Registry(_Conn(), after_polls=3), runner, on_event=events.append)
    assert r.ok, r
    assert r.data["outcome"] == "started" and r.data["state_before"] == "Ready"
    assert r.data["agent"] == "bench-a" and r.data["bench_dir"] == r"D:\agent"
    assert "$name = 'my-agent'" in runner.script(0) and len(runner.calls) == 1
    assert [e.phase for e in events] == ["ssh", "wait"]


def test_a_running_task_whose_agent_comes_back_is_reconnected_not_started():
    runner = _Runner(
        _ok(_out(r"alb-start: dir=D:\agent", "alb-start: state=Running", "alb-start: action=none"))
    )
    r = _run(_Registry(_Conn(), after_polls=2), runner)
    assert r.ok and r.data["outcome"] == "reconnected"


@pytest.mark.parametrize(
    ("result", "said"),
    [
        (
            ProcessResult(
                exit_code=255,
                stdout="",
                stderr="user@bench: Permission denied (publickey).\r\n",
                duration_ms=1,
            ),
            "Permission denied (publickey)",
        ),
        (
            ProcessResult(exit_code=-1, stdout="", stderr="", duration_ms=1, binary_missing=True),
            "no ssh client",
        ),
        (
            ProcessResult(exit_code=-1, stdout="", stderr="", duration_ms=1, timed_out=True),
            "did not finish",
        ),
        # ssh got in, but what answered was not our script
        (
            ProcessResult(
                exit_code=1, stdout="'powershell' is not recognized\r\n", stderr="", duration_ms=1
            ),
            "not recognized",
        ),
    ],
)
def test_an_ssh_that_did_not_run_the_script_says_what_ssh_said(result, said):
    r = _run(_Registry(), _Runner(result))
    assert r.code == "AGENT_START_SSH_FAILED" and said in r.error


def test_no_task_points_at_the_setup_script():
    r = _run(_Registry(), _Runner(_ok(_out("alb-start: no-task"))))
    assert r.code == "AGENT_START_NO_TASK" and "setup-remote-start.bat" in r.error


def test_windows_refusing_the_start_is_passed_on():
    out = _out(
        r"alb-start: dir=D:\agent",
        "alb-start: state=Disabled",
        "alb-start: start-failed The task is disabled.",
    )
    r = _run(_Registry(), _Runner(_ok(out)))
    assert r.code == "AGENT_START_TASK_FAILED" and "The task is disabled." in r.error


def test_a_task_that_ended_at_once_fails_now_with_the_agent_log():
    """Not after the full wait: Ready 3 s after the start means nothing is
    going to connect (no one logged on, or run-agent.bat stopped)."""
    ended = _STARTED.replace("after=Running", "after=Ready").replace("267009", "1")
    runner = _Runner(_ok(ended), _ok(_TAIL))
    # 300 s of waiting allowed, 5 s given: an answer that waits fails here.
    r = asyncio.run(asyncio.wait_for(st.start_agent(_Registry(), runner=runner, wait_s=300), 5))
    assert r.code == "AGENT_START_TASK_FAILED"
    assert "Ready" in r.error and "last result 1" in r.error
    assert "Get-Content" in runner.script(1), "the second call reads the log"
    assert r.data["log_tail"][-1].endswith("[WinError 1225]")
    assert "WinError 1225" in r.stdout


@pytest.mark.parametrize("already", [False, True])
def test_no_agent_within_the_wait_fails_with_the_agent_log(already):
    first = (
        _out(r"alb-start: dir=D:\agent", "alb-start: state=Running", "alb-start: action=none")
        if already
        else _STARTED
    )
    runner = _Runner(_ok(first), _ok(_TAIL))
    r = _run(_Registry(), runner, wait_s=0.1)
    assert r.code == "AGENT_START_NO_CONNECT"
    assert r.data["log"] == r"D:\agent\logs\agent.log" and len(r.data["log_tail"]) == 2
    assert ("would change nothing" in r.error) is already


def test_a_missing_log_is_said_not_guessed():
    runner = _Runner(
        _ok(_STARTED),
        _ok(_out(r"alb-start: dir=D:\agent", r"alb-start: no-log D:\agent\logs\agent.log")),
    )
    r = _run(_Registry(), runner, wait_s=0.1)
    assert r.data["log"] == r"no log at D:\agent\logs\agent.log" and r.data["log_tail"] == []


def test_every_code_this_returns_has_a_catalog_entry():
    for code in (
        "AGENT_START_NOT_CONFIGURED",
        "AGENT_START_SSH_FAILED",
        "AGENT_START_NO_TASK",
        "AGENT_START_TASK_FAILED",
        "AGENT_START_NO_CONNECT",
    ):
        assert lookup(code) is not None, code


# ── endpoint ─────────────────────────────────────────────────────────


def test_the_endpoint_passes_only_the_wait(monkeypatch):
    import alb.api.bench_route as route

    seen: dict[str, Any] = {}

    async def fake_start(registry, **kw):
        seen.update(kw)
        kw["on_event"](FlashEvent(phase="ssh", text="ssh bench"))
        await asyncio.sleep(0)
        return FlashResult(ok=True, rc=0, data={"outcome": "started"})

    monkeypatch.setattr(route, "start_agent", fake_start)
    with TestClient(create_app()) as c:
        resp = c.post("/api/bench/agent-start", json={"wait_s": 12, "ssh_host": "elsewhere"})
    lines = [json.loads(x) for x in resp.text.splitlines() if x.strip()]
    assert lines[0]["phase"] == "ssh" and lines[-1]["data"] == {"outcome": "started"}
    assert set(seen) == {"wait_s", "on_event"} and seen["wait_s"] == 12


def test_the_endpoint_refuses_a_wait_out_of_range():
    with TestClient(create_app()) as c:
        assert c.post("/api/bench/agent-start", json={"wait_s": 1}).status_code == 422


# ── CLI ──────────────────────────────────────────────────────────────

runner = CliRunner()


@pytest.fixture
def hub(monkeypatch):
    state: dict[str, Any] = {"runs": [], "final": {}}

    def run_job(path, payload=None, render=None, *, base="/api/flash"):
        state["runs"].append((base, path, payload))
        return state["final"]

    monkeypatch.setattr(bench_cli, "_run_job", run_job)
    return state


def test_cli_sends_the_wait_and_reports_the_start(hub):
    hub["final"] = {
        "ev": "done",
        "ok": True,
        "stdout": "agent bench-a connected (build b1) after 4.2s",
        "data": {"outcome": "started", "task": "alb-agent", "host": "bench"},
    }
    r = runner.invoke(app, ["bench", "agent-start", "--wait", "30"])
    assert r.exit_code == 0, r.output
    assert hub["runs"] == [("/api/bench", "agent-start", {"wait_s": 30.0})]
    assert "started task alb-agent on bench" in r.output and "after 4.2s" in r.output


def test_cli_shows_the_agent_log_under_its_own_heading(hub):
    hub["final"] = {
        "ev": "done",
        "ok": False,
        "code": "AGENT_START_NO_CONNECT",
        "error": "task alb-agent started on bench, but no agent reached this hub within 60s",
        "stdout": "line a\nline b",
        "data": {"log": r"D:\agent\logs\agent.log", "log_tail": ["line a", "line b"]},
    }
    r = runner.invoke(app, ["bench", "agent-start"])
    assert r.exit_code == 1
    assert "AGENT_START_NO_CONNECT" in r.output and "hub_url / token" in r.output
    assert r"agent log on the bench host (D:\agent\logs\agent.log)" in r.output
    assert r.output.count("line a") == 1, "the tail is shown once, not also as stdout"


def test_cli_refuses_a_wait_out_of_range(hub):
    r = runner.invoke(app, ["bench", "agent-start", "--wait", "1"])
    assert r.exit_code != 0 and hub["runs"] == []
