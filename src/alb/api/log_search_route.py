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

from alb.capabilities.logging import search_logs
from alb.infra.result import envelope_dict
from alb.infra.workspace import is_safe_device

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
        raise HTTPException(
            status_code=400, detail=f"invalid device serial: {device!r}"
        )
    r = await search_logs(pattern, device=device, max_matches=max)
    return envelope_dict(r)
