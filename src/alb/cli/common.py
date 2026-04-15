"""Shared helpers for CLI subcommands."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, is_dataclass
from typing import Any

import typer
from rich.console import Console

from alb.infra.config import ActiveSettings, load_active
from alb.infra.result import Result
from alb.transport.adb import AdbTransport
from alb.transport.base import Transport

console = Console()


def run_async(coro: Any) -> Any:
    """Run an async callable from a sync typer handler."""
    try:
        return asyncio.run(coro)
    except KeyboardInterrupt:
        console.print("\n[red]Interrupted[/]")
        raise typer.Exit(code=130) from None


def get_transport(
    ctx: typer.Context,
    *,
    settings: ActiveSettings | None = None,
    override: str | None = None,
    device_serial: str | None = None,
) -> Transport:
    """Resolve the active transport.

    Priority:
        explicit `override` arg > CLI --transport > profile.primary_transport
    """
    settings = settings or load_active(
        getattr(ctx.obj, "profile", None) if ctx.obj else None
    )
    which = (
        override
        or (ctx.obj or {}).get("transport")
        or settings.primary_transport
    )

    if which == "adb":
        return AdbTransport(
            serial=device_serial,
            bin_path=settings.config.adb.bin_path,
            server_socket=settings.config.adb.server_socket,
        )
    if which == "ssh":
        raise typer.BadParameter("ssh transport not yet implemented (M1 WIP)")
    if which == "serial":
        raise typer.BadParameter("serial transport not yet implemented (M1 WIP)")

    raise typer.BadParameter(f"Unknown transport: {which}")


def print_result(ctx: typer.Context, result: Result[Any]) -> None:
    """Render a Result. Honours global --json flag."""
    json_mode = bool((ctx.obj or {}).get("json"))

    if json_mode:
        print(json.dumps(result.to_dict(), indent=2, default=_json_default))
        if not result.ok:
            raise typer.Exit(code=1)
        return

    if result.ok:
        if result.data is not None:
            _print_data_pretty(result.data)
        if result.artifacts:
            console.print("[dim]artifacts:[/]")
            for a in result.artifacts:
                console.print(f"  • {a}")
        return

    if result.error:
        console.print(f"[red]✗ {result.error.code}[/] — {result.error.message}")
        if result.error.suggestion:
            console.print(f"[yellow]suggestion:[/] {result.error.suggestion}")
        raise typer.Exit(code=1)


def _print_data_pretty(data: Any) -> None:
    if is_dataclass(data):
        for k, v in asdict(data).items():
            console.print(f"  [bold]{k}[/]: {v}")
    elif isinstance(data, dict):
        for k, v in data.items():
            console.print(f"  [bold]{k}[/]: {v}")
    elif isinstance(data, list):
        for item in data:
            console.print(f"  • {item}")
    elif hasattr(data, "to_dict"):
        for k, v in data.to_dict().items():
            console.print(f"  [bold]{k}[/]: {v}")
    else:
        console.print(str(data))


def _json_default(o: Any) -> Any:
    if hasattr(o, "to_dict"):
        return o.to_dict()
    if is_dataclass(o):
        return asdict(o)
    return str(o)
