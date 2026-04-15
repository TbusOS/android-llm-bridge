"""CLI: reboot / battery / wait-boot / sleep-wake."""

from __future__ import annotations

import typer

from alb.capabilities.power import battery, reboot, sleep_wake_test, wait_boot_completed
from alb.cli.common import get_transport, print_result, run_async

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
    """Reboot device. Modes: normal | recovery | bootloader | fastboot | sideload."""
    t = get_transport(ctx, device_serial=device)
    result = run_async(
        reboot(
            t, mode,
            wait_boot=wait,
            timeout=timeout,
            allow_dangerous=allow_dangerous,
        )
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
