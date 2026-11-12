"""CLI: `alb flash` — fastboot jobs through the hub (ADR-056).

Unlike every other alb command, this one talks to alb-api over HTTP instead
of building a transport. That is not an inconsistency, it is the topology:
adb and serial reach the device through an OS-level forwarder port that the
hub opens, so a CLI process can just connect to it. fastboot has no such
port — the tool speaks USB on the agent's host, and the only thing that can
reach it is the hub's agent connection. So the hub runs the job and this
command is a client of it.

    alb flash status
    alb flash devices
    alb flash reboot [target]
    alb flash oem <command>
    alb flash write <partition> <image>
    alb flash erase <partition>
    alb flash plan <manifest> [--skip p1,p2] [--skip-action a] [--from-line N] [--dry-run] [--yes]
    alb flash tools
    alb flash tool <name> [--pkg <dir>] [--yes] -- <the tool's own arguments>

`write`, not `flash flash`: the verb reads as what it does to the device.
"""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path
from typing import Any

import httpx
import typer

app = typer.Typer(help="Flash / fastboot via the connected device agent.")

# Plain typer.echo, not rich: every line here can carry text produced by
# fastboot on the agent host, and rich eats square brackets in it as markup —
# fastboot's own progress output is full of them, and the `[ok]` / `[fail]`
# markers vanished for exactly that reason. Matches serial_cli's convention.

# A partition write can legitimately run for minutes; the hub applies its own
# ceiling (flash.JOB_TIMEOUT_S). A short client timeout here would only make
# the CLI lie about an operation that is still happening.
_READ_TIMEOUT_S = 40 * 60.0
_CONNECT_TIMEOUT_S = 10.0
# The hub's defaults for keeping the UART after a reboot to the system
# (alb.remote.flash.UART_AFTER_REBOOT_S / UART_AFTER_MAX_S). Repeated, not
# imported: this CLI talks HTTP and does not load the hub's modules.
UART_AFTER_REBOOT_S = 90.0
UART_AFTER_MAX_S = 600.0


def hub_url() -> str:
    """Where alb-api lives. ALB_API_URL overrides for a remote hub."""
    base = os.environ.get("ALB_API_URL", "").strip()
    if base:
        return base.rstrip("/")
    host = os.environ.get("ALB_API_HOST", "127.0.0.1")
    if host == "0.0.0.0":  # bind-all is not a dial-able address
        host = "127.0.0.1"
    return f"http://{host}:{os.environ.get('ALB_API_PORT', '8765')}"


def _timeout() -> httpx.Timeout:
    return httpx.Timeout(_READ_TIMEOUT_S, connect=_CONNECT_TIMEOUT_S)


def _hub_unreachable(e: Exception) -> None:
    typer.echo(f"[fail] cannot reach alb-api at {hub_url()}: {e}", err=True)
    typer.echo("        fastboot runs on the hub — start alb-api, or set ALB_API_URL", err=True)
    raise typer.Exit(code=2)


def _render_progress(msg: dict[str, Any], state: dict[str, Any]) -> None:
    """One line per phase change, overwritten in place for the byte counter.

    Deliberately not a progress bar library: this output is read as often by
    an LLM agent reading a log as by a human watching a terminal, and a
    redrawn bar turns into thousands of control characters in a transcript.
    """
    phase = msg.get("phase", "")
    done, total = int(msg.get("done") or 0), int(msg.get("total") or 0)
    text = str(msg.get("text") or "")
    if text:
        typer.echo(f"  {phase}: {text}")
        return
    if total > 0:
        pct = done * 100 // total
        # Only speak at 10% steps: a 2 GB image at 64 KiB chunks would
        # otherwise emit 32k lines.
        step = pct // 10
        if state.get("step") != step:
            state["step"] = step
            typer.echo(f"  {phase}: {done}/{total} bytes ({pct}%)")


def _print_rejection(resp: httpx.Response) -> None:
    """Show why the hub said no. `detail` is a string for most refusals and
    `{"code", "problems": [...]}` for a flash plan — one problem per line,
    because a plan refusal is a list to work through, not a sentence."""
    typer.echo(f"[fail] hub rejected the request ({resp.status_code})", err=True)
    detail: Any = ""
    try:
        detail = resp.json().get("detail", "")
    except Exception:
        # The body need not be JSON — an error page or a proxy's text is
        # still worth showing verbatim.
        detail = resp.text[:400]
    if isinstance(detail, dict):
        if detail.get("code"):
            typer.echo(f"  {detail['code']}", err=True)
        for problem in detail.get("problems") or []:
            typer.echo(f"  - {problem}", err=True)
    elif detail:
        typer.echo(f"  {detail}", err=True)


def _run_job(
    path: str,
    payload: dict[str, Any] | None = None,
    render: Any = None,
    *,
    base: str = "/api/flash",
) -> dict[str, Any]:
    """POST and consume the NDJSON stream; return the terminal `done` frame.
    `base` lets `alb bench` reuse this for /api/bench/ops/<op>."""
    url = f"{hub_url()}{base}/{path}"
    render = render or _render_progress
    state: dict[str, Any] = {}
    final: dict[str, Any] = {}
    try:
        with (
            httpx.Client(timeout=_timeout()) as client,
            client.stream("POST", url, json=payload or {}) as resp,
        ):
            if resp.status_code >= 400:
                resp.read()
                _print_rejection(resp)
                raise typer.Exit(code=2)
            for line in resp.iter_lines():
                if not line.strip():
                    continue
                msg = json.loads(line)
                if msg.get("ev") == "progress":
                    render(msg, state)
                elif msg.get("ev") == "done":
                    final = msg
    except httpx.HTTPError as e:
        _hub_unreachable(e)
    if not final:
        # The stream ended without a verdict. Say exactly that — "no result"
        # is not "failed", and after a partition write the difference matters.
        typer.echo("[fail] the hub closed the stream without reporting a result", err=True)
        typer.echo(
            "        the device state is unknown — check the hub log before retrying", err=True
        )
        raise typer.Exit(code=3)
    return final


# Wording a failing tool emits when it has nothing specific to say. Treated
# as "no message" so the catalog's sentence is used instead — printing
# `FASTBOOT_NO_DEVICE: fastboot failed` tells the reader nothing they did not
# already see in the code.
_EMPTY_ERRORS = {"", "fastboot failed", "error", "failed", "unknown"}


def _explain(code: str, error: str) -> tuple[str, str]:
    """(what happened, what to do) for a failure.

    The agent reports a code; the catalog in `infra.errors` already holds a
    sentence and a remedy for it. Not consulting it was the bug: the code was
    right and the message pointed nowhere, which is the same failure mode as
    an error code pointing at the wrong subsystem — the reader still has to
    go and find out for themselves."""
    from alb.infra.errors import lookup

    spec = lookup(code) if code else None
    message = error.strip()
    if message.lower() in _EMPTY_ERRORS:
        message = spec.default_message if spec else "unknown failure"
    return message, (spec.default_suggestion if spec else "")


def _report(final: dict[str, Any]) -> None:
    artifacts = str(final.get("artifacts") or "")
    if final.get("ok"):
        out = str(final.get("stdout") or "").strip()
        typer.echo(f"[ok] {final.get('duration_s', 0)}s")
        if out:
            typer.echo(out)
        _report_artifacts(artifacts)
        return
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


def _report_artifacts(directory: str, *, err: bool = False) -> None:
    """Point at the recording. Named specifically rather than "see the logs":
    after a flash that did not come back, `timeline.jsonl` — job events and
    UART lines on one clock — is where the answer is, and nobody finds a file
    they were not told about."""
    if not directory:
        return
    typer.echo(f"  record: {directory}/timeline.jsonl (job + UART, one timeline)", err=err)


@app.command("status")
def cmd_status() -> None:
    """Is a fastboot-capable agent connected, is it busy, and may a partition be written?

    Exit 1 when no partition may be written — `ready` alone was misleading
    once the agent kept a list per board (ADR-063): devices/getvar worked,
    and every write was refused.
    """
    try:
        with httpx.Client(timeout=httpx.Timeout(10.0)) as client:
            data = client.get(f"{hub_url()}/api/flash/status").json()
    except httpx.HTTPError as e:
        _hub_unreachable(e)
    if not data.get("available"):
        why = data.get("unavailable") or {}
        if why.get("message"):
            typer.echo(f"[fail] {why.get('code')}: {why['message']}")
            typer.echo(f"        {why.get('suggestion') or ''}".rstrip())
        else:  # a hub from before the reason was reported
            typer.echo("[fail] fastboot unavailable")
            typer.echo(
                "        no connected agent advertises the fastboot capability — "
                "set fastboot_path in the agent's agent.conf and restart it"
            )
        raise typer.Exit(code=1)
    if data.get("busy"):
        typer.echo(f"[busy] {data.get('job') or 'a job is running'}")
        return
    problem = str(data.get("partitions_problem") or "")
    partitions = [str(p) for p in data.get("partitions") or []]
    board = str(data.get("board") or "")
    if problem:
        typer.echo("[fail] ready, but no partition may be written")
        typer.echo(f"        {problem}")
        raise typer.Exit(code=1)
    typer.echo("[ok] ready")
    if board:
        typer.echo(f"        board {board} · partitions {', '.join(partitions)}")
    elif partitions:
        typer.echo(f"        partitions {', '.join(partitions)}")
    else:
        typer.echo("        partitions: none configured (any well-formed name)")


@app.command("devices")
def cmd_devices() -> None:
    """`fastboot devices` on the agent host — the only way to see a board
    that has dropped off adb by entering fastboot."""
    _report(_run_job("devices"))


@app.command("getvar")
def cmd_getvar(
    name: str = typer.Argument(
        "", help='variable name, e.g. partition-size:cfg; empty = getvar all'
    ),
) -> None:
    """`fastboot getvar <name>` — ask the device and print what it said.

    Deliberately prints the answer raw. `getvar` is a protocol-level verb and
    survives a platform change, but **what the values mean does not**: this
    bench answers `partition-size:cfg` with `0` on flashes that succeed, so
    anything that translated that into "the partition is missing" would be
    shipping a misreading. Read the device's own words.
    """
    _report(_run_job("getvar", {"name": name}))


@app.command("reboot")
def cmd_reboot(
    target: str = typer.Argument("", help="empty = back to the system; or bootloader/recovery"),
    uart_after: float = typer.Option(
        UART_AFTER_REBOOT_S,
        "--uart-after",
        help="back to the system: keep recording the UART this many seconds after the "
        "reboot, so the record holds the first boot (0 = return at once)",
        min=0,
        max=UART_AFTER_MAX_S,
    ),
) -> None:
    """Leave fastboot. With no argument this reboots the board back into the
    system — the way out of the state `alb power reboot fastboot` puts it in."""
    _report(_run_job("reboot", {"target": target, "uart_after_s": uart_after}))


@app.command("oem")
def cmd_oem(
    command: str = typer.Argument(
        ..., help="any oem command, e.g. device-info; quote one with spaces"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="skip the confirmation"),
) -> None:
    """`fastboot oem <command>` — any command; the bootloader decides.

    The agent sends it as is and brings the board's answer back word for
    word, a refusal included. It only refuses text that would not arrive
    intact (empty, a leading '-' or whitespace, control characters, over 256
    characters). Whether the change took is read from the device afterwards,
    not from this command's OKAY.
    """
    if not yes:
        typer.echo(f"about to run `fastboot oem {command}` on the board")
        typer.confirm("proceed?", abort=True)
    _report(_run_job("oem", {"command": command}))


@app.command("write")
def cmd_write(
    partition: str = typer.Argument(..., help="target partition name"),
    image: Path = typer.Argument(..., help="image file (path under the alb workspace)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="skip the confirmation"),
) -> None:
    """Write an image to one partition.

    The confirmation is not ceremony: flashing the wrong partition is not
    undoable from here, and the board may not come back to tell you.
    """
    if not yes:
        typer.echo(f"about to write {image} to partition {partition}")
        typer.confirm("proceed?", abort=True)
    _report(_run_job("flash", {"partition": partition, "image": str(image)}))


@app.command("erase")
def cmd_erase(
    partition: str = typer.Argument(..., help="target partition name"),
    yes: bool = typer.Option(False, "--yes", "-y", help="skip the confirmation"),
) -> None:
    """`fastboot erase <partition>` — the same checks as `write`.

    Runs the agent's `erase` op through /api/bench/ops, the path
    `alb bench run erase partition=<p>` already takes, so there is no second
    copy of the checks: the hub holds the name against the chosen board's
    partition list, the agent checks the list again and, when agent.conf gives
    the board a fastboot_product, compares it with the board on the cable.
    Nothing to undo afterwards, hence the confirmation.
    """
    if not yes:
        typer.echo(f"about to erase partition {partition} — this cannot be undone")
        typer.confirm("proceed?", abort=True)
    _report(_run_job("ops/erase", {"params": {"partition": partition}}, base="/api/bench"))


# ── plan: a whole flashing manifest, checked first, run once ───────────


def _row_text(row: dict[str, Any]) -> str:
    action = str(row.get("action") or "")
    if action == "flash":
        return f"flash {row.get('partition')} <- {row.get('file')}"
    if action == "erase":
        return f"erase {row.get('partition')}"
    if action == "setvar":
        return f"setvar {row.get('key')} {row.get('value')}"
    if action == "getvar":
        return f"getvar {row.get('key')}"
    if action == "oem":
        return f"oem {row.get('command')}"
    if action == "wait":
        # The manifest's own pause, e.g. `ping -n 5 127.0.0.1` → 4 s.
        return f"wait {float(row.get('wait_s') or 0):g} s ({row.get('command')})"
    return action


def _print_plan(plan: dict[str, Any]) -> None:
    """Every row with its decision. Size and md5 are there so the plan can
    be held against the vendor tool's own listing before anything is sent."""
    typer.echo(
        f"plan: {plan.get('manifest')} · {plan.get('total', 0)} rows · "
        f"run {plan.get('run', 0)} · skip {plan.get('skipped', 0)}"
    )
    typer.echo(f"  {'line':>4}  {'row':<44}  {'decision':<30}  {'bytes':>12}  md5")
    for row in plan.get("rows") or []:
        decision = f"skip ({row['skip']})" if row.get("skip") else "run"
        size = str(row["size"]) if row.get("size") else ""
        typer.echo(
            f"  {row.get('line', 0):>4}  {_row_text(row):<44}  {decision:<30}  "
            f"{size:>12}  {row.get('md5') or ''}".rstrip()
        )
    typer.echo(f"  fingerprint: {plan.get('fingerprint')}")


def _render_plan_progress(msg: dict[str, Any], state: dict[str, Any]) -> None:
    phase = msg.get("phase", "")
    if phase == "step":
        state.clear()  # a new row's transfer starts from 0% again
        typer.echo(f"[{msg.get('done')}/{msg.get('total')}] {msg.get('text')}")
        return
    if phase == "skip":
        typer.echo(f"  skip {msg.get('text')}")
        return
    _render_progress(msg, state)


def _report_plan(final: dict[str, Any]) -> None:
    rows = final.get("rows") or []
    head = (
        f"{final.get('total', 0)} rows · ran {final.get('ran', 0)} · "
        f"skipped {final.get('skipped', 0)}"
    )
    skipped = [r for r in rows if r.get("status") == "skipped"]
    if final.get("ok"):
        typer.echo(f"[ok] plan finished · {head} · {final.get('duration_s', 0)}s")
        for r in skipped:
            typer.echo(f"  skipped line {r.get('line')}: {_row_text(r)} ({r.get('skip')})")
        _report_artifacts(str(final.get("artifacts") or ""))
        return
    code = str(final.get("code") or "")
    message, advice = _explain(code, str(final.get("error") or ""))
    line = int(final.get("failed_line") or 0)
    failed = next((r for r in rows if r.get("line") == line), None)
    where = f"line {line} {_row_text(failed)}: " if failed else ""
    typer.echo(f"[fail] {where}{code or 'error'}: {message}", err=True)
    if advice:
        typer.echo(f"  try: {advice}", err=True)
    for stream in ("stdout", "stderr"):
        text = str(final.get(stream) or "").strip()
        if text:
            typer.echo(f"  {stream}: {text}", err=True)
    typer.echo(f"  {head} · not run {final.get('not_run', 0)}", err=True)
    for r in skipped:
        typer.echo(f"  skipped line {r.get('line')}: {_row_text(r)} ({r.get('skip')})", err=True)
    not_run = [str(r.get("line")) for r in rows if r.get("status") == "not-run"]
    if not_run:
        typer.echo(f"  not run: line {', '.join(not_run)}", err=True)
    if line:
        # the failed row itself runs again: it may have been cut off mid-write
        typer.echo(
            f"  to resume once fixed: the same command with --from-line {line} "
            "(rows before it are skipped, getvar checks still run)",
            err=True,
        )
    _report_artifacts(str(final.get("artifacts") or ""), err=True)
    raise typer.Exit(code=1)


def _fetch_plan(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=_timeout()) as client:
            resp = client.post(f"{hub_url()}/api/flash/plan", json=payload)
    except httpx.HTTPError as e:
        _hub_unreachable(e)
    if resp.status_code >= 400:
        _print_rejection(resp)
        raise typer.Exit(code=2)
    data: dict[str, Any] = resp.json()
    return data


@app.command("plan")
def cmd_plan(
    manifest: str = typer.Argument(
        ..., help="flashing manifest — a path under the hub's alb workspace; image names "
        "in it are relative to the manifest's directory"
    ),
    skip: list[str] = typer.Option(
        None, "--skip", help="partition(s) to leave untouched: exact names, comma-separated "
        "or repeated; a name that matches no row refuses the plan"
    ),
    only: list[str] = typer.Option(
        None, "--only", help="flash only these partition(s), each with the image the manifest "
        "gives it; rows without a partition (getvar, oem, reboot, waits) still run — drop them "
        "with --skip-action. Exact names, comma-separated or repeated; not with --skip"
    ),
    skip_action: list[str] = typer.Option(
        None, "--skip-action", help="rows without a partition to leave out: setvar, "
        "getvar, oem, reboot-bootloader, reboot, wait (comma-separated or repeated)"
    ),
    from_line: int = typer.Option(
        0, "--from-line", help="resume a plan that stopped: skip the rows before this manifest "
        "line (getvar checks still run); it must be a row of the manifest", min=0
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="print the plan and stop"),
    reboot_wait: float = typer.Option(
        120.0, "--reboot-wait", help="seconds to wait for the board to return to fastboot "
        "after reboot-bootloader (10-600)"
    ),
    uart_after: float = typer.Option(
        UART_AFTER_REBOOT_S,
        "--uart-after",
        help="when the last row reboots the board to the system: keep recording the UART "
        "this many seconds after it, so the record holds the first boot (0 = off)",
        min=0,
        max=UART_AFTER_MAX_S,
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="skip the confirmation"),
) -> None:
    """Flash a whole package by its manifest, leaving out named partitions.

    The plan is checked as a whole first — unknown action, missing image,
    malformed name, a skip that matches nothing, a partition the agent will
    refuse: any one of them and NO row runs. Then it is shown, confirmed
    once, and run in order under one hold of the job lock; the first failing
    row stops it.

    Exit codes: 0 done (or a clean dry run) · 1 the plan has problems or a
    row failed · 2 the hub refused the request or cannot be reached.
    """
    payload: dict[str, Any] = {
        "manifest": manifest,
        "skip": list(skip or []),
        "skip_actions": list(skip_action or []),
    }
    if only:
        payload["only"] = list(only)
    if from_line:
        payload["from_line"] = from_line
    plan = _fetch_plan(payload)
    _print_plan(plan)
    problems = plan.get("problems") or []
    if problems:
        typer.echo(
            f"[fail] FLASH_PLAN_REJECTED: {len(problems)} problem(s), nothing was run", err=True
        )
        for problem in problems:
            typer.echo(f"  - {problem}", err=True)
        raise typer.Exit(code=1)
    if dry_run:
        typer.echo("[dry-run] plan is valid; nothing was run")
        return
    if not yes:
        typer.echo(
            f"about to run {plan.get('run', 0)} row(s) of {plan.get('manifest')} on the board "
            f"({plan.get('skipped', 0)} skipped)"
        )
        typer.confirm("proceed?", abort=True)
    run_payload = {
        **payload,
        "fingerprint": plan.get("fingerprint"),
        "reboot_wait_s": reboot_wait,
        "uart_after_s": uart_after,
    }
    _report_plan(_run_job("plan/run", run_payload, render=_render_plan_progress))


# ── vendor tools registered on the bench host (ADR-068) ──────────────


def _size_text(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MiB"
    if n >= 1024:
        return f"{n / 1024:.1f} KiB"
    return f"{n} B"


def _print_tool_result(final: dict[str, Any], *, err: bool, show_output: bool) -> None:
    """What a tool-run done frame says beyond ok/fail: who judged it, the
    highlight lines, the files it wrote and where the hub kept them."""
    data = final.get("data") or {}
    judged = str(data.get("judged_by") or "")
    if judged:
        typer.echo(f"  judged by: {judged}", err=err)
    if data.get("note"):
        typer.echo(f"  note: {data['note']}", err=err)
    highlights = [str(h) for h in data.get("highlights") or []]
    if highlights:
        typer.echo("  highlights (tool_note):", err=err)
        for line in highlights:
            typer.echo(f"    {line}", err=err)
        if data.get("highlights_dropped"):
            typer.echo(f"    … {data['highlights_dropped']} more not kept", err=err)
    if show_output:
        out = str(final.get("stdout") or "").strip()
        if out:
            typer.echo("  last output lines:", err=err)
            for line in out.splitlines():
                typer.echo(f"    {line}", err=err)
    files = [f for f in data.get("files") or [] if isinstance(f, dict)]
    lost = set(data.get("files_not_kept") or [])
    if files:
        where = data.get("files_dir") or "(not kept: the hub had no record directory)"
        typer.echo(f"  files the tool wrote — kept in {where}:", err=err)
        for f in files:
            name = str(f.get("name") or "")
            size = _size_text(int(f.get("size") or 0))
            if not f.get("sent"):
                typer.echo(
                    f"    {name or '(none)'} ({size}) — not brought back: {f.get('why')}", err=err
                )
            elif name in lost:
                typer.echo(f"    {name} ({size}) — arrived but could not be kept", err=err)
            else:
                typer.echo(f"    {name} ({size})", err=err)
    _report_artifacts(str(final.get("artifacts") or ""), err=err)


@app.command("tools")
def cmd_tools(
    as_json: bool = typer.Option(False, "--json", help="print the raw answer"),
) -> None:
    """List the vendor flashing tools registered on the bench host.

    For each tool: the exe, the directory its packages sit under, where it
    runs, its time limit, how its result is judged, its USB device and
    whether that device is attached right now, and the usage notes written
    in agent.conf. Register a tool there with tool_exe.<name> and
    tool_root.<name> (see clients/windows-agent/agent.conf.example).
    """
    final = _run_job("ops/tool-list", {"params": {}}, base="/api/bench")
    if as_json:
        typer.echo(json.dumps(final, ensure_ascii=False, indent=2))
    if not final.get("ok"):
        if not as_json:
            _report(final)
        raise typer.Exit(code=1)
    if as_json:
        return
    data = final.get("data") or {}
    tools = [t for t in data.get("tools") or [] if isinstance(t, dict)]
    for t in tools:
        where = (
            "the package directory" if t.get("cwd") == "package" else "a fresh scratch directory"
        )
        typer.echo(f"{t.get('name')}  {t.get('exe')}")
        typer.echo(
            f"    packages under {t.get('root')} · runs in {where} · "
            f"time limit {float(t.get('timeout_s') or 0):.0f}s"
        )
        ok = f"exit code 0 and /{t['ok']}/" if t.get("ok") else "exit code 0 only"
        fail = f" · failure: /{t['fail']}/" if t.get("fail") else ""
        note = f" · highlights: /{t['note']}/" if t.get("note") else ""
        typer.echo(f"    success: {ok}{fail}{note}")
        usb = ", ".join(t.get("usb") or [])
        if not usb:
            typer.echo("    USB: not checked (no tool_usb)")
        elif "attached" not in t:
            typer.echo(f"    USB {usb}: unknown — {data.get('usb_error') or 'not listed'}")
        elif t["attached"]:
            typer.echo(f"    USB {usb}: attached — {'; '.join(t['attached'])}")
        else:
            typer.echo(f"    USB {usb}: not attached (is the board in download mode?)")
        usage = str(t.get("usage") or "").strip()
        if usage:
            typer.echo("    usage:")
            for line in usage.splitlines():
                typer.echo(f"      {line}")
    for problem in data.get("problems") or []:
        typer.echo(f"[warn] {problem}")
    if not tools:
        typer.echo("[fail] no vendor tool is usable on the bench host", err=True)
        raise typer.Exit(code=1)


@app.command("tool")
def cmd_tool(
    name: str = typer.Argument(..., help="a registered tool, see `alb flash tools`"),
    tool_args: list[str] = typer.Argument(
        None, help="the tool's own arguments, after `--` so options reach the tool"
    ),
    pkg: str = typer.Option(
        "",
        "--pkg",
        help="package directory, relative to the tool's tool_root on the bench "
        "host; the tool runs in it (unless tool_cwd is job) and {pkg} in an argument is its path",
    ),
    port: str = typer.Option(
        "",
        "--port",
        help="COMnn to use for {com} when several devices of the tool's tool_usb are attached",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="skip the confirmation"),
    as_json: bool = typer.Option(False, "--json", help="print the done frame as JSON"),
) -> None:
    """Run a registered vendor flashing tool with its own arguments.

    Everything after `--` goes to the tool unchanged:

        alb flash tool fb --pkg board-a/v12 -- getvar product
        alb flash tool vt --pkg board-a/v12 -- -p {com} -f update.img

    The bench agent refuses an argument with a drive path (X:\\), a network
    path (\\\\server), a path from the drive root (\\x) or `..`; name files
    relative to the package, or write {pkg}. {com} is the COM port of the
    tool's USB device. Every output line streams here and into the record;
    files the tool writes into its working directory come back to the hub.
    The result says who judged it: tool_fail / exit code / tool_ok from
    agent.conf — with no tool_ok, only the exit code was looked at.

    Exit codes: 0 judged a success · 1 refused or judged a failure · 2 the
    hub refused the request or cannot be reached.
    """
    args = list(tool_args or [])
    if not yes:
        shown = shlex.join(args) if args else "(no arguments)"
        where = f" in package {pkg}" if pkg else ""
        typer.echo(f"about to run {name} {shown}{where} on the bench — it may write the board")
        typer.confirm("proceed?", abort=True)
    params: dict[str, Any] = {"tool": name, "args": args}
    if pkg:
        params["pkg"] = pkg
    if port:
        params["port"] = port
    state: dict[str, Any] = {}

    def render(msg: dict[str, Any], st: dict[str, Any]) -> None:
        state["lines"] = state.get("lines", 0) + 1
        _render_progress(msg, st)

    final = _run_job("ops/tool-run", {"params": params}, render=render, base="/api/bench")
    if as_json:
        typer.echo(json.dumps(final, ensure_ascii=False, indent=2))
        if not final.get("ok"):
            raise typer.Exit(code=1)
        return
    # The tool's lines streamed above; print the tail again only when they
    # did not (a refusal before it ran says why in its own words).
    show_output = not state.get("lines")
    if final.get("ok"):
        typer.echo(f"[ok] {final.get('duration_s', 0)}s")
        _print_tool_result(final, err=False, show_output=show_output)
        return
    code = str(final.get("code") or "")
    message, advice = _explain(code, str(final.get("error") or ""))
    typer.echo(f"[fail] {code or 'error'}: {message}", err=True)
    if advice:
        typer.echo(f"  try: {advice}", err=True)
    _print_tool_result(final, err=True, show_output=show_output)
    raise typer.Exit(code=1)
