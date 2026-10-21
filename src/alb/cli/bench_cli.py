"""CLI: `alb bench` — ops on the bench host, from the agent's own table (ADR-059).

    alb bench ops                               what this agent can do
    alb bench run <op> [name=value ...] [--image <workspace path>] [--yes]
    alb bench usb                               USB devices on the bench host, with the
                                                mode each board is in
    alb bench log <source> [-n LINES] [-g TEXT] tail a log on the bench host
    alb bench agent-update [--config FILE]      send the hub's agent files to the bench
                                                host and restart the agent there
    alb bench agent-start [--wait SECONDS]      start the bench agent over ssh when none
                                                is connected
    alb bench board                             the bench boards and which one the serial
                                                console points at
    alb bench board use <name> [--force]        point the serial console at another board
                                                (its COM port and baud), hub keeps running

`run` is the generic form: any op the agent reports, parameters by name.
`usb` and `log` are short forms for the two read-only ops people reach for
when a board is not where they expect it. `alb flash ...` stays as it was —
it is the same path under the fastboot names.

Like `alb flash`, this is a client of alb-api: the ops run on the agent's
host, which only the hub can reach.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import typer

from alb.cli.flash_cli import _explain, _hub_unreachable, _report_artifacts, _run_job, hub_url

app = typer.Typer(help="Ops on the bench host, from the agent's op table.")


def _fetch_ops() -> dict[str, Any]:
    try:
        with httpx.Client(timeout=httpx.Timeout(10.0)) as client:
            resp = client.get(f"{hub_url()}/api/bench/ops")
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            return data
    except httpx.HTTPError as e:
        _hub_unreachable(e)
    raise AssertionError("unreachable")  # _hub_unreachable exits


def _param_text(p: dict[str, Any]) -> str:
    kind = p.get("kind")
    if kind == "enum":
        shape = "|".join(p.get("choices") or [])
    elif kind == "int":
        shape = f"{p.get('min')}..{p.get('max')}"
    else:
        shape = str(kind)
    text = f"{p.get('name')}=<{shape}>"
    if not p.get("required", True):
        text = f"[{text}, default {p.get('default')!r}]"
    return text


def _parse_assignments(items: list[str]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for item in items:
        name, sep, value = item.partition("=")
        if not sep or not name:
            typer.echo(f"[fail] expected name=value, got {item!r}", err=True)
            raise typer.Exit(code=2)
        params[name] = value  # the hub turns decimal strings into ints by the op's kinds
    return params


def _finish(final: dict[str, Any], *, as_json: bool = False) -> dict[str, Any]:
    """Print the outcome; exit 1 on failure. Returns the done frame."""
    if as_json:
        typer.echo(json.dumps(final, ensure_ascii=False, indent=2))
        if not final.get("ok"):
            raise typer.Exit(code=1)
        return final
    artifacts = str(final.get("artifacts") or "")
    if not final.get("ok"):
        code = str(final.get("code") or "")
        message, advice = _explain(code, str(final.get("error") or ""))
        typer.echo(f"[fail] {code or 'error'}: {message}", err=True)
        if advice:
            typer.echo(f"  try: {advice}", err=True)
        for stream in ("stdout", "stderr"):
            text = str(final.get(stream) or "").strip()
            if text:
                typer.echo(f"  {stream}: {text}", err=True)
        _report_artifacts(artifacts, err=True)
        raise typer.Exit(code=1)
    return final


@app.command("ops")
def cmd_ops(as_json: bool = typer.Option(False, "--json", help="raw table")) -> None:
    """List what the agent on the bench host can do, with parameters."""
    data = _fetch_ops()
    if as_json:
        typer.echo(json.dumps(data, ensure_ascii=False, indent=2))
        return
    agent = data.get("agent")
    if not agent:
        typer.echo("[fail] no agent is connected to the hub", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"agent {agent.get('name') or agent.get('id')}")
    build = str(agent.get("build") or "")
    bundle = str(data.get("bundle_build") or "")
    if bundle and build != bundle:
        typer.echo(
            f"  build {build or '(older than build reporting)'} — this hub has {bundle}; "
            "`alb bench agent-update` brings the bench host up to date"
        )
    elif build:
        typer.echo(f"  build {build} (same as this hub's copy)")
    if not data.get("reported"):
        typer.echo(
            "  this agent predates the op table — the list below is inferred from its "
            "caps; update alb_agent.py on the bench host for the full set"
        )
    for op in data.get("ops") or []:
        flags = []
        if op.get("mutates"):
            flags.append("changes device")
        if op.get("needs") == "fastboot-device":
            flags.append("needs fastboot")
        if op.get("image"):
            flags.append("takes --image")
        params = " ".join(_param_text(p) for p in op.get("params") or [])
        typer.echo(f"  {op.get('name'):<12} {params}")
        detail = op.get("summary") or ""
        if flags:
            detail = f"{detail}  ({', '.join(flags)})" if detail else f"({', '.join(flags)})"
        if detail:
            typer.echo(f"  {'':<12} {detail}")
    partitions = data.get("partitions") or []
    typer.echo(
        f"  partition allowlist: {', '.join(partitions)}"
        if partitions
        else "  partition allowlist: none configured (any well-formed name)"
    )


@app.command("run")
def cmd_run(
    op: str = typer.Argument(..., help="op name, see `alb bench ops`"),
    params: list[str] = typer.Argument(None, help="parameters as name=value"),
    image: Path | None = typer.Option(
        None, "--image", help="image for image ops (path under the alb workspace)"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="skip the confirmation"),
    as_json: bool = typer.Option(False, "--json", help="print the done frame as JSON"),
) -> None:
    """Run one op from the agent's table.

    Ops that change the device ask for confirmation first, read from the
    table rather than from a list in this command — and an agent that did
    not report its table has every op treated as one that changes the device.
    """
    values = _parse_assignments(params or [])
    table = {o.get("name"): o for o in _fetch_ops().get("ops") or []}
    spec = table.get(op)
    if spec is not None and spec.get("mutates", True) and not yes:
        shown = " ".join(f"{k}={v}" for k, v in values.items())
        target = f" with {image}" if image else ""
        typer.echo(f"about to run `{op} {shown}`{target} on the bench — it changes the device")
        typer.confirm("proceed?", abort=True)
    payload: dict[str, Any] = {"params": values}
    if image is not None:
        payload["image"] = str(image)
    final = _finish(_run_job(f"ops/{op}", payload, base="/api/bench"), as_json=as_json)
    if as_json:
        return
    typer.echo(f"[ok] {final.get('duration_s', 0)}s")
    out = str(final.get("stdout") or "").strip()
    if out:
        typer.echo(out)
    if final.get("data"):
        typer.echo(json.dumps(final["data"], ensure_ascii=False, indent=2))
    _report_artifacts(str(final.get("artifacts") or ""))


@app.command("usb")
def cmd_usb(
    show_all: bool = typer.Option(False, "--all", "-a", help="also list non-board devices"),
    as_json: bool = typer.Option(False, "--json", help="print the done frame as JSON"),
) -> None:
    """USB devices on the bench host, and what mode each board is in.

    The question this answers: the board should be in fastboot (or adb), and
    the bench does not see it. A board listed with `driver: missing` needs a
    driver on the bench host; no board listed at all means the USB cable is
    not on that host, or the board is not enumerating."""
    final = _finish(_run_job("ops/usb-list", {"params": {}}, base="/api/bench"), as_json=as_json)
    if as_json:
        return
    data = final.get("data") or {}
    devices = data.get("devices") or []
    boards = data.get("boards") or []
    if boards:
        typer.echo(f"[ok] {len(boards)} board(s) on the bench host:")
        for b in boards:
            # A Windows-made id is not the board's serial; shown apart so it is
            # not matched against `adb devices`, but kept to tell boards apart.
            windows_id = f" (windows id {b['windows_id']})" if b.get("windows_id") else ""
            typer.echo(
                f"  {b.get('mode'):<20} {b.get('vid')}:{b.get('pid')}  "
                f"serial {b.get('serial') or '-'}{windows_id}  driver: {b.get('driver')}  "
                f"{b.get('name')}"
            )
            if b.get("hint"):
                typer.echo(f"    {b['hint']}")
    else:
        typer.echo(
            f"[ok] none of the {len(devices)} USB device(s) on the bench host is a board in "
            "adb, fastboot or a known download mode"
        )
        typer.echo(
            "     if the board should be there: is its USB cable on the bench host, and "
            "does it enumerate at all? (`--all` lists every device)"
        )
    if show_all:
        for d in devices:
            vid_pid = f"{d.get('vid') or '----'}:{d.get('pid') or '----'}"
            typer.echo(
                f"  - {vid_pid}  {d.get('mode', 'other'):<20} driver: {d.get('driver')}  "
                f"{d.get('name') or ''}"
            )


@app.command("log")
def cmd_log(
    source: str = typer.Argument(..., help="log source, see `alb bench ops` (e.g. agent)"),
    lines: int = typer.Option(200, "--lines", "-n", help="last N lines"),
    grep: str = typer.Option("", "--grep", "-g", help="keep only lines containing this text"),
) -> None:
    """Tail a log on the bench host, by source name (never a path)."""
    params: dict[str, Any] = {"source": source, "lines": lines}
    if grep:
        params["grep"] = grep
    final = _finish(_run_job("ops/log-tail", {"params": params}, base="/api/bench"))
    data = final.get("data") or {}
    text = str(final.get("stdout") or "")
    if text:
        typer.echo(text.rstrip("\n"))
    note = " (older lines exist beyond the window read)" if data.get("truncated") else ""
    typer.echo(f"[ok] {data.get('lines', 0)} line(s) from {data.get('source', source)}{note}")


@app.command("agent-update")
def cmd_agent_update(
    config: Path | None = typer.Option(
        None,
        "--config",
        help="also replace the bench agent's agent.conf with this file "
        "(default: keep the one on the bench host)",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="skip the confirmation"),
    as_json: bool = typer.Option(False, "--json", help="print the done frame as JSON"),
) -> None:
    """Send the hub's clients/windows-agent files to the bench host and
    restart the agent there (ADR-060).

    The bench host stops only THIS agent (another agent with its own folder
    keeps running), swaps the files and starts the new build. If the new
    build does not reach the hub within 60 s, the old files go back and the
    old build starts again — the result then says AGENT_UPDATE_ROLLED_BACK.
    """
    payload: dict[str, Any] = {}
    if config is not None:
        try:
            # Bytes, not text mode: keep the file's line endings exactly.
            payload["config_text"] = config.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as e:
            typer.echo(f"[fail] cannot read {config}: {e}", err=True)
            raise typer.Exit(code=2) from e
    if not yes:
        what = " and its agent.conf" if config is not None else ""
        typer.echo(
            f"about to replace the bench agent's files{what} and restart it — "
            "the bench link drops for a few seconds"
        )
        typer.confirm("proceed?", abort=True)
    final = _finish(_run_job("agent-update", payload, base="/api/bench"), as_json=as_json)
    if as_json:
        return
    data = final.get("data") or {}
    if not data.get("restarting"):
        typer.echo(f"[ok] {str(final.get('stdout') or '').strip() or 'nothing to change'}")
        return
    typer.echo(
        f"[ok] updated: build {data.get('from_build')} -> {data.get('build')} "
        f"({final.get('duration_s', 0)}s)"
    )
    changed = data.get("changed") or []
    if changed:
        typer.echo(f"  changed: {', '.join(str(c) for c in changed)}")


@app.command("agent-start")
def cmd_agent_start(
    wait: float = typer.Option(
        60.0, "--wait", min=5.0, max=300.0, help="seconds to wait for the agent to connect"
    ),
    as_json: bool = typer.Option(False, "--json", help="print the done frame as JSON"),
) -> None:
    """Start the bench agent over ssh when no agent is connected (ADR-061).

    The hub runs the bench host's scheduled task `alb-agent` (set up once
    with setup-remote-start.bat there) and waits for the agent to connect.
    The ssh host is the hub's ALB_BENCH_SSH_HOST. A connected agent is left
    alone, and a task that is already running is not started again: this
    is not a restart (Windows ignores a second run of a running task).
    """
    final = _run_job("agent-start", {"wait_s": wait}, base="/api/bench")
    if as_json:
        _finish(final, as_json=True)
        return
    data = final.get("data") or {}
    if not final.get("ok"):
        try:
            # The log tail rides in stdout; shown below under its own heading.
            _finish({**final, "stdout": ""})
        finally:
            where = str(data.get("log") or "")
            tail = data.get("log_tail") or []
            if tail:
                typer.echo(f"  agent log on the bench host ({where}), last lines:", err=True)
                for line in tail:
                    typer.echo(f"    {line}", err=True)
            elif where:
                typer.echo(f"  agent log: {where}", err=True)
        return
    outcome = data.get("outcome")
    if outcome == "already_connected":
        typer.echo(f"[ok] {final.get('stdout')}")
    elif outcome == "reconnected":
        typer.echo(
            f"[ok] task {data.get('task')} was already running on {data.get('host')}; "
            f"{final.get('stdout')}"
        )
    else:
        typer.echo(
            f"[ok] started task {data.get('task')} on {data.get('host')}; {final.get('stdout')}"
        )


# ── which board the serial console points at (ADR-063) ──────────────

board_app = typer.Typer(
    help="Which bench board the serial console points at.", invoke_without_command=True
)
app.add_typer(board_app, name="board")


def _board_request(method: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=httpx.Timeout(30.0)) as client:
            resp = client.request(method, f"{hub_url()}/api/bench/board", json=body)
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            return data
    except httpx.HTTPError as e:
        _hub_unreachable(e)
    raise AssertionError("unreachable")  # _hub_unreachable exits


def _print_boards(data: dict[str, Any]) -> None:
    serial = data.get("serial") or {}
    where = f"{serial.get('com')} @ {serial.get('baud')}" if serial.get("com") else "not configured"
    active = data.get("active")
    source = f"board {active}" if active else "hub.env (no board chosen)"
    typer.echo(f"serial console: {where} · from {source} · readers {serial.get('readers', 0)}")
    if data.get("problem"):
        typer.echo(f"  [!] {data['problem']}")
    boards = data.get("boards") or []
    if not boards:
        typer.echo(
            "  no board table — set ALB_BENCH_BOARDS in deploy/hub/hub.env "
            "(see deploy/hub/boards.toml.example)"
        )
        return
    typer.echo(f"  boards ({data.get('table')}):")
    for b in boards:
        mark = "*" if b.get("name") == active else " "
        note = f"  {b['note']}" if b.get("note") else ""
        typer.echo(f"  {mark} {b['name']:<16} {b['serial_com']} @ {b['serial_baud']}{note}")


@board_app.callback()
def board_show(
    ctx: typer.Context,
    as_json: bool = typer.Option(False, "--json", help="raw answer"),
) -> None:
    """List the bench boards and which one the serial console points at."""
    if ctx.invoked_subcommand is not None:
        return
    data = _board_request("GET")
    if as_json:
        typer.echo(json.dumps(data, ensure_ascii=False, indent=2))
        return
    _print_boards(data)


@board_app.command("use")
def board_use(
    name: str = typer.Argument(..., help="a board from `alb bench board`"),
    force: bool = typer.Option(
        False, "--force", help="cut off whoever reads the serial console right now"
    ),
    as_json: bool = typer.Option(False, "--json", help="raw answer"),
) -> None:
    """Point the serial console at another board, without restarting the hub.

    The hub asks the agent for that board's COM port and baud from the next
    serial open on, and remembers the choice across restarts. Refused while
    a job runs, and while someone reads the console unless --force. adb and
    fastboot are not touched.
    """
    data = _board_request("POST", {"name": name, "force": force})
    if as_json:
        typer.echo(json.dumps(data, ensure_ascii=False, indent=2))
        if not data.get("ok"):
            raise typer.Exit(code=1)
        return
    if not data.get("ok"):
        code = str(data.get("code") or "")
        message, advice = _explain(code, str(data.get("error") or ""))
        typer.echo(f"[fail] {code or 'error'}: {message}", err=True)
        if advice:
            typer.echo(f"  try: {advice}", err=True)
        raise typer.Exit(code=1)
    result = data.get("data") or {}
    board = result.get("board") or {}
    previous = result.get("previous") or {}
    typer.echo(
        f"[ok] serial console -> {board.get('name')} "
        f"({board.get('serial_com')} @ {board.get('serial_baud')}); "
        f"was {previous.get('com')} @ {previous.get('baud')}"
    )
    if result.get("cut_off"):
        typer.echo(f"  cut off {result['cut_off']} reader(s)")
    if not result.get("bound"):
        typer.echo("  no agent connected yet: the console opens on this board once it is")
