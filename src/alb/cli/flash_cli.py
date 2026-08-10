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
    alb flash write <partition> <image>

`write`, not `flash flash`: the verb reads as what it does to the device.
"""

from __future__ import annotations

import json
import os
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


def _run_job(path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """POST and consume the NDJSON stream; return the terminal `done` frame."""
    url = f"{hub_url()}/api/flash/{path}"
    state: dict[str, Any] = {}
    final: dict[str, Any] = {}
    try:
        with (
            httpx.Client(timeout=_timeout()) as client,
            client.stream("POST", url, json=payload or {}) as resp,
        ):
            if resp.status_code >= 400:
                resp.read()
                typer.echo(f"[fail] hub rejected the request ({resp.status_code})", err=True)
                detail = ""
                try:
                    detail = str(resp.json().get("detail", ""))
                except Exception:
                    # The body need not be JSON — an error page or a
                    # proxy's text is still worth showing verbatim.
                    detail = resp.text[:400]
                if detail:
                    typer.echo(f"  {detail}", err=True)
                raise typer.Exit(code=2)
            for line in resp.iter_lines():
                if not line.strip():
                    continue
                msg = json.loads(line)
                if msg.get("ev") == "progress":
                    _render_progress(msg, state)
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
    """Is a fastboot-capable agent connected, and is it busy?"""
    try:
        with httpx.Client(timeout=httpx.Timeout(10.0)) as client:
            data = client.get(f"{hub_url()}/api/flash/status").json()
    except httpx.HTTPError as e:
        _hub_unreachable(e)
    if not data.get("available"):
        typer.echo("[fail] fastboot unavailable")
        typer.echo(
            "        no connected agent advertises the fastboot capability — "
            "set fastboot_path in the agent's agent.conf and restart it"
        )
        raise typer.Exit(code=1)
    if data.get("busy"):
        typer.echo(f"[busy] {data.get('job') or 'a job is running'}")
        return
    typer.echo("[ok] ready")


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
) -> None:
    """Leave fastboot. With no argument this reboots the board back into the
    system — the way out of the state `alb power reboot fastboot` puts it in."""
    _report(_run_job("reboot", {"target": target}))


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
