"""GET /devices — list devices visible through the active transport.

Thin HTTP wrapper around `Transport.devices()`. Mirrors the
`alb_devices` MCP tool (src/alb/mcp/tools/devices.py) but flattens the
response so the Web UI doesn't have to peel off a `data` envelope.

Transports without a `.devices()` method (ssh / serial) return an empty
list rather than 404 — the UI can still ask "what's the active
transport?" and render an empty state.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from fastapi import APIRouter

from alb.mcp.transport_factory import build_transport

router = APIRouter()


def _to_jsonable(d: Any) -> dict[str, Any]:
    if is_dataclass(d):
        return asdict(d)
    if isinstance(d, dict):
        return d
    return {"repr": repr(d)}


@router.get("/devices")
async def list_devices() -> dict[str, Any]:
    """Return devices visible through the active transport.

    Shape::

        {
          "ok": true,
          "transport": "AdbTransport",
          "devices": [
            {"serial": "...", "state": "device", "product": "...", "model": "...",
             "transport_id": "..."},
            ...
          ]
        }

    On transport build / probe failure the endpoint stays 200 and reports
    `ok: false` + an `error` string, so the UI can render the failure
    inline rather than treating it as a server crash.
    """
    try:
        t = build_transport()
    except Exception as exc:  # noqa: BLE001 — surface any factory failure as ok=false
        return {
            "ok": False,
            "transport": None,
            "devices": [],
            "error": f"{type(exc).__name__}: {exc}",
        }

    transport_name = type(t).__name__
    if not hasattr(t, "devices"):
        return {"ok": True, "transport": transport_name, "devices": []}

    try:
        devs = await t.devices()
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "transport": transport_name,
            "devices": [],
            "error": f"{type(exc).__name__}: {exc}",
        }

    return {
        "ok": True,
        "transport": transport_name,
        "devices": [_to_jsonable(d) for d in devs],
    }
