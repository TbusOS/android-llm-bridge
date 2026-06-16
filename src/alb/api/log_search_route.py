"""Web API: GET /api/log/search — historical regex search over workspace logs.

Counterpart to the live `/logcat/stream` WS: instead of tailing new
bytes, this scans the already-collected logcat / dmesg / uart artifacts
under ``workspace/devices/<serial>/logs/`` and returns line-level
matches. Powers the Inspect "Log Search" tab.

Backed by :func:`alb.capabilities.logging.search_logs` — one source of
truth shared with the CLI ``alb log search`` and the MCP
``alb_log_search`` tool. The route is a thin envelope over that
capability, with extra guard rails:

  * ``device`` is validated as a safe serial (L-035).
  * ``max`` is capped at 1000 to keep response payloads bounded.
  * Errors come back as ``{ok: false, error}`` envelopes so the front-end
    can render them inline (rather than chasing HTTP error codes).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from alb.capabilities.logging import collect_dmesg, search_logs
from alb.infra.result import envelope_dict, envelope_transport_init_error
from alb.infra.workspace import is_safe_device
from alb.transport.factory import build_transport

router = APIRouter(prefix="/api/log", tags=["log"])


@router.get("/search")
async def get_log_search(
    pattern: str = Query(..., min_length=1, max_length=512),
    device: str | None = Query(None, max_length=128),
    max: int = Query(200, ge=1, le=1000),
) -> dict[str, Any]:
    """Regex-search across workspace logs and return line-level matches.

    The pattern is compiled by the capability layer (``re.compile``);
    bad regex syntax bubbles up as ``ok: false`` with ``error.code =
    INVALID_FILTER`` — no HTTP 500.
    """
    if device and not is_safe_device(device):
        # L-051: no-echo on reject (sec MID-1). See diag_route._resolve_transport.
        raise HTTPException(status_code=400, detail="invalid device serial")
    r = await search_logs(pattern, device=device, max_matches=max)
    return envelope_dict(r)


class DmesgRequest(BaseModel):
    duration: int = Field(10, ge=1, le=3600)


@router.post("/dmesg")
async def post_dmesg(
    body: DmesgRequest,
    device: str | None = Query(None, max_length=128),
) -> dict[str, Any]:
    """Collect a fresh kernel dmesg snapshot into the workspace (ARCH-1).

    The Log Search tab can only search artifacts that already exist; the
    dmesg capability was reachable from CLI / MCP but had no web entry, so
    web-only users could never produce a dmesg artifact to search. Mirrors
    ``alb dmesg`` / ``alb_dmesg`` — collect then search.
    """
    if device and not is_safe_device(device):
        # L-051: no-echo on reject. See diag_route._resolve_transport.
        raise HTTPException(status_code=400, detail="invalid device serial")
    try:
        transport = build_transport(device_serial=device)
    except Exception as e:  # noqa: BLE001
        return envelope_transport_init_error(e, device=device)
    r = await collect_dmesg(transport, duration=body.duration, device=device)
    return envelope_dict(r)
