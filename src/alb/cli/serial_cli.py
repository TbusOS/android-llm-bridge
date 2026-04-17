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
