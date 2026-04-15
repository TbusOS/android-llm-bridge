"""MCP tools: alb_reboot, alb_battery, alb_wait_boot, alb_sleep_wake_test."""

from __future__ import annotations

from typing import Any

from alb.capabilities.power import (
    battery,
    reboot,
    sleep_wake_test,
    wait_boot_completed,
)
from alb.mcp.transport_factory import build_transport


def register(mcp) -> None:  # noqa: ANN001
    @mcp.tool()
    async def alb_reboot(
        mode: str = "normal",
        wait_boot: bool = True,
        timeout: int = 180,
        allow_dangerous: bool = False,
        device: str | None = None,
    ) -> dict[str, Any]:
        """Reboot the device.

        Modes:
            - normal (default, safe)
            - recovery / bootloader / fastboot / sideload (ASK-level,
              may not auto-return; require allow_dangerous=True)

        LLM notes:
            - wait_boot=True waits for sys.boot_completed=1 before returning.
            - For non-normal modes, make sure you have a recovery path
              (another adb connection or UART) before requesting.
        """
        t = build_transport(device_serial=device)
        r = await reboot(
            t,
            mode,
            wait_boot=wait_boot,
            timeout=timeout,
            allow_dangerous=allow_dangerous,
        )
        return r.to_dict()

    @mcp.tool()
    async def alb_wait_boot(
        timeout: int = 180, device: str | None = None
    ) -> dict[str, Any]:
        """Poll sys.boot_completed=1 until true or timeout.

        Returns boot duration (ms) which is useful for boot-speed regression.
        """
        t = build_transport(device_serial=device)
        r = await wait_boot_completed(t, timeout=timeout)
        return r.to_dict()

    @mcp.tool()
    async def alb_battery(device: str | None = None) -> dict[str, Any]:
        """Return structured battery state (level/health/temp/voltage/plugged)."""
        t = build_transport(device_serial=device)
        r = await battery(t)
        return r.to_dict()

    @mcp.tool()
    async def alb_sleep_wake_test(
        cycles: int = 1,
        hold_sec: int = 5,
        device: str | None = None,
    ) -> dict[str, Any]:
        """Trigger N sleep/wake cycles via KEYCODE_POWER / KEYCODE_WAKEUP.

        Useful for power regression testing.
        """
        t = build_transport(device_serial=device)
        r = await sleep_wake_test(t, cycles=cycles, hold_sec=hold_sec)
        return r.to_dict()
