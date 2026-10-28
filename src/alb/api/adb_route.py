"""Web API: /api/adb/* -- adbd state (root / unroot / remount).

Wraps :mod:`alb.capabilities.adbd`, the same code `alb adb root | unroot |
remount` runs, so the web page and the CLI report identical outcomes and
identical error codes (ADB_ROOT_REFUSED, ADB_UNROOT_NO_EFFECT, ...).

Endpoints (all POST, all `?device=<serial>`, no body):

    /api/adb/root       restart adbd as root, verify `id -u` == 0
    /api/adb/unroot     restart adbd as the shell user, verify `id -u` != 0
    /api/adb/remount    `adb remount`; verity is never disabled from here

A refusal by the device (a production build will not run adbd as root) is an
ordinary `ok: false` envelope, not an HTTP error.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from alb.capabilities import adbd as adbd_cap
from alb.infra.result import envelope_dict, envelope_transport_init_error
from alb.infra.workspace import is_safe_device
from alb.transport.factory import build_transport

router = APIRouter(prefix="/api/adb", tags=["adb"])


def _resolve_transport(
    device: str | None,
) -> tuple[Any | None, dict[str, Any] | None]:
    """See ``power_route._resolve_transport``."""
    if device and not is_safe_device(device):
        # L-051: no-echo on reject.
        raise HTTPException(status_code=400, detail="invalid device serial")
    try:
        return build_transport(device_serial=device), None
    except Exception as e:
        return None, envelope_transport_init_error(e, device=device)


@router.post("/root")
async def post_root(device: str | None = Query(None, max_length=128)) -> dict[str, Any]:
    transport, err = _resolve_transport(device)
    if err is not None:
        return err
    assert transport is not None  # err is None means the transport was built
    return envelope_dict(await adbd_cap.root(transport))


@router.post("/unroot")
async def post_unroot(device: str | None = Query(None, max_length=128)) -> dict[str, Any]:
    transport, err = _resolve_transport(device)
    if err is not None:
        return err
    assert transport is not None  # err is None means the transport was built
    return envelope_dict(await adbd_cap.unroot(transport))


@router.post("/remount")
async def post_remount(device: str | None = Query(None, max_length=128)) -> dict[str, Any]:
    transport, err = _resolve_transport(device)
    if err is not None:
        return err
    assert transport is not None  # err is None means the transport was built
    return envelope_dict(await adbd_cap.remount(transport))
