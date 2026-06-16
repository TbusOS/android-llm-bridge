"""Web API: GET /api/info/{panel} — per-panel device info (ARCH-2).

Exposes the :mod:`alb.capabilities.info` panels (security / gpu /
processes / cpu / …) over REST so the Inspect "System Info" tab can
surface high-value fields — verified boot / AVB / verity / SELinux,
GPU governor + freq, top processes — that were previously reachable only
from the CLI (``alb info <panel>``) and MCP (``alb_info``). The
``/devices/{serial}/system`` aggregate covers props/storage/network/etc;
this endpoint fills the panels it doesn't.

One source of truth: the same panel functions back CLI / MCP, so there is
no behaviour drift. Thin envelope over ``info.all_info`` — errors come
back as ``{ok: false, error}`` so the front-end renders them inline.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from alb.capabilities import info as info_cap
from alb.infra.result import envelope_dict, envelope_transport_init_error
from alb.infra.workspace import is_safe_device
from alb.transport.factory import build_transport

router = APIRouter(prefix="/api/info", tags=["info"])


@router.get("/{panel}")
async def get_info_panel(
    panel: str,
    device: str | None = Query(None, max_length=128),
) -> dict[str, Any]:
    """Run one info panel and return its envelope.

    ``panel`` must be one of ``info.panel_names()`` (system / cpu / gpu /
    memory / storage / network / battery / security / display / packages /
    processes); anything else is 404.
    """
    if panel not in info_cap.panel_names():
        raise HTTPException(status_code=404, detail="unknown panel")
    if device and not is_safe_device(device):
        # L-051: no-echo on reject. See diag_route._resolve_transport.
        raise HTTPException(status_code=400, detail="invalid device serial")
    try:
        transport = build_transport(device_serial=device)
    except Exception as e:  # noqa: BLE001
        return envelope_transport_init_error(e, device=device)
    results = await info_cap.all_info(transport, device=device, panels=[panel])
    r = results.get(panel)
    if r is None:  # defensive — panel was validated above
        raise HTTPException(status_code=404, detail="unknown panel")
    return envelope_dict(r)
