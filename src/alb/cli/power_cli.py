"""CLI: reboot / battery / wait-boot / sleep-wake."""

from __future__ import annotations

import typer
from rich.markup import escape

from alb.capabilities.power import battery, reboot, sleep_wake_test, wait_boot_completed
from alb.cli.common import err_console, get_transport, print_result, run_async
from alb.transport.factory import serial_fallback_for

app = typer.Typer(help="Power state commands.")


@app.command("reboot")
def cmd_reboot(
    ctx: typer.Context,
    mode: str = typer.Argument("normal"),
    wait: bool = typer.Option(True, "--wait-boot/--no-wait-boot"),
    timeout: int = typer.Option(180, "--timeout"),
    allow_dangerous: bool = typer.Option(False, "--allow-dangerous"),
    device: str | None = typer.Option(None, "--device"),
) -> None:
    """Reboot device. Modes: normal | recovery | bootloader | fastboot | sideload.

    With no --transport / --device, a reboot that adb cannot place (no
    device) is retried over the serial console; adb's error is printed.
    """
    t = get_transport(ctx, device_serial=device)
    fb = serial_fallback_for(
        override=(ctx.obj or {}).get("transport"), device_serial=device
    )
    result = run_async(
        reboot(
            t, mode,
            wait_boot=wait,
            timeout=timeout,
            allow_dangerous=allow_dangerous,
            serial_fallback=fb,
        )
    )
    if result.ok and result.data.fallback and not (ctx.obj or {}).get("json"):
        f = result.data.fallback
        err_console.print(
            f"[yellow]warning:[/] {escape(f['from'])} found no device "
            f"({escape(f['error_code'])}: {escape(f['message'])}); "
            "rebooted over the serial console instead. "
            "Why adb sees nothing: alb devices",
            soft_wrap=True,
        )
    print_result(ctx, result)


@app.command("wait-boot")
def cmd_wait_boot(
    ctx: typer.Context,
    timeout: int = typer.Option(180, "--timeout"),
    device: str | None = typer.Option(None, "--device"),
) -> None:
    """Wait until sys.boot_completed=1."""
    t = get_transport(ctx, device_serial=device)
    result = run_async(wait_boot_completed(t, timeout=timeout))
    print_result(ctx, result)


@app.command("battery")
def cmd_battery(
    ctx: typer.Context, device: str | None = typer.Option(None, "--device")
) -> None:
    """Query battery state."""
    t = get_transport(ctx, device_serial=device)
    result = run_async(battery(t))
    print_result(ctx, result)


@app.command("sleep-wake")
def cmd_sleep_wake(
    ctx: typer.Context,
    cycles: int = typer.Option(1, "--cycles"),
    hold: int = typer.Option(5, "--hold"),
    device: str | None = typer.Option(None, "--device"),
) -> None:
    """Trigger N sleep/wake cycles for power regression."""
    t = get_transport(ctx, device_serial=device)
    result = run_async(sleep_wake_test(t, cycles=cycles, hold_sec=hold))
    print_result(ctx, result)
