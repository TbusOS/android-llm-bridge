"""CLI: `alb bench` — ops on the bench host, from the agent's own table (ADR-059).

    alb bench ops                               what this agent can do
    alb bench run <op> [name=value ...] [--image <workspace path>] [--yes]
    alb bench usb                               USB devices on the bench host, with the
                                                mode each board is in
    alb bench log <source> [-n LINES] [-g TEXT] tail a log on the bench host

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
            typer.echo(
                f"  {b.get('mode'):<20} {b.get('vid')}:{b.get('pid')}  "
                f"serial {b.get('serial') or '-'}  driver: {b.get('driver')}  {b.get('name')}"
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
