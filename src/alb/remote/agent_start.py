"""Start the bench agent from the hub over ssh (ADR-061).

The agent dials out, so while it is not running the hub has no way to reach
it: someone had to double-click run-agent.bat on the bench host. With the
bench host set up once (`setup-remote-start.bat` in the agent folder: hub key
authorised for ssh, scheduled task `alb-agent` registered), the hub can ask
Windows to start that task and then wait for the agent's hello:

    hub ─ssh─> bench host ─Start-ScheduledTask─> alb-agent ─> run-agent.bat

The task, not ssh, starts the agent: a process started by an ssh session has
no window and ends with the session.

The ssh target comes only from the hub's own environment (ALB_BENCH_SSH_HOST,
a Host in the hub user's ~/.ssh/config) and the remote command is fixed here.
The hub API has no auth (ADR-060 cost), so a request can say how long to
wait, never where to ssh or what to run.

What the bench host says is read from marker lines the script prints, not
from its exit code (an `exit 3` in -EncodedCommand came back as 1 over ssh,
2026-10-20) and not from schtasks' text (localised: 「正在运行」). The task's
State is an enum name and reads the same in every language.

A task that is already Running is not started again — the task is registered
with MultipleInstancesPolicy=IgnoreNew, so Windows would ignore it anyway
(2026-10-20 on the bench: `schtasks /Run` answered "running" + "success" and
the agent's pid did not change). This is a start, not a restart.
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

from alb.infra import process
from alb.remote.flash import EventSink, FlashEvent, FlashResult

SSH_HOST_ENV = "ALB_BENCH_SSH_HOST"
TASK_ENV = "ALB_BENCH_AGENT_TASK"
DEFAULT_TASK = "alb-agent"  # what setup_remote_start.py registers
CONNECT_TIMEOUT_S = 60.0
MIN_WAIT_S, MAX_WAIT_S = 5.0, 300.0
SSH_CONNECT_TIMEOUT_S = 10
SSH_TIMEOUT_S = 45.0
LOG_TAIL_LINES = 20
_POLL_S = 0.5
# Seconds the bench host waits after starting the task before reading its
# state back: long enough for a run-agent.bat that fails at once to end.
_SETTLE_S = 3

# No quotes, no leading '-': the task name is pasted into the script between
# single quotes, the host is an ssh argument.
_TASK_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}\Z")
_HOST_RE = re.compile(r"^[^\s\-]\S{0,252}\Z")
_MARK = "alb-start: "
_LOG_MARK = "alb-log: "

Runner = Callable[..., Awaitable[process.ProcessResult]]

_PRELUDE = """$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$name = '{task}'
try {{ $t = Get-ScheduledTask -TaskName $name -ErrorAction Stop }} catch {{ 'alb-start: no-task'; exit 3 }}
$wd = [string]$t.Actions[0].WorkingDirectory
'alb-start: dir=' + $wd
"""

_START = """'alb-start: state=' + [string]$t.State
if ([string]$t.State -eq 'Running') {{ 'alb-start: action=none'; exit 0 }}
try {{ Start-ScheduledTask -TaskName $name -ErrorAction Stop }} catch {{
  'alb-start: start-failed ' + $_.Exception.Message; exit 4 }}
'alb-start: action=started'
Start-Sleep -Seconds {settle}
$i = Get-ScheduledTaskInfo -TaskName $name
'alb-start: after=' + [string](Get-ScheduledTask -TaskName $name).State
'alb-start: last-result=' + [string]$i.LastTaskResult
"""

# The agent writes logs\\agent.log under its folder unless agent.conf says
# otherwise (log_file); a different path is reported as no-log, not guessed.
_TAIL = """$log = Join-Path $wd 'logs\\agent.log'
if (Test-Path -LiteralPath $log) {{
  'alb-start: log=' + $log
  Get-Content -LiteralPath $log -Tail {lines} -Encoding UTF8 | ForEach-Object {{ 'alb-log: ' + $_ }}
}} else {{ 'alb-start: no-log ' + $log }}
"""


def start_script(task: str, *, settle_s: int = _SETTLE_S) -> str:
    return _PRELUDE.format(task=task) + _START.format(settle=settle_s)


def tail_script(task: str, *, lines: int = LOG_TAIL_LINES) -> str:
    return _PRELUDE.format(task=task) + _TAIL.format(lines=lines)


def encode_ps(script: str) -> str:
    """PowerShell -EncodedCommand form: base64 of UTF-16LE. Passing the
    script as plain -Command text loses `$_` on the way through the remote
    shell (2026-10-20: `.CommandLine` is not recognised)."""
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def ssh_argv(host: str, script: str) -> list[str]:
    return [
        "ssh",
        "-o",
        "BatchMode=yes",  # never wait on a password or host-key prompt
        "-o",
        f"ConnectTimeout={SSH_CONNECT_TIMEOUT_S}",
        "--",
        host,
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-EncodedCommand",
        encode_ps(script),
    ]


def parse_output(text: str) -> tuple[dict[str, str], list[str]]:
    """Marker lines -> {key: value}; flag-only markers map to their rest of
    line ('' when there is none). Log lines are returned in order."""
    marks: dict[str, str] = {}
    log: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        if line.startswith(_LOG_MARK):
            log.append(line[len(_LOG_MARK) :])
        elif line.startswith(_MARK):
            body = line[len(_MARK) :]
            key, sep, value = body.partition("=")
            if sep and " " not in key:
                marks[key] = value
            else:
                key, _, rest = body.partition(" ")
                marks[key] = rest
    return marks, log


def _say(sink: EventSink | None, phase: str, text: str) -> None:
    if sink is not None:
        sink(FlashEvent(phase=phase, text=text))


def _fail(
    code: str, error: str, data: dict[str, Any] | None = None, stdout: str = ""
) -> FlashResult:
    return FlashResult(ok=False, code=code, error=error, stdout=stdout, data=dict(data or {}))


def _tail(text: str, n: int = 5) -> str:
    return "\n".join(text.strip().splitlines()[-n:])


async def _ssh(
    runner: Runner, host: str, script: str
) -> FlashResult | tuple[dict[str, str], list[str]]:
    """Run one script on the bench host; a FlashResult means ssh itself failed."""
    res = await runner(*ssh_argv(host, script), timeout=SSH_TIMEOUT_S)
    if res.binary_missing:
        return _fail("AGENT_START_SSH_FAILED", "no ssh client on the hub host")
    if res.timed_out:
        return _fail(
            "AGENT_START_SSH_FAILED", f"ssh {host} did not finish within {SSH_TIMEOUT_S:.0f}s"
        )
    marks, log = parse_output(res.stdout)
    if not marks:
        # ssh refused, the host is unreachable, or no PowerShell ran there.
        said = _tail(res.stderr) or _tail(res.stdout) or f"exit {res.exit_code}, no output"
        return _fail("AGENT_START_SSH_FAILED", f"ssh {host}: {said}")
    return marks, log


async def _log_tail(runner: Runner, host: str, task: str) -> tuple[str, list[str]]:
    """(where, lines) of the agent's log on the bench host; best effort."""
    got = await _ssh(runner, host, tail_script(task))
    if isinstance(got, FlashResult):
        return "", []
    marks, log = got
    if "no-log" in marks:
        return f"no log at {marks['no-log']}", []
    return marks.get("log", ""), log


def _connected(conn: Any, outcome: str, started: float, data: dict[str, Any]) -> FlashResult:
    took = time.monotonic() - started
    return FlashResult(
        ok=True,
        rc=0,
        stdout=f"agent {conn.name} connected (build {conn.build or '?'}) after {took:.1f}s",
        duration_s=took,
        data={
            **data,
            "outcome": outcome,
            "agent": conn.name,
            "agent_id": conn.agent_id,
            "build": conn.build,
        },
    )


async def start_agent(
    registry: Any,
    *,
    wait_s: float = CONNECT_TIMEOUT_S,
    ssh_host: str | None = None,
    task: str | None = None,
    on_event: EventSink | None = None,
    runner: Runner = process.run,
) -> FlashResult:
    """Start the bench agent's scheduled task unless an agent is connected,
    then wait up to `wait_s` for it to reach this hub."""
    started = time.monotonic()
    conn = registry.current_agent()
    if conn is not None:
        return FlashResult(
            ok=True,
            rc=0,
            stdout=f"agent {conn.name} is already connected (build {conn.build or '?'}); "
            "nothing was started",
            data={
                "outcome": "already_connected",
                "agent": conn.name,
                "agent_id": conn.agent_id,
                "build": conn.build,
            },
        )

    host = (ssh_host if ssh_host is not None else os.environ.get(SSH_HOST_ENV, "")).strip()
    task = (task if task is not None else os.environ.get(TASK_ENV, "")).strip() or DEFAULT_TASK
    if not host:
        return _fail(
            "AGENT_START_NOT_CONFIGURED",
            f"{SSH_HOST_ENV} is not set for this hub — set it to the ~/.ssh/config Host "
            "of the bench host and restart alb-api",
        )
    if not _HOST_RE.match(host):
        return _fail(
            "AGENT_START_NOT_CONFIGURED", f"{SSH_HOST_ENV}={host!r} is not a usable ssh host"
        )
    if not _TASK_RE.match(task):
        return _fail("AGENT_START_NOT_CONFIGURED", f"{TASK_ENV}={task!r} is not a usable task name")
    wait_s = min(max(wait_s, MIN_WAIT_S), MAX_WAIT_S)

    _say(on_event, "ssh", f"ssh {host}: start scheduled task {task}")
    got = await _ssh(runner, host, start_script(task))
    if isinstance(got, FlashResult):
        return got
    marks, _ = got
    data: dict[str, Any] = {
        "host": host,
        "task": task,
        "state_before": marks.get("state", ""),
        "bench_dir": marks.get("dir", ""),
    }
    if "no-task" in marks:
        return _fail(
            "AGENT_START_NO_TASK",
            f"no scheduled task {task!r} on {host} — run setup-remote-start.bat in the "
            "agent folder there once",
            data,
        )
    if "start-failed" in marks:
        return _fail(
            "AGENT_START_TASK_FAILED",
            f"Windows refused to start task {task!r} on {host}: {marks['start-failed']}",
            data,
        )

    already = marks.get("action") == "none"
    if already:
        _say(
            on_event,
            "wait",
            f"task {task} is already running on {host} (not started again); "
            "waiting for its agent to reach the hub",
        )
    else:
        data["state_after"] = marks.get("after", "")
        data["last_result"] = marks.get("last-result", "")
        if data["state_after"] != "Running":
            # Ended within seconds (run-agent.bat failed), or never ran — the
            # task only runs in a logged-on user's desktop session.
            where, log = await _log_tail(runner, host, task)
            data["log"], data["log_tail"] = where, log
            return _fail(
                "AGENT_START_TASK_FAILED",
                f"task {task} on {host} is {data['state_after'] or '?'} {_SETTLE_S}s after "
                f"the start (last result {data['last_result'] or '?'}) — no one logged on "
                "to the bench host, or run-agent.bat ended at once",
                data,
                stdout="\n".join(log),
            )
        _say(on_event, "wait", f"task {task} started on {host}; waiting for the agent to connect")

    outcome = "reconnected" if already else "started"
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_s
    while loop.time() < deadline:
        conn = registry.current_agent()
        if conn is not None:
            return _connected(conn, outcome, started, data)
        await asyncio.sleep(_POLL_S)

    where, log = await _log_tail(runner, host, task)
    data["log"], data["log_tail"] = where, log
    if already:
        why = (
            f"task {task} was already running on {host}, but no agent reached this hub "
            f"within {wait_s:.0f}s — it is stuck or pointed at another hub / token; "
            "starting it again would change nothing"
        )
    else:
        why = f"task {task} started on {host}, but no agent reached this hub within {wait_s:.0f}s"
    return _fail("AGENT_START_NO_CONNECT", why, data, stdout="\n".join(log))
