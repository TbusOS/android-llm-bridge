"""Web API: /api/app/* — APK install / uninstall / start / stop / list /
info / clear-data REST surface.

Mirrors the CLI's ``alb app *`` subcommands through the shared
:mod:`alb.capabilities.app`. Used by the Inspect "App" tab.

Endpoints:

  GET  /api/app/list?device=...&filter=X&include_system=bool
  GET  /api/app/info?device=...&package=...
  POST /api/app/start          body {component}
  POST /api/app/stop           body {package}
  POST /api/app/clear-data     body {package, allow_dangerous}
  POST /api/app/uninstall      body {package, keep_data, allow_dangerous}
  POST /api/app/install        multipart: apk file
      query: ?device=...&replace=bool&grant_runtime=bool&downgrade=bool

The install endpoint writes the uploaded APK to a NamedTemporaryFile
and calls the capability with that path — the capability handles the
push + ``pm install`` flow.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field

from alb.capabilities import app as app_cap
from alb.infra.result import envelope_dict, envelope_transport_init_error
from alb.infra.workspace import is_safe_device
from alb.transport.factory import build_transport

router = APIRouter(prefix="/api/app", tags=["app"])


def _resolve_transport(
    device: str | None,
) -> tuple[Any | None, dict[str, Any] | None]:
    """Return ``(transport, None)`` on success or ``(None, envelope_b)``
    on a ``build_transport`` failure.  The route handler should
    ``return envelope_b`` verbatim if it's non-None.

    Bad ``device`` (regex reject) still raises ``HTTPException(400)`` —
    that's envelope shape (c): "input invalid".  Transport init failure
    is envelope shape (b): "device-side / upstream failure", per
    ``architecture.md`` "REST envelope 三态约定".
    """
    if device and not is_safe_device(device):
        # L-051: no-echo on reject (sec MID-1). See diag_route._resolve_transport.
        raise HTTPException(status_code=400, detail="invalid device serial")
    try:
        return build_transport(device_serial=device), None
    except Exception as e:  # noqa: BLE001
        return None, envelope_transport_init_error(e, device=device)


class PackageRequest(BaseModel):
    package: str = Field(..., min_length=1, max_length=256)


class StartRequest(BaseModel):
    component: str = Field(..., min_length=1, max_length=512)


class UninstallRequest(BaseModel):
    package: str = Field(..., min_length=1, max_length=256)
    keep_data: bool = False
    allow_dangerous: bool = False


class ClearDataRequest(BaseModel):
    package: str = Field(..., min_length=1, max_length=256)
    allow_dangerous: bool = False


@router.get("/list")
async def get_list(
    device: str | None = Query(None, max_length=128),
    filter: str | None = Query(None, max_length=128),  # noqa: A002
    include_system: bool = Query(False),
) -> dict[str, Any]:
    transport, err = _resolve_transport(device)
    if err is not None:
        return err
    r = await app_cap.list_apps(
        transport, filter=filter, include_system=include_system
    )
    return envelope_dict(r)


@router.get("/info")
async def get_info(
    package: str = Query(..., max_length=256),
    device: str | None = Query(None, max_length=128),
) -> dict[str, Any]:
    transport, err = _resolve_transport(device)
    if err is not None:
        return err
    r = await app_cap.info(transport, package)
    return envelope_dict(r)


@router.post("/start")
async def post_start(
    body: StartRequest,
    device: str | None = Query(None, max_length=128),
) -> dict[str, Any]:
    transport, err = _resolve_transport(device)
    if err is not None:
        return err
    r = await app_cap.start(transport, body.component)
    return envelope_dict(r)


@router.post("/stop")
async def post_stop(
    body: PackageRequest,
    device: str | None = Query(None, max_length=128),
) -> dict[str, Any]:
    transport, err = _resolve_transport(device)
    if err is not None:
        return err
    r = await app_cap.stop(transport, body.package)
    return envelope_dict(r)


@router.post("/clear-data")
async def post_clear_data(
    body: ClearDataRequest,
    device: str | None = Query(None, max_length=128),
) -> dict[str, Any]:
    transport, err = _resolve_transport(device)
    if err is not None:
        return err
    r = await app_cap.clear_data(
        transport, body.package, allow_dangerous=body.allow_dangerous
    )
    return envelope_dict(r)


@router.post("/uninstall")
async def post_uninstall(
    body: UninstallRequest,
    device: str | None = Query(None, max_length=128),
) -> dict[str, Any]:
    transport, err = _resolve_transport(device)
    if err is not None:
        return err
    r = await app_cap.uninstall(
        transport,
        body.package,
        keep_data=body.keep_data,
        allow_dangerous=body.allow_dangerous,
    )
    return envelope_dict(r)


# Hard ceiling for apk size — protects the server from accidental 500MB
# uploads. Most real APKs are <100MB.
_APK_MAX_BYTES = 500 * 1024 * 1024


def _too_large_envelope() -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": "APK_TOO_LARGE",
            "message": (
                f"APK exceeds {_APK_MAX_BYTES // (1024 * 1024)} MB cap"
            ),
            "suggestion": "Compress, split, or push manually",
        },
    }


@router.post("/install")
async def post_install(
    request: Request,
    apk: UploadFile = File(...),
    device: str | None = Query(None, max_length=128),
    replace: bool = Query(True),
    grant_runtime: bool = Query(False),
    downgrade: bool = Query(False),
) -> dict[str, Any]:
    """Install an APK by streaming the upload to a tempfile then
    delegating to ``app_cap.install``. The tempfile is unlinked in
    ``finally``."""
    transport, err = _resolve_transport(device)
    if err is not None:
        return err

    # Pre-flight Content-Length check: rejects multi-GB uploads at the
    # door rather than streaming them all the way to the per-chunk cap.
    # Multipart envelope adds ~200 bytes overhead, so the header may be
    # marginally larger than the actual APK — comparing directly to the
    # cap is conservative enough (we accept a small slack). The
    # post-write check below stays as the authoritative guard for
    # clients that lie or omit Content-Length.
    cl_header = request.headers.get("content-length")
    if cl_header:
        try:
            if int(cl_header) > _APK_MAX_BYTES:
                return _too_large_envelope()
        except ValueError:
            # Malformed header → fall through to streaming check.
            pass

    # Validate filename to prevent path-traversal even on the temp side.
    name = apk.filename or "upload.apk"
    safe_name = os.path.basename(name)
    if not safe_name.lower().endswith(".apk"):
        return {
            "ok": False,
            "error": {
                "code": "INVALID_FILENAME",
                "message": "Upload filename must end with .apk",
                "suggestion": "Rename the file or pick a real APK",
            },
        }

    # Stream to disk with a hard size cap.
    tmp_dir = Path(tempfile.gettempdir())
    fd, tmp_path = tempfile.mkstemp(prefix="alb-apk-", suffix=".apk", dir=tmp_dir)
    written = 0
    try:
        with os.fdopen(fd, "wb") as f:
            while chunk := await apk.read(1 << 20):  # 1 MiB chunks
                written += len(chunk)
                if written > _APK_MAX_BYTES:
                    return _too_large_envelope()
                # Up to 500 sync 1 MiB writes — keep each off the loop
                # (L-033).
                await asyncio.to_thread(f.write, chunk)
        r = await app_cap.install(
            transport,
            Path(tmp_path),
            replace=replace,
            grant_runtime=grant_runtime,
            downgrade=downgrade,
        )
        return envelope_dict(r)
    finally:
        try:
            await asyncio.to_thread(os.unlink, tmp_path)
        except OSError:
            pass
