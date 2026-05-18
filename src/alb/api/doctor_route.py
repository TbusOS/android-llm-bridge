"""Web API: GET /doctor — environment health snapshot.

Mirrors the CLI's ``alb doctor`` six-layer report so the web
DoctorPanel can render the same diagnostic without forking a subprocess.
One source of truth: both call into :mod:`alb.capabilities.doctor`.

Response shape (same as ``alb --json doctor``):

    {
      "layers": [
        {"name": "env", "status": "ok", "checks": [
            {"name": "ALB_WORKSPACE", "status": "skip", "detail": "(unset)"},
            ...
        ]},
        ...
      ],
      "summary": {"ok": 5, "warn": 0, "err": 0, "skip": 1}
    }
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from alb.capabilities.doctor import run_doctor

router = APIRouter(prefix="/api", tags=["doctor"])


@router.get("/doctor")
async def get_doctor() -> dict[str, Any]:
    """Run the six-layer health probe and return the JSON payload.

    Probes run concurrently (sync ones via ``asyncio.to_thread``) so the
    whole snapshot completes in roughly the worst single-probe latency
    (~1.5 s for a slow TCP check), not the sum of all probes.
    """
    return await run_doctor()
