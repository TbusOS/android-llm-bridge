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
from rich.console import Console

app = typer.Typer(help="Flash / fastboot via the connected device agent.")
_console = Console()

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
    _console.print(f"[red]cannot reach alb-api at {hub_url()}[/red]: {e}")
    _console.print("[dim]fastboot runs on the hub — start alb-api, or set ALB_API_URL[/dim]")
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
        _console.print(f"  [dim]{phase}[/dim] {text}")
        return
    if total > 0:
        pct = done * 100 // total
        # Only speak at 10% steps: a 2 GB image at 64 KiB chunks would
        # otherwise emit 32k lines.
        step = pct // 10
        if state.get("step") != step:
            state["step"] = step
            _console.print(f"  [dim]{phase}[/dim] {done}/{total} bytes ({pct}%)")


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
                _console.print(f"[red]hub rejected the request ({resp.status_code})[/red]")
                detail = ""
                try:
                    detail = str(resp.json().get("detail", ""))
                except Exception:
                    # The body need not be JSON — an error page or a
                    # proxy's text is still worth showing verbatim.
                    detail = resp.text[:400]
                if detail:
                    _console.print(f"  {detail}")
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
        _console.print("[red]the hub closed the stream without reporting a result[/red]")
        _console.print("[dim]the device state is unknown — check the hub log before retrying[/dim]")
        raise typer.Exit(code=3)
    return final


def _report(final: dict[str, Any]) -> None:
    if final.get("ok"):
        out = str(final.get("stdout") or "").strip()
        _console.print(f"[green][ok][/green] {final.get('duration_s', 0)}s")
        if out:
            _console.print(out)
        return
    code = str(final.get("code") or "")
    _console.print(f"[red][fail][/red] {code or 'error'}: {final.get('error') or 'unknown'}")
    for stream in ("stdout", "stderr"):
        text = str(final.get(stream) or "").strip()
        if text:
            _console.print(f"[dim]{stream}:[/dim] {text}")
    raise typer.Exit(code=1)


@app.command("status")
def cmd_status() -> None:
    """Is a fastboot-capable agent connected, and is it busy?"""
    try:
        with httpx.Client(timeout=httpx.Timeout(10.0)) as client:
            data = client.get(f"{hub_url()}/api/flash/status").json()
    except httpx.HTTPError as e:
        _hub_unreachable(e)
    if not data.get("available"):
        _console.print("[yellow]fastboot unavailable[/yellow]")
        _console.print(
            "[dim]no connected agent advertises the fastboot capability — "
            "set fastboot_path in the agent's agent.conf and restart it[/dim]"
        )
        raise typer.Exit(code=1)
    if data.get("busy"):
        _console.print(f"[yellow]busy[/yellow]: {data.get('job') or 'a job is running'}")
        return
    _console.print("[green]ready[/green]")


@app.command("devices")
def cmd_devices() -> None:
    """`fastboot devices` on the agent host — the only way to see a board
    that has dropped off adb by entering fastboot."""
    _report(_run_job("devices"))


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
        _console.print(f"about to write [bold]{image}[/bold] to partition [bold]{partition}[/bold]")
        typer.confirm("proceed?", abort=True)
    _report(_run_job("flash", {"partition": partition, "image": str(image)}))
