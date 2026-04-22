"""CLI: serial capture / send / shell (method G)."""

from __future__ import annotations

from pathlib import Path

import typer

from alb.capabilities.logging import capture_uart
from alb.capabilities.shell import execute as shell_execute
from alb.cli.common import get_transport, print_result, run_async
from alb.transport.serial import SerialTransport

app = typer.Typer(help="UART / serial commands (method G).")


def _force_serial(ctx: typer.Context, device: str | None) -> SerialTransport:
    """Ensure the active transport is a SerialTransport regardless of profile."""
    t = get_transport(ctx, override="serial", device_serial=device)
    assert isinstance(t, SerialTransport), "expected SerialTransport"
    return t


@app.command("capture")
def cmd_capture(
    ctx: typer.Context,
    duration: int = typer.Option(30, "--duration", "-d"),
    device: str | None = typer.Option(None, "--device"),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help=(
            "Output path: either a directory (log goes inside as "
            "<ts>-uart.log) or a full file path. Default: workspace/logs/."
        ),
    ),
) -> None:
    """Capture UART bytes for N seconds (default: workspace/logs/, or --output to override)."""
    t = _force_serial(ctx, device)
    result = run_async(capture_uart(t, duration=duration, device=device, output=output))
    print_result(ctx, result)


@app.command("send")
def cmd_send(
    ctx: typer.Context,
    text: str = typer.Argument(..., help="Text to send (newline appended)."),
    no_newline: bool = typer.Option(False, "--no-newline", help="Skip the trailing newline."),
    device: str | None = typer.Option(None, "--device"),
) -> None:
    """Send a raw string over UART (no prompt waiting)."""
    t = _force_serial(ctx, device)
    payload = text if no_newline else text + "\n"
    result = run_async(t.send_raw(payload.encode("utf-8")))
    # Reuse print_result by wrapping in an ok/fail Result-like object via the
    # shell path — simpler: print raw ShellResult.
    if result.ok:
        typer.echo(f"[ok] wrote {len(payload)} bytes to serial")
    else:
        typer.echo(f"[fail] {result.error_code}: {result.stderr}", err=True)
        raise typer.Exit(code=1)


@app.command("shell")
def cmd_shell(
    ctx: typer.Context,
    cmd: str = typer.Argument(..., help="Command to run over serial shell."),
    timeout: int = typer.Option(30, "--timeout"),
    device: str | None = typer.Option(None, "--device"),
) -> None:
    """Execute a shell command via UART (prompt-based, best-effort)."""
    t = _force_serial(ctx, device)
    result = run_async(shell_execute(t, cmd, timeout=timeout))
    print_result(ctx, result)


@app.command("health")
def cmd_health(
    ctx: typer.Context,
    device: str | None = typer.Option(None, "--device"),
) -> None:
    """Check serial connectivity + endpoint info."""
    t = _force_serial(ctx, device)
    import json

    info = run_async(t.health())
    if (ctx.obj or {}).get("json"):
        typer.echo(json.dumps(info, indent=2, default=str))
        return
    from rich.table import Table

    from alb.cli.common import console

    table = Table(title="serial transport health")
    table.add_column("key")
    table.add_column("value")
    for k, v in info.items():
        table.add_row(k, str(v))
    console.print(table)


@app.command("status")
def cmd_status(
    ctx: typer.Context,
    device: str | None = typer.Option(None, "--device"),
    tail: int = typer.Option(
        400,
        "--tail",
        help="How many bytes of recent output to display.",
    ),
) -> None:
    """Detect what's currently at the UART endpoint.

    Runs handshake, classifies the state (shell / u-boot / kernel boot /
    panic / …) and prints a one-page report. Doesn't send any command.

    Typical use:

        alb serial status                # what is this board doing?
        alb --json serial status         # machine-readable for scripts

    States that matter most:

    - ``shell_root`` / ``shell_user``: shell is ready, run `alb serial shell`
    - ``uboot``: u-boot prompt; use `alb serial shell` for u-boot commands
    - ``kernel_boot``: board is still booting, wait and retry
    - ``panic``: board crashed — only tail is readable
    - ``corrupted``: likely wrong baud; try `alb setup serial --baud N`
    - ``idle``: no output; check power / cable / bridge
    """
    import json

    t = _force_serial(ctx, device)
    info = run_async(t.detect_state())

    # Trim tail for display if requested smaller than full
    if isinstance(info.get("tail"), str) and len(info["tail"]) > tail:
        info["tail"] = "…" + info["tail"][-tail:]

    if (ctx.obj or {}).get("json"):
        typer.echo(json.dumps(info, indent=2, default=str))
        return

    from rich.console import Group
    from rich.panel import Panel
    from rich.table import Table

    from alb.cli.common import console

    # Colour-code the state pill based on severity.
    state = info.get("state", "error")
    colour = {
        "shell_root": "green",  "shell_user": "green",
        "uboot": "cyan",        "recovery": "cyan",        "fastboot": "cyan",
        "kernel_boot": "yellow","linux_init": "yellow",    "spl": "yellow",
        "login_prompt": "yellow",
        "crash": "dark_orange",
        "panic": "red",         "corrupted": "red",
        "idle": "bright_black", "unknown": "bright_black",
    }.get(state, "white")

    summary = Table.grid(padding=(0, 1))
    summary.add_column(style="bold")
    summary.add_column()
    summary.add_row("state",    f"[{colour}]{state}[/{colour}]")
    summary.add_row("endpoint", str(info.get("endpoint", "")))
    summary.add_row("baud",     str(info.get("baud", "")))
    summary.add_row("duration", f"{info.get('duration_ms', '?')} ms")
    if not info.get("ok"):
        summary.add_row("error", str(info.get("error", "")))
        summary.add_row("code",  str(info.get("error_code", "")))

    body: list = [summary]
    if info.get("history"):
        history = Table(show_header=True, header_style="bold")
        history.add_column("from")
        history.add_column("→")
        history.add_column("to")
        history.add_column("wall time")
        for t_ in info["history"]:
            history.add_row(t_["from"], "→", t_["to"], t_.get("wall_time", ""))
        body.append(history)
    if info.get("tail"):
        body.append(Panel(info["tail"], title="tail", border_style="bright_black"))

    console.print(Panel(Group(*body), title="serial status", border_style=colour))
