"""Meta endpoints: schema version + endpoint discovery.

Lets Web UI (and any other client) feature-detect what the running
server supports without reading `pyproject.toml` or running `alb --version`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from alb import __version__
from alb.api.schema import API_VERSION, schema_dict

router = APIRouter()


@router.get("/api/version")
async def api_version() -> dict[str, Any]:
    """Return the full Web API schema (REST paths + WS message types)."""
    return schema_dict(alb_version=__version__)


@router.get("/api/schema")
async def api_schema_alias() -> dict[str, Any]:
    """Alias for /api/version — kept so `openapi.json` isn't the only
    discovery route."""
    return schema_dict(alb_version=__version__)


@router.get("/api/ping")
async def api_ping() -> dict[str, str]:
    """Tiny health beacon for uptime probes. Keeps `/health` free for
    later expansion (e.g. device / backend health aggregation)."""
    return {"ok": "true", "v": API_VERSION}
