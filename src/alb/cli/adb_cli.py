"""CLI: adbd state — `alb adb root | unroot | remount`.

Mounted as `alb adb` by main.py. Debugging defaults to root (dmesg,
/proc/cmdline, /data all need it); this group replaces typing raw `adb root`
by hand and remembering the toybox `su 0 <cmd>` form when a build refuses.
"""

from __future__ import annotations

import typer

from alb.capabilities.adbd import remount, root, unroot
from alb.cli.common import get_transport, print_result, run_async

app = typer.Typer(help="adbd state: root / unroot / remount.")

_TIMEOUT_HELP = "Seconds to wait for adbd to come back after it restarts (bounded poll)."


@app.command("root")
def cmd_root(
    ctx: typer.Context,
    timeout: float = typer.Option(15.0, "--timeout", help=_TIMEOUT_HELP),
    device: str | None = typer.Option(None, "--device"),
) -> None:
    """Restart adbd as root and verify `id -u` == 0 (became / already / refused)."""
    t = get_transport(ctx, device_serial=device)
    result = run_async(root(t, timeout=timeout))
    print_result(ctx, result)


@app.command("unroot")
def cmd_unroot(
    ctx: typer.Context,
    timeout: float = typer.Option(15.0, "--timeout", help=_TIMEOUT_HELP),
    device: str | None = typer.Option(None, "--device"),
) -> None:
    """Restart adbd as the shell user and verify `id -u` != 0."""
    t = get_transport(ctx, device_serial=device)
    result = run_async(unroot(t, timeout=timeout))
    print_result(ctx, result)


@app.command("remount")
def cmd_remount(
    ctx: typer.Context,
    device: str | None = typer.Option(None, "--device"),
) -> None:
    """`adb remount` — output verbatim; outcome: remounted / needs_reboot / failed.

    Never runs `adb disable-verity` for you (it changes boot verification).
    """
    t = get_transport(ctx, device_serial=device)
    result = run_async(remount(t))
    print_result(ctx, result)
