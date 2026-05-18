"""Web API: /api/power/* — battery query, reboot, sleep-wake.

Mirrors the CLI's `alb power` subcommands through the same capability
layer (:mod:`alb.capabilities.power`) so the web "power panel" reuses
the exact battery parser and reboot-mode validation.

Endpoints:

    GET  /api/power/battery?device=<serial>
        Read-only: `dumpsys battery` → parsed dict.

    POST /api/power/reboot?device=<serial>
        Body: {"mode": "normal" | "recovery" | "bootloader" | "fastboot" |
                       "sideload",
               "wait_boot": bool,                # default true (only honored for normal)
               "timeout": int,                   # seconds; default 180
               "allow_dangerous": bool}          # required for non-normal modes
        Returns the RebootResult / failure envelope verbatim.

    POST /api/power/sleep-wake?device=<serial>
        Body: {"cycles": int, "hold_sec": int}

All endpoints require ``device``: power is per-device, not global.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from alb.capabilities import power as power_cap
from alb.infra.workspace import is_safe_device
from alb.mcp.transport_factory import build_transport

router = APIRouter(prefix="/api/power", tags=["power"])


def _resolve_transport(device: str | None) -> Any:
    """Resolve `device` query string to a transport, rejecting bad serials."""
    if device and not is_safe_device(device):
        raise HTTPException(
            status_code=400, detail=f"invalid device serial: {device!r}"
        )
    try:
        return build_transport(device_serial=device)
    except Exception as e:  # noqa: BLE001 — surface init errors as 503
        raise HTTPException(
            status_code=503,
            detail=f"transport init failed: {type(e).__name__}: {e}",
        ) from e


class RebootRequest(BaseModel):
    mode: str = Field("normal", description="normal/recovery/bootloader/fastboot/sideload")
    wait_boot: bool = True
    timeout: int = Field(180, ge=1, le=900)
    allow_dangerous: bool = False


class SleepWakeRequest(BaseModel):
    cycles: int = Field(1, ge=1, le=1000)
    hold_sec: int = Field(5, ge=1, le=60)


@router.get("/battery")
async def get_battery(
    device: str | None = Query(None, max_length=128),
) -> dict[str, Any]:
    """Return parsed `dumpsys battery` for the active device."""
    transport = _resolve_transport(device)
    r = await power_cap.battery(transport)
    if not r.ok:
        return {
            "ok": False,
            "error": r.error.to_dict() if r.error else None,
        }
    data = r.data.to_dict() if r.data else {}
    return {"ok": True, "data": data, "timing_ms": r.timing_ms}


@router.post("/reboot")
async def post_reboot(
    body: RebootRequest,
    device: str | None = Query(None, max_length=128),
) -> dict[str, Any]:
    """Reboot the device. Non-normal modes require ``allow_dangerous=true``."""
    transport = _resolve_transport(device)
    r = await power_cap.reboot(
        transport,
        mode=body.mode,
        wait_boot=body.wait_boot,
        timeout=body.timeout,
        allow_dangerous=body.allow_dangerous,
    )
    if not r.ok:
        return {
            "ok": False,
            "error": r.error.to_dict() if r.error else None,
            "timing_ms": r.timing_ms,
        }
    return {
        "ok": True,
        "data": r.data.to_dict() if r.data else {},
        "timing_ms": r.timing_ms,
    }


@router.post("/sleep-wake")
async def post_sleep_wake(
    body: SleepWakeRequest,
    device: str | None = Query(None, max_length=128),
) -> dict[str, Any]:
    """Trigger N sleep→wake cycles via key events (KEYCODE_POWER / WAKEUP)."""
    transport = _resolve_transport(device)
    r = await power_cap.sleep_wake_test(
        transport, cycles=body.cycles, hold_sec=body.hold_sec
    )
    if not r.ok:
        return {
            "ok": False,
            "error": r.error.to_dict() if r.error else None,
            "timing_ms": r.timing_ms,
        }
    return {
        "ok": True,
        "data": r.data,
        "timing_ms": r.timing_ms,
    }
