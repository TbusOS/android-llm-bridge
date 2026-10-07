"""Shared helpers for CLI subcommands."""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict, is_dataclass
from typing import Any

import typer
from rich.console import Console
from rich.markup import escape

from alb.infra.config import ActiveSettings, load_active as _raw_load_active
from alb.infra.result import Result
from alb.infra.workspace import InvalidProfileName
from alb.mcp.transport_factory import build_transport
from alb.transport.base import Transport

console = Console()
err_console = Console(stderr=True)


def run_async(coro: Any) -> Any:
    """Run an async callable from a sync typer handler."""
    try:
        return asyncio.run(coro)
    except KeyboardInterrupt:
        console.print("\n[red]Interrupted[/]")
        raise typer.Exit(code=130) from None


def load_active_friendly(profile_name: str | None = None) -> ActiveSettings:
    """`load_active` with `InvalidProfileName` → `typer.BadParameter` mapping.

    Use this from any CLI subcommand that needs active settings (status /
    setup / chat / etc.). Bad `--profile` flag or `ALB_PROFILE` env now
    surfaces as a friendly typer error rather than a Python traceback.

    `alb doctor` is the exception — its `_probe_config` deliberately treats
    a bad config as a layer finding, not a fatal error, so doctor uses
    `load_active()` directly inside a try/except.
    """
    try:
        return _raw_load_active(profile_name)
    except InvalidProfileName as e:
        raise typer.BadParameter(
            str(e), param_hint="ALB_PROFILE or --profile"
        ) from None


def get_transport(
    ctx: typer.Context,
    *,
    override: str | None = None,
    device_serial: str | None = None,
) -> Transport:
    """Resolve the active transport (shared with MCP layer).

    Priority:
        explicit `override` > CLI --transport > profile.primary_transport
    """
    which = override or (ctx.obj or {}).get("transport")
    try:
        return build_transport(override=which, device_serial=device_serial)
    except NotImplementedError as e:
        raise typer.BadParameter(str(e)) from e
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e


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
                console.print(f"  • {escape(str(a))}", soft_wrap=True)
        return

    if result.error:
        _print_error(console, result)
        raise typer.Exit(code=1)


def _print_error(con: Console, result: Result[Any]) -> None:
    # message / suggestion often carry device text (stderr), so escape it:
    # rich would otherwise eat anything that looks like "[tag]".
    con.print(
        f"[red]✗ {escape(result.error.code)}[/] — {escape(result.error.message)}",
        soft_wrap=True,
    )
    if result.error.suggestion:
        con.print(
            f"[yellow]suggestion:[/] {escape(result.error.suggestion)}",
            soft_wrap=True,
        )


def print_shell_result(
    ctx: typer.Context, result: Result[Any], *, terminate_last_line: bool = False
) -> None:
    """Render a shell Result the way `adb shell` does.

    stdout / stderr are device data, not display text: they are written
    byte-for-byte (no markup parsing, no line wrapping, no "stdout:" label),
    and the process exits with the device command's exit code. Going through
    the pretty printer used to drop "[false]"-like text, insert newlines at
    80 columns when not on a tty, and hide stdout when the command exited
    non-zero.

    Exit codes: the device command's own code when it ran; 1 when alb could
    not run it at all (timeout, no device, permission rule, ...), in which
    case a one-line reason goes to stderr. --json output is unchanged.

    terminate_last_line: for transports that rebuild stdout line by line
    and drop the final newline (serial: `_extract_between_markers` rstrips,
    `_split_printk` re-joins with "\n"). adb output is already exact and
    must not get one added (`printf x` has no newline on the device either).
    """
    if (ctx.obj or {}).get("json"):
        print_result(ctx, result)
        return

    if result.ok:
        out, err, code = result.data.stdout, result.data.stderr, result.data.exit_code
    else:
        d = result.error.details if result.error else {}
        out, err, code = d.get("stdout") or "", d.get("stderr") or "", d.get("exit_code")

    if out:
        if terminate_last_line and not out.endswith("\n"):
            out += "\n"
        sys.stdout.write(out)
        sys.stdout.flush()

    if result.ok or (isinstance(code, int) and code > 0):
        # The command ran on the device: pass its stderr and exit code through.
        if err:
            sys.stderr.write(err)
            sys.stderr.flush()
        if code:
            raise typer.Exit(code=code)
        return

    # alb itself failed (exit_code -1 / missing): stderr is alb's own message,
    # already in error.message, so print the reason once and exit 1.
    if result.error:
        _print_error(err_console, result)
    raise typer.Exit(code=1)


def _print_kv(k: Any, v: Any) -> None:
    # Values are data (device output, paths, ...): escape so "[false]" stays
    # "[false]", and soft_wrap so long values are not split at 80 columns.
    console.print(f"  [bold]{escape(str(k))}[/]: {escape(str(v))}", soft_wrap=True)


def _print_data_pretty(data: Any) -> None:
    if is_dataclass(data):
        for k, v in asdict(data).items():
            _print_kv(k, v)
    elif isinstance(data, dict):
        for k, v in data.items():
            _print_kv(k, v)
    elif isinstance(data, list):
        for item in data:
            console.print(f"  • {escape(str(item))}", soft_wrap=True)
    elif hasattr(data, "to_dict"):
        for k, v in data.to_dict().items():
            _print_kv(k, v)
    else:
        console.print(str(data), markup=False, soft_wrap=True)


def _json_default(o: Any) -> Any:
    if hasattr(o, "to_dict"):
        return o.to_dict()
    if is_dataclass(o):
        return asdict(o)
    return str(o)
