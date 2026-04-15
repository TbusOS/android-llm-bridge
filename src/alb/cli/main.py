"""Main CLI entry point (`alb` command).

M0 skeleton — prints an informative help + `alb describe` works.
Real subcommands land in M1.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from alb import __version__
from alb.infra.registry import CAPABILITIES, TRANSPORTS

app = typer.Typer(
    name="alb",
    help="android-llm-bridge — Unified Android debugging bridge for LLM agents.",
    no_args_is_help=True,
    add_completion=False,
)

console = Console()


@app.callback()
def _main_options(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="Structured JSON output."),
    profile: str | None = typer.Option(
        None, "--profile", help="Profile name to activate."
    ),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Verbose output."),
) -> None:
    ctx.obj = {"json": json_output, "profile": profile, "verbose": verbose}


@app.command()
def version() -> None:
    """Show version."""
    console.print(f"[bold]android-llm-bridge[/] (alb) v{__version__}")


@app.command()
def describe(ctx: typer.Context) -> None:
    """Output the full tool/capability/transport schema. LLM-oriented."""
    schema = {
        "version": __version__,
        "transports": [asdict(t) for t in TRANSPORTS],
        "capabilities": [asdict(c) for c in CAPABILITIES],
    }
    if ctx.obj and ctx.obj.get("json"):
        print(json.dumps(schema, indent=2))
        return

    # Human-friendly table
    table = Table(title="android-llm-bridge · transports")
    table.add_column("name")
    table.add_column("methods")
    table.add_column("status")
    table.add_column("description")
    for t in TRANSPORTS:
        table.add_row(
            t.name,
            ",".join(t.methods_supported) or "—",
            t.status,
            t.description or "",
        )
    console.print(table)

    table = Table(title="android-llm-bridge · capabilities")
    table.add_column("name")
    table.add_column("cli")
    table.add_column("status")
    table.add_column("description")
    for c in CAPABILITIES:
        table.add_row(c.name, c.cli_command, c.status, c.description or "")
    console.print(table)


@app.command()
def status() -> None:
    """Current device / transport / active-task snapshot. (M1)"""
    console.print(
        Panel.fit(
            "[yellow]M0 skeleton — status not implemented yet.[/]\n"
            "This command will return the active transport, connected devices,\n"
            "background tasks, and recent errors once M1 ships.",
            title="alb status",
            border_style="yellow",
        )
    )


@app.command()
def setup(method: str = typer.Argument(..., help="adb | wifi | ssh | serial")) -> None:
    """Interactive setup for a transport method. (M1)"""
    console.print(
        Panel.fit(
            f"[yellow]M0 skeleton — setup {method!r} not implemented yet.[/]\n"
            f"See docs/methods/ for manual setup per method.",
            title=f"alb setup {method}",
            border_style="yellow",
        )
    )


@app.command()
def devices() -> None:
    """List connected devices. (M1)"""
    console.print(Panel.fit("[yellow]M0 skeleton.[/]", title="alb devices"))


def main() -> None:
    """Entry point referenced by pyproject.toml `[project.scripts]`."""
    try:
        app()
    except KeyboardInterrupt:
        console.print("\n[red]Interrupted[/]")
        sys.exit(130)


if __name__ == "__main__":
    main()
