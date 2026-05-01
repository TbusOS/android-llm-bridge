"""GET /devices — list devices visible through the active transport.

Thin HTTP wrapper around `Transport.devices()`. Mirrors the
`alb_devices` MCP tool (src/alb/mcp/tools/devices.py) but flattens the
response so the Web UI doesn't have to peel off a `data` envelope.

Transports without a `.devices()` method (ssh / serial) return an empty
list rather than 404 — the UI can still ask "what's the active
transport?" and render an empty state.

Also serves `GET /devices/{serial}/details` — composite device snapshot
(brand / SoC / RAM / display / temperature / battery / storage) for the
dashboard device card. ADR-028 (a) — split summary endpoint from the
inspect "full system dump" endpoint that PR-B will add.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from fastapi import APIRouter

import base64
from pathlib import Path

from alb.capabilities.diagnose import device_system, devinfo
from alb.capabilities.ui import screenshot, ui_dump
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


@router.get("/devices/{serial}/details")
async def device_details(serial: str) -> dict[str, Any]:
    """Composite device snapshot for the dashboard summary card.

    Wraps `alb.capabilities.diagnose.devinfo()` — same data as the
    `alb_devinfo` MCP tool but reachable via HTTP. ADR-028 (a) — this
    endpoint is the **summary** view; the **full system dump**
    (partition / memory / flash layout) belongs to a future
    `/devices/{serial}/system` endpoint (DEBT-022 PR-B).

    Shape::

        {
          "ok": true,
          "serial": "<requested serial>",
          "transport": "AdbTransport",
          "device": {
            "model": "...", "brand": "...", "manufacturer": "...",
            "sdk": "33", "release": "13", "abi": "arm64-v8a",
            "hardware": "...", "build_fingerprint": "...",
            "serialno": "...", "uptime_sec": 12345,
            "battery_level": 82,
            "storage": {"/data": "used=... avail=..."},
            "extras": {
              "soc": "Tensor G2", "cpu_cores": 8,
              "cpu_max_khz": 2802000,
              "ram_total_kb": 7929164, "ram_avail_kb": 5500000,
              "display": {"size": "1080x2400", "density": "420"},
              "temp_c": 47.35
            }
          }
        }

    On transport / shell failure stays HTTP 200 with `ok: false` so the
    UI can render a per-card error inline.
    """
    try:
        t = build_transport(device_serial=serial)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "serial": serial,
            "transport": None,
            "device": None,
            "error": f"{type(exc).__name__}: {exc}",
        }

    try:
        r = await devinfo(t)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "serial": serial,
            "transport": type(t).__name__,
            "device": None,
            "error": f"{type(exc).__name__}: {exc}",
        }

    if not r.ok:
        return {
            "ok": False,
            "serial": serial,
            "transport": type(t).__name__,
            "device": None,
            "error": r.error.message if r.error else "devinfo failed",
        }

    return {
        "ok": True,
        "serial": serial,
        "transport": type(t).__name__,
        "device": r.data.to_dict() if r.data else None,
    }


@router.get("/devices/{serial}/system")
async def device_system_endpoint(serial: str) -> dict[str, Any]:
    """Full system snapshot for the inspect detail page (DEBT-022 PR-B).

    ADR-028 (a) — this is the **system** view: heavier payload than
    `/details` (full props + partitions + mounts + block devices +
    full meminfo + storage + network + battery + thermal). Pulled on
    demand by inspect SystemInfoTab; not on the dashboard polling
    path so payload size doesn't matter.

    Errors stay 200 with ok=false so the UI can render inline.
    """
    try:
        t = build_transport(device_serial=serial)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "serial": serial,
            "transport": None,
            "system": None,
            "error": f"{type(exc).__name__}: {exc}",
        }

    try:
        r = await device_system(t)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "serial": serial,
            "transport": type(t).__name__,
            "system": None,
            "error": f"{type(exc).__name__}: {exc}",
        }

    if not r.ok:
        return {
            "ok": False,
            "serial": serial,
            "transport": type(t).__name__,
            "system": None,
            "error": r.error.message if r.error else "device_system failed",
        }

    return {
        "ok": True,
        "serial": serial,
        "transport": type(t).__name__,
        "system": r.data.to_dict() if r.data else None,
    }


@router.post("/devices/{serial}/screenshot")
async def device_screenshot(serial: str) -> dict[str, Any]:
    """Capture a fresh PNG framebuffer + return base64 inline (PR-G).

    base64 inflates the payload by ~33% but skips the second-round
    fetch — the dashboard inspect tab can `<img src="data:...">`
    immediately. v1 simplification; if PNGs ever exceed a few MB we'll
    add a separate `GET /devices/{serial}/screenshots/{filename}` for
    binary streaming.
    """
    try:
        t = build_transport(device_serial=serial)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "serial": serial,
            "transport": None,
            "screenshot": None,
            "error": f"{type(exc).__name__}: {exc}",
        }

    try:
        r = await screenshot(t, device=serial, include_thumbnail=False)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "serial": serial,
            "transport": type(t).__name__,
            "screenshot": None,
            "error": f"{type(exc).__name__}: {exc}",
        }

    if not r.ok or r.data is None:
        return {
            "ok": False,
            "serial": serial,
            "transport": type(t).__name__,
            "screenshot": None,
            "error": r.error.message if r.error else "screenshot failed",
        }

    try:
        png_bytes = Path(r.data.path).read_bytes()
        png_b64 = base64.b64encode(png_bytes).decode("ascii")
    except OSError as exc:
        return {
            "ok": False,
            "serial": serial,
            "transport": type(t).__name__,
            "screenshot": None,
            "error": f"OSError reading PNG: {exc}",
        }

    return {
        "ok": True,
        "serial": serial,
        "transport": type(t).__name__,
        "screenshot": {
            "filename": Path(r.data.path).name,
            "path": r.data.path,
            "device_path": r.data.device_path,
            "size_bytes": r.data.size_bytes,
            "width": r.data.width,
            "height": r.data.height,
            "png_base64": png_b64,
        },
    }


@router.post("/devices/{serial}/ui-dump")
async def device_ui_dump(serial: str) -> dict[str, Any]:
    """Dump the current view hierarchy to JSON (PR-G).

    Returns the parsed UINode tree + top_activity + node_count. The
    raw XML is also kept on disk (dump.path) for advanced debugging.
    """
    try:
        t = build_transport(device_serial=serial)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "serial": serial,
            "transport": None,
            "ui_dump": None,
            "error": f"{type(exc).__name__}: {exc}",
        }

    try:
        r = await ui_dump(t, device=serial)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "serial": serial,
            "transport": type(t).__name__,
            "ui_dump": None,
            "error": f"{type(exc).__name__}: {exc}",
        }

    if not r.ok or r.data is None:
        return {
            "ok": False,
            "serial": serial,
            "transport": type(t).__name__,
            "ui_dump": None,
            "error": r.error.message if r.error else "ui_dump failed",
        }

    return {
        "ok": True,
        "serial": serial,
        "transport": type(t).__name__,
        "ui_dump": r.data.to_dict(),
    }
