"""CLI: app install / uninstall / start / stop / list / info / clear-data."""

from __future__ import annotations

from pathlib import Path

import typer

from alb.capabilities.app import (
    clear_data,
    info,
    install,
    list_apps,
    start,
    stop,
    uninstall,
)
from alb.cli.common import get_transport, print_result, run_async

app = typer.Typer(help="APK management commands.")


@app.command("install")
def cmd_install(
    ctx: typer.Context,
    apk: Path = typer.Argument(...),
    replace: bool = typer.Option(True, "--replace/--no-replace"),
    grant_runtime: bool = typer.Option(False, "--grant-runtime", "-g"),
    downgrade: bool = typer.Option(False, "--downgrade", "-d"),
    device: str | None = typer.Option(None, "--device"),
) -> None:
    """Install an APK."""
    t = get_transport(ctx, device_serial=device)
    result = run_async(
        install(
            t, apk,
            replace=replace,
            grant_runtime=grant_runtime,
            downgrade=downgrade,
        )
    )
    print_result(ctx, result)


@app.command("uninstall")
def cmd_uninstall(
    ctx: typer.Context,
    package: str = typer.Argument(...),
    keep_data: bool = typer.Option(False, "--keep-data"),
    allow_dangerous: bool = typer.Option(False, "--allow-dangerous"),
    device: str | None = typer.Option(None, "--device"),
) -> None:
    """Uninstall a package."""
    t = get_transport(ctx, device_serial=device)
    result = run_async(
        uninstall(
            t, package,
            keep_data=keep_data,
            allow_dangerous=allow_dangerous,
        )
    )
    print_result(ctx, result)


@app.command("start")
def cmd_start(
    ctx: typer.Context,
    component: str = typer.Argument(...),
    device: str | None = typer.Option(None, "--device"),
) -> None:
    """Start an app or activity."""
    t = get_transport(ctx, device_serial=device)
    result = run_async(start(t, component))
    print_result(ctx, result)


@app.command("stop")
def cmd_stop(
    ctx: typer.Context,
    package: str = typer.Argument(...),
    device: str | None = typer.Option(None, "--device"),
) -> None:
    """Force-stop a package."""
    t = get_transport(ctx, device_serial=device)
    result = run_async(stop(t, package))
    print_result(ctx, result)


@app.command("list")
def cmd_list(
    ctx: typer.Context,
    filter: str | None = typer.Option(None, "--filter"),  # noqa: A002
    system: bool = typer.Option(False, "--system", help="Include system apps"),
    device: str | None = typer.Option(None, "--device"),
) -> None:
    """List installed packages."""
    t = get_transport(ctx, device_serial=device)
    result = run_async(
        list_apps(t, filter=filter, include_system=system)
    )
    print_result(ctx, result)


@app.command("info")
def cmd_info(
    ctx: typer.Context,
    package: str = typer.Argument(...),
    device: str | None = typer.Option(None, "--device"),
) -> None:
    """Show version / install time / permissions for a package."""
    t = get_transport(ctx, device_serial=device)
    result = run_async(info(t, package))
    print_result(ctx, result)


@app.command("clear-data")
def cmd_clear_data(
    ctx: typer.Context,
    package: str = typer.Argument(...),
    allow_dangerous: bool = typer.Option(False, "--allow-dangerous"),
    device: str | None = typer.Option(None, "--device"),
) -> None:
    """Clear app data (destructive)."""
    t = get_transport(ctx, device_serial=device)
    result = run_async(
        clear_data(t, package, allow_dangerous=allow_dangerous)
    )
    print_result(ctx, result)
