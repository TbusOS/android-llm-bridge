"""power capability — reboot / sleep-wake / battery / wait_boot.

See docs/capabilities/power.md.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from alb.infra.result import Result, fail, ok
from alb.transport.base import Transport


@dataclass(frozen=True)
class RebootResult:
    mode: str
    wait_boot_ms: int | None

    def to_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "wait_boot_ms": self.wait_boot_ms}


@dataclass(frozen=True)
class BootResult:
    boot_completed: bool
    duration_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "boot_completed": self.boot_completed,
            "duration_ms": self.duration_ms,
        }


@dataclass(frozen=True)
class BatteryInfo:
    level: int
    scale: int
    health: str
    status: str
    plugged: str
    temperature_deci_c: int
    voltage_mv: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "scale": self.scale,
            "health": self.health,
            "status": self.status,
            "plugged": self.plugged,
            "temperature_celsius": round(self.temperature_deci_c / 10.0, 1),
            "voltage_mv": self.voltage_mv,
        }


VALID_MODES = {"normal", "recovery", "bootloader", "fastboot", "sideload"}


async def reboot(
    transport: Transport,
    mode: str = "normal",
    *,
    wait_boot: bool = True,
    timeout: int = 180,
    allow_dangerous: bool = False,
) -> Result[RebootResult]:
    """Reboot device. Modes: normal | recovery | bootloader | fastboot | sideload.

    LLM: recovery/bootloader/fastboot/sideload require adb transport and
    trigger the ASK permission path — set allow_dangerous=True to proceed.
    """
    if mode not in VALID_MODES:
        return fail(
            code="INVALID_FILTER",
            message=f"Unknown reboot mode: {mode}",
            suggestion=f"Use one of: {sorted(VALID_MODES)}",
            category="input",
        )

    if mode in {"recovery", "bootloader", "fastboot", "sideload"} and transport.name != "adb":
        return fail(
            code="TRANSPORT_NOT_SUPPORTED",
            message=f"Reboot mode '{mode}' requires adb transport",
            suggestion="Use adb (method A) for this operation",
            category="transport",
        )

    perm = await transport.check_permissions("power.reboot", {"mode": mode})
    if perm.behavior == "deny":
        return fail(
            code="PERMISSION_DENIED",
            message=perm.reason or "reboot blocked",
            suggestion=perm.suggestion or "",
            category="permission",
        )
    if perm.behavior == "ask" and not allow_dangerous:
        return fail(
            code="PERMISSION_DENIED",
            message=perm.reason or "reboot needs confirmation",
            suggestion=perm.suggestion
            or "Re-run with --allow-dangerous after confirming",
            category="permission",
            details={"behavior": "ask"},
        )

    r = await transport.reboot(mode)
    if not r.ok:
        return fail(
            code=r.error_code or "ADB_COMMAND_FAILED",
            message=r.stderr.strip() or "reboot failed",
            suggestion="Check transport / device state",
            category="transport",
            details={"stderr": r.stderr},
            timing_ms=r.duration_ms,
        )

    wait_ms: int | None = None
    if wait_boot and mode == "normal":
        wr = await wait_boot_completed(transport, timeout=timeout)
        if not wr.ok:
            return fail(
                code=wr.error.code if wr.error else "TIMEOUT_BOOT",
                message=(wr.error.message if wr.error else "boot timeout"),
                suggestion=(wr.error.suggestion if wr.error else ""),
                category="timeout",
                details={"reboot_completed": True},
                timing_ms=r.duration_ms + wr.timing_ms,
            )
        wait_ms = wr.timing_ms

    return ok(
        data=RebootResult(mode=mode, wait_boot_ms=wait_ms),
        timing_ms=r.duration_ms + (wait_ms or 0),
    )


async def wait_boot_completed(
    transport: Transport, *, timeout: int = 180, poll_sec: float = 3.0
) -> Result[BootResult]:
    """Poll sys.boot_completed=1 until true or timeout."""
    start = perf_counter()
    while perf_counter() - start < timeout:
        r = await transport.shell("getprop sys.boot_completed", timeout=5)
        if r.ok and r.stdout.strip() == "1":
            duration_ms = int((perf_counter() - start) * 1000)
            return ok(
                data=BootResult(boot_completed=True, duration_ms=duration_ms),
                timing_ms=duration_ms,
            )
        await asyncio.sleep(poll_sec)
    duration_ms = int((perf_counter() - start) * 1000)
    return fail(
        code="TIMEOUT_BOOT",
        message=f"sys.boot_completed did not become 1 within {timeout}s",
        suggestion="Device may have kernel panic; check UART (method G)",
        category="timeout",
        timing_ms=duration_ms,
    )


async def battery(transport: Transport) -> Result[BatteryInfo]:
    """Query battery state via dumpsys battery."""
    r = await transport.shell("dumpsys battery", timeout=10)
    if not r.ok:
        return fail(
            code=r.error_code or "ADB_COMMAND_FAILED",
            message=r.stderr.strip() or "dumpsys battery failed",
            suggestion="Device may be offline",
            category="transport",
            timing_ms=r.duration_ms,
        )
    return ok(data=_parse_battery(r.stdout), timing_ms=r.duration_ms)


async def sleep_wake_test(
    transport: Transport,
    *,
    cycles: int = 1,
    hold_sec: int = 5,
) -> Result[dict[str, Any]]:
    """Trigger N sleep/wake cycles via keyevents."""
    if cycles < 1 or cycles > 1000:
        return fail(
            code="INVALID_DURATION",
            message=f"cycles must be 1..1000, got {cycles}",
            suggestion="Use a smaller number",
            category="input",
        )

    records: list[dict[str, Any]] = []
    overall_start = perf_counter()
    for i in range(cycles):
        t0 = perf_counter()
        await transport.shell("input keyevent KEYCODE_POWER", timeout=10)
        await asyncio.sleep(hold_sec)
        await transport.shell("input keyevent KEYCODE_WAKEUP", timeout=10)
        await transport.shell("input keyevent KEYCODE_MENU", timeout=10)
        records.append({"cycle": i + 1, "duration_ms": int((perf_counter() - t0) * 1000)})

    duration_ms = int((perf_counter() - overall_start) * 1000)
    return ok(
        data={"cycles": cycles, "records": records, "total_ms": duration_ms},
        timing_ms=duration_ms,
    )


def _parse_battery(stdout: str) -> BatteryInfo:
    fields: dict[str, str] = {}
    for raw in stdout.splitlines():
        line = raw.strip()
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        fields[k.strip()] = v.strip()

    def _int(key: str, default: int = -1) -> int:
        try:
            return int(fields.get(key, str(default)))
        except ValueError:
            return default

    return BatteryInfo(
        level=_int("level"),
        scale=_int("scale", 100),
        health=fields.get("health", ""),
        status=fields.get("status", ""),
        plugged=fields.get("AC powered", "")
        or fields.get("plugged", ""),
        temperature_deci_c=_int("temperature"),
        voltage_mv=_int("voltage"),
    )
