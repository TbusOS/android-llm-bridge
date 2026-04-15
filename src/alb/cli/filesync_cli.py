"""CLI: push / pull."""

from __future__ import annotations

from pathlib import Path

import typer

from alb.capabilities.filesync import pull as cap_pull
from alb.capabilities.filesync import push as cap_push
from alb.cli.common import get_transport, print_result, run_async

app = typer.Typer(help="File transfer commands.")


@app.command("push")
def cmd_push(
    ctx: typer.Context,
    local: Path = typer.Argument(...),
    remote: str = typer.Argument(...),
    verify: bool = typer.Option(False, "--verify"),
    device: str | None = typer.Option(None, "--device"),
) -> None:
    """Push a local file to the device."""
    t = get_transport(ctx, device_serial=device)
    result = run_async(cap_push(t, local, remote, verify=verify))
    print_result(ctx, result)


@app.command("pull")
def cmd_pull(
    ctx: typer.Context,
    remote: str = typer.Argument(...),
    local: Path | None = typer.Argument(None),
    device: str | None = typer.Option(None, "--device"),
) -> None:
    """Pull from device to local (defaults to workspace/.../pulls/)."""
    t = get_transport(ctx, device_serial=device)
    result = run_async(cap_pull(t, remote, local, device=device))
    print_result(ctx, result)
