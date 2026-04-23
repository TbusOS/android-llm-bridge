"""CLI: alb info {system,cpu,memory,storage,network,battery,all}."""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

from alb.capabilities.info import (
    all_info,
    battery,
    cpu,
    display,
    gpu,
    memory,
    network,
    packages,
    panel_names,
    processes,
    security,
    storage,
    system,
)
from alb.cli.common import get_transport, print_result, run_async

app = typer.Typer(
    help="Structured device info (software + hardware snapshot, read-only)."
)
console = Console()


@app.command("system")
def cmd_system(
    ctx: typer.Context, device: str | None = typer.Option(None, "--device", "-d")
) -> None:
    """Android version / build / kernel / bootloader / SELinux."""
    t = get_transport(ctx, device_serial=device)
    result = run_async(system(t, device=device))
    print_result(ctx, result)


@app.command("cpu")
def cmd_cpu(
    ctx: typer.Context, device: str | None = typer.Option(None, "--device", "-d")
) -> None:
    """CPU model / cores / frequencies / thermal zones."""
    t = get_transport(ctx, device_serial=device)
    result = run_async(cpu(t, device=device))
    print_result(ctx, result)


@app.command("memory")
def cmd_memory(
    ctx: typer.Context, device: str | None = typer.Option(None, "--device", "-d")
) -> None:
    """RAM / swap / zram usage."""
    t = get_transport(ctx, device_serial=device)
    result = run_async(memory(t, device=device))
    print_result(ctx, result)


@app.command("storage")
def cmd_storage(
    ctx: typer.Context, device: str | None = typer.Option(None, "--device", "-d")
) -> None:
    """Partitions / filesystems / UFS·eMMC type."""
    t = get_transport(ctx, device_serial=device)
    result = run_async(storage(t, device=device))
    print_result(ctx, result)


@app.command("network")
def cmd_network(
    ctx: typer.Context, device: str | None = typer.Option(None, "--device", "-d")
) -> None:
    """Interfaces / IPs / MAC / default route / DNS."""
    t = get_transport(ctx, device_serial=device)
    result = run_async(network(t, device=device))
    print_result(ctx, result)


@app.command("battery")
def cmd_battery(
    ctx: typer.Context, device: str | None = typer.Option(None, "--device", "-d")
) -> None:
    """Battery level / status / voltage / temp / health."""
    t = get_transport(ctx, device_serial=device)
    result = run_async(battery(t, device=device))
    print_result(ctx, result)


@app.command("gpu")
def cmd_gpu(
    ctx: typer.Context, device: str | None = typer.Option(None, "--device", "-d")
) -> None:
    """GPU model / frequency / governor / utilization."""
    t = get_transport(ctx, device_serial=device)
    result = run_async(gpu(t, device=device))
    print_result(ctx, result)


@app.command("security")
def cmd_security(
    ctx: typer.Context, device: str | None = typer.Option(None, "--device", "-d")
) -> None:
    """Verified Boot / dm-verity / FBE / SELinux / OEM unlock."""
    t = get_transport(ctx, device_serial=device)
    result = run_async(security(t, device=device))
    print_result(ctx, result)


@app.command("display")
def cmd_display(
    ctx: typer.Context, device: str | None = typer.Option(None, "--device", "-d")
) -> None:
    """Resolution / density / refresh rate / brightness."""
    t = get_transport(ctx, device_serial=device)
    result = run_async(display(t, device=device))
    print_result(ctx, result)


@app.command("packages")
def cmd_packages(
    ctx: typer.Context,
    device: str | None = typer.Option(None, "--device", "-d"),
    sample: int = typer.Option(10, "--sample", help="Sample size per list."),
) -> None:
    """Installed packages: system / user / disabled counts + samples."""
    t = get_transport(ctx, device_serial=device)
    result = run_async(packages(t, device=device, sample_limit=sample))
    print_result(ctx, result)


@app.command("processes")
def cmd_processes(
    ctx: typer.Context,
    device: str | None = typer.Option(None, "--device", "-d"),
    limit: int = typer.Option(15, "--limit", help="Top-N by CPU and by RSS."),
) -> None:
    """Top processes by CPU and by memory (RSS)."""
    t = get_transport(ctx, device_serial=device)
    result = run_async(processes(t, device=device, limit=limit))
    print_result(ctx, result)


@app.command("all")
def cmd_all(
    ctx: typer.Context,
    device: str | None = typer.Option(None, "--device", "-d"),
    panels: str = typer.Option(
        "",
        "--panels",
        "-p",
        help=f"Comma-separated panel names. Default: all. Choices: {','.join(panel_names())}",
    ),
) -> None:
    """Run multiple panels in parallel; print a one-line summary + JSON."""
    t = get_transport(ctx, device_serial=device)
    names = [s.strip() for s in panels.split(",") if s.strip()] or None
    results = run_async(all_info(t, device=device, panels=names))

    if (ctx.obj or {}).get("json"):
        as_dict = {k: v.to_dict() for k, v in results.items()}
        print(json.dumps(as_dict, indent=2))
        return

    table = Table(title="alb info · summary")
    table.add_column("panel")
    table.add_column("ok", justify="center")
    table.add_column("timing_ms", justify="right")
    table.add_column("note")
    for name, r in results.items():
        mark = "[green]✓[/]" if r.ok else "[red]✗[/]"
        note = "" if r.ok else (r.error.message if r.error else "")
        table.add_row(name, mark, str(r.timing_ms), note[:60])
    console.print(table)
