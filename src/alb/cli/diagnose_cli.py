"""CLI: bugreport / anr / tombstone / devinfo."""

from __future__ import annotations

import typer

from alb.capabilities.diagnose import anr_pull, bugreport, devinfo, tombstone_pull
from alb.cli.common import get_transport, print_result, run_async

app = typer.Typer(help="Diagnostic data collection.")


@app.command("bugreport")
def cmd_bugreport(
    ctx: typer.Context, device: str | None = typer.Option(None, "--device")
) -> None:
    """Run adb bugreport and pull the zip."""
    t = get_transport(ctx, device_serial=device)
    result = run_async(bugreport(t, device=device))
    print_result(ctx, result)


@app.command("devinfo")
def cmd_devinfo(
    ctx: typer.Context, device: str | None = typer.Option(None, "--device")
) -> None:
    """Composite device info."""
    t = get_transport(ctx, device_serial=device)
    result = run_async(devinfo(t))
    print_result(ctx, result)


anr_app = typer.Typer(help="ANR manipulation.")
app.add_typer(anr_app, name="anr")


@anr_app.command("pull")
def cmd_anr_pull(
    ctx: typer.Context,
    clear_after: bool = typer.Option(False, "--clear-after"),
    device: str | None = typer.Option(None, "--device"),
) -> None:
    """Pull ANR files from /data/anr/."""
    t = get_transport(ctx, device_serial=device)
    result = run_async(anr_pull(t, clear_after=clear_after, device=device))
    print_result(ctx, result)


tombstone_app = typer.Typer(help="Native crash tombstones.")
app.add_typer(tombstone_app, name="tombstone")


@tombstone_app.command("pull")
def cmd_tombstone_pull(
    ctx: typer.Context,
    limit: int = typer.Option(10, "--limit"),
    device: str | None = typer.Option(None, "--device"),
) -> None:
    """Pull tombstone files from /data/tombstones/."""
    t = get_transport(ctx, device_serial=device)
    result = run_async(tombstone_pull(t, limit=limit, device=device))
    print_result(ctx, result)
