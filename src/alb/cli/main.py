"""Main CLI entry point (`alb` command).

M1 WIP — commands that hit a real transport require `alb setup adb` first.
See docs/llm-integration.md §五 for full CLI conventions.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from alb import __version__
from alb.capabilities.logging import collect_dmesg, collect_logcat, search_logs, tail_log
from alb.capabilities.shell import execute as shell_execute
from alb.infra.env_loader import load_env_files

# Load .env.local / .env at CLI startup so subcommands see the values
# (typer's envvar= and os.environ.get both pick them up).
load_env_files()
from alb.cli.app_cli import app as app_cli
from alb.cli.chat_cli import app as chat_cli
from alb.cli.common import get_transport, print_result, run_async
from alb.cli.diagnose_cli import app as diagnose_cli
from alb.cli.filesync_cli import app as filesync_cli
from alb.cli.power_cli import app as power_cli
from alb.cli.serial_cli import app as serial_cli
from alb.cli.setup_cli import app as setup_cli
from alb.cli.skills_cli import app as skills_cli
from alb.cli.info_cli import app as info_cli
from alb.cli.ui_cli import app as ui_cli
from alb.infra.config import load_active
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
    profile: str | None = typer.Option(None, "--profile", help="Profile name to activate."),
    transport: str | None = typer.Option(
        None, "--transport", help="Override transport: adb|ssh|serial."
    ),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Verbose output."),
) -> None:
    ctx.obj = {
        "json": json_output,
        "profile": profile,
        "transport": transport,
        "verbose": verbose,
    }


# ─── Meta commands ─────────────────────────────────────────────────
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
def status(ctx: typer.Context) -> None:
    """Current device / transport / recent state."""
    settings = load_active((ctx.obj or {}).get("profile"))

    if (ctx.obj or {}).get("transport") is None and settings.primary_transport != "adb":
        console.print(
            Panel.fit(
                f"Primary transport: [bold]{settings.primary_transport}[/]\n"
                "Only adb transport is implemented in this build.",
                title="alb status",
                border_style="yellow",
            )
        )

    transport = get_transport(ctx)
    health = run_async(transport.health())
    if (ctx.obj or {}).get("json"):
        print(json.dumps(health, indent=2, default=str))
        return

    table = Table(title="alb status")
    table.add_column("key")
    table.add_column("value")
    for k, v in health.items():
        table.add_row(k, str(v))
    console.print(table)


# `alb setup {adb,wifi,ssh,serial}` — guided setup (see setup_cli.py)
app.add_typer(setup_cli, name="setup", help="Guided setup for each transport.")


# ─── Device / transport commands ───────────────────────────────────
@app.command()
def devices(ctx: typer.Context) -> None:
    """List connected devices."""
    transport = get_transport(ctx)
    if not hasattr(transport, "devices"):
        console.print("[yellow]Current transport does not expose a device list.[/]")
        return
    devs = run_async(transport.devices())

    if (ctx.obj or {}).get("json"):
        print(json.dumps([asdict(d) for d in devs], indent=2))
        return

    if not devs:
        console.print("[yellow]No devices found.[/]")
        console.print("Run [bold]alb status[/] to diagnose.")
        return

    table = Table(title="connected devices")
    table.add_column("serial")
    table.add_column("state")
    table.add_column("model")
    table.add_column("product")
    for d in devs:
        table.add_row(d.serial, d.state, d.model, d.product)
    console.print(table)


@app.command()
def shell(
    ctx: typer.Context,
    cmd: str = typer.Argument(..., help="Shell command to run on the device."),
    timeout: int = typer.Option(30, "--timeout", "-t"),
    device: str | None = typer.Option(None, "--device", "-d"),
    allow_dangerous: bool = typer.Option(False, "--allow-dangerous"),
) -> None:
    """Execute a shell command on the active device."""
    transport = get_transport(ctx, device_serial=device)
    result = run_async(
        shell_execute(
            transport,
            cmd,
            timeout=timeout,
            allow_dangerous=allow_dangerous,
        )
    )
    print_result(ctx, result)


@app.command()
def logcat(
    ctx: typer.Context,
    duration: int = typer.Option(60, "--duration", "-d"),
    filter: str | None = typer.Option(None, "--filter", "-f"),  # noqa: A002
    tag: list[str] = typer.Option(None, "--tag", help="Tag filter (repeatable)."),
    clear: bool = typer.Option(False, "--clear", help="logcat -c before collecting."),
    device: str | None = typer.Option(None, "--device"),
) -> None:
    """Collect logcat to workspace for `duration` seconds."""
    transport = get_transport(ctx, device_serial=device)
    result = run_async(
        collect_logcat(
            transport,
            duration=duration,
            filter=filter,
            tags=tag,
            clear_before=clear,
            device=device,
        )
    )
    print_result(ctx, result)


@app.command()
def dmesg(
    ctx: typer.Context,
    duration: int = typer.Option(10, "--duration", "-d"),
    device: str | None = typer.Option(None, "--device"),
) -> None:
    """Collect kernel dmesg."""
    transport = get_transport(ctx, device_serial=device)
    result = run_async(collect_dmesg(transport, duration=duration, device=device))
    print_result(ctx, result)


# ─── Subcommand groups ─────────────────────────────────────────────
# Use explicit group names to keep the CLI discoverable:
#   alb fs push / pull
#   alb diag bugreport / devinfo / anr pull / tombstone pull
#   alb power reboot / battery / wait-boot / sleep-wake
#   alb app install / uninstall / start / stop / list / info / clear-data
app.add_typer(filesync_cli, name="fs", help="File transfer commands (push/pull).")
app.add_typer(diagnose_cli, name="diag", help="Diagnostic data (bugreport/anr/...)")
app.add_typer(power_cli, name="power", help="Power state (reboot/battery/wait-boot).")
app.add_typer(app_cli, name="app", help="APK management.")
app.add_typer(serial_cli, name="serial", help="UART / serial (method G).")
app.add_typer(skills_cli, name="skills", help="SKILL.md generator for LLM clients.")
app.add_typer(chat_cli, name="chat", help="Interactive LLM agent REPL.")
app.add_typer(ui_cli, name="ui", help="UI diagnostics (screenshot / uiautomator dump).")
app.add_typer(info_cli, name="info", help="Structured device info (system/cpu/memory/...).")


# ─── Log tool group (search / tail) ────────────────────────────────
log_app = typer.Typer(help="Log inspection commands (workspace-side, no transport).")
app.add_typer(log_app, name="log")


@log_app.command("search")
def log_search(
    ctx: typer.Context,
    pattern: str = typer.Argument(...),
    path: Path | None = typer.Option(None, "--path"),
    device: str | None = typer.Option(None, "--device"),
    max_matches: int = typer.Option(200, "--max"),
) -> None:
    """Regex-search across collected logs."""
    result = run_async(
        search_logs(pattern, path=path, device=device, max_matches=max_matches)
    )
    print_result(ctx, result)


@log_app.command("tail")
def log_tail(
    ctx: typer.Context,
    path: Path = typer.Argument(...),
    lines: int = typer.Option(50, "--lines", "-n"),
    from_line: int | None = typer.Option(None, "--from"),
    to_line: int | None = typer.Option(None, "--to"),
) -> None:
    """Read the tail (or a range) of a workspace log file."""
    result = run_async(
        tail_log(path, lines=lines, from_line=from_line, to_line=to_line)
    )
    print_result(ctx, result)


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        console.print("\n[red]Interrupted[/]")
        sys.exit(130)


if __name__ == "__main__":
    main()
