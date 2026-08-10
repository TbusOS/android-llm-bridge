"""Web API: /api/board-config/* — find and read the board's config partition.

Mirrors `alb config` through the same capability layer so the web panel and
the CLI cannot drift apart in what they consider a config partition.

Endpoints:

    GET /api/board-config/scan?device=<serial>
        Partitions whose head parses as KEY="VALUE". Detection is by CONTENT,
        not by name — the by-name label differs per product, so anything that
        hard-codes or asks for a name silently finds nothing on the next board.

    GET /api/board-config/read?device=<serial>&name=<by-name>&bytes=<n>
        The partition's head, parsed. This is the readback the flash path does
        not perform: `Writing OKAY` is fastboot saying it believes it wrote.

Read-only on purpose. Writing config from a browser would carry a flash's
destructive power without a flash's protections (two-step arm, digest check,
single-job lock); that path stays in /api/flash.

Requires root and a board that has booted into Android — in fastboot there is
no shell to read a block device with.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from alb.capabilities import board_config as cap
from alb.infra.result import envelope_dict, envelope_transport_init_error
from alb.infra.workspace import is_safe_device
from alb.transport.factory import build_transport

router = APIRouter(prefix="/api/board-config", tags=["board-config"])


def _resolve_transport(device: str | None) -> tuple[Any | None, dict[str, Any] | None]:
    if device and not is_safe_device(device):
        # No echo of the rejected value (L-051).
        raise HTTPException(status_code=400, detail="invalid device serial")
    try:
        return build_transport(device_serial=device), None
    except Exception as e:  # a transport that will not build is a device-side fact
        return None, envelope_transport_init_error(e, device=device)


@router.get("/scan")
async def scan_config(
    device: str | None = Query(None, max_length=128),
) -> dict[str, Any]:
    transport, err = _resolve_transport(device)
    if err is not None:
        return err
    assert transport is not None
    return envelope_dict(await cap.scan(transport))


@router.get("/read")
async def read_config(
    device: str | None = Query(None, max_length=128),
    name: str = Query(..., max_length=64, description="by-name entry from /scan"),
    max_bytes: int = Query(cap.DEFAULT_READ_BYTES, alias="bytes", ge=1, le=cap.MAX_READ_BYTES),
) -> dict[str, Any]:
    transport, err = _resolve_transport(device)
    if err is not None:
        return err
    assert transport is not None
    # `name` is shape-checked inside the capability, which is also where the
    # CLI enters — one gate, not two that can drift.
    return envelope_dict(await cap.read(transport, name, limit=max_bytes))
