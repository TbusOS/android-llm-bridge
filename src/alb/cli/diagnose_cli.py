"""CLI: bugreport / anr / tombstone / devinfo / pstore."""

from __future__ import annotations

from typing import Any

import typer
from rich.markup import escape

from alb.capabilities.diagnose import anr_pull, bugreport, devinfo, pstore_pull, tombstone_pull
from alb.cli.common import console, get_transport, print_result, run_async
from alb.infra.result import Result

app = typer.Typer(help="Diagnostic data collection.")


@app.command("pstore")
def cmd_pstore(
    ctx: typer.Context,
    device: str | None = typer.Option(None, "--device"),
) -> None:
    """Previous boot's crash record: /sys/fs/pstore/* (ramoops), else /proc/last_kmsg.

    Needs root (alb adb root). Files are pulled into workspace/.../pstore/<ts>/
    and never deleted from the device.
    """
    t = get_transport(ctx, device_serial=device)
    result = run_async(pstore_pull(t, device=device))
    _print_pstore(ctx, result)


def _print_pstore(ctx: typer.Context, result: Result[Any]) -> None:
    """One line per record: name · size · first crash marker.

    The generic printer would render `files` as a Python list of dicts;
    the reader wants to see at a glance which file names the panic.
    """
    if (ctx.obj or {}).get("json") or not result.ok or result.data is None:
        print_result(ctx, result)
        return
    d = result.data
    present = (
        f"pstore: {'present' if d.pstore_present else 'absent'}, "
        f"last_kmsg: {'present' if d.last_kmsg_present else 'absent'}"
    )
    console.print(f"[bold]source:[/] {escape(d.source)}  ({present})")
    for f in d.files:
        if not f.local_path:
            detail = f"[red]pull failed: {escape(f.error)}[/]"
        elif f.first_panic_line:
            detail = f"[red]{escape(f.first_panic_line)}[/]"
        else:
            detail = "[dim](no panic marker)[/]"
        console.print(f"  • {escape(f.name)}  {f.size} B  {detail}", soft_wrap=True)
    console.print(escape(d.note), soft_wrap=True)
    if d.saved_dir:
        console.print(f"[dim]saved:[/] {escape(d.saved_dir)}", soft_wrap=True)


@app.command("bugreport")
def cmd_bugreport(
    ctx: typer.Context,
    device: str | None = typer.Option(None, "--device"),
    output: str | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Local zip path (default: workspace/.../bugreports/<ts>-bugreport.zip; "
        "trailing '/' or existing dir → that dir + auto name).",
    ),
) -> None:
    """Run adb bugreport and pull the zip (default: workspace/, or --output)."""
    t = get_transport(ctx, device_serial=device)
    result = run_async(bugreport(t, device=device, output=output))
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
