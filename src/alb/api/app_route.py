"""Web API: /api/app/* — APK install / uninstall / start / stop / list /
info / clear-data REST surface.

Mirrors the CLI's ``alb app *`` subcommands through the shared
:mod:`alb.capabilities.app`. Used by the Inspect "App" tab.

Endpoints:

  GET  /api/app/list?device=...&filter=X&include_system=bool
  GET  /api/app/info?device=...&package=...
  POST /api/app/start          body {component}
  POST /api/app/stop           body {package}
  POST /api/app/clear-data     body {package}
  POST /api/app/uninstall      body {package, keep_data, allow_dangerous}
  POST /api/app/install        multipart: apk file
      query: ?device=...&replace=bool&grant_runtime=bool&downgrade=bool

The install endpoint writes the uploaded APK to a NamedTemporaryFile
and calls the capability with that path — the capability handles the
push + ``pm install`` flow.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from alb.capabilities import app as app_cap
from alb.infra.workspace import is_safe_device
from alb.mcp.transport_factory import build_transport

router = APIRouter(prefix="/api/app", tags=["app"])


def _resolve_transport(device: str | None) -> Any:
    if device and not is_safe_device(device):
        raise HTTPException(
            status_code=400, detail=f"invalid device serial: {device!r}"
        )
    try:
        return build_transport(device_serial=device)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=503,
            detail=f"transport init failed: {type(e).__name__}: {e}",
        ) from e


def _envelope(r: Any) -> dict[str, Any]:
    if not r.ok:
        return {
            "ok": False,
            "error": r.error.to_dict() if r.error else None,
            "timing_ms": r.timing_ms,
        }
    if hasattr(r.data, "to_dict"):
        data = r.data.to_dict()
    else:
        data = r.data
    return {"ok": True, "data": data, "timing_ms": r.timing_ms}


class PackageRequest(BaseModel):
    package: str = Field(..., min_length=1, max_length=256)


class StartRequest(BaseModel):
    component: str = Field(..., min_length=1, max_length=512)


class UninstallRequest(BaseModel):
    package: str = Field(..., min_length=1, max_length=256)
    keep_data: bool = False
    allow_dangerous: bool = False


@router.get("/list")
async def get_list(
    device: str | None = Query(None, max_length=128),
    filter: str | None = Query(None, max_length=128),  # noqa: A002
    include_system: bool = Query(False),
) -> dict[str, Any]:
    transport = _resolve_transport(device)
    r = await app_cap.list_apps(
        transport, filter=filter, include_system=include_system
    )
    return _envelope(r)


@router.get("/info")
async def get_info(
    package: str = Query(..., max_length=256),
    device: str | None = Query(None, max_length=128),
) -> dict[str, Any]:
    transport = _resolve_transport(device)
    r = await app_cap.info(transport, package)
    return _envelope(r)


@router.post("/start")
async def post_start(
    body: StartRequest,
    device: str | None = Query(None, max_length=128),
) -> dict[str, Any]:
    transport = _resolve_transport(device)
    r = await app_cap.start(transport, body.component)
    return _envelope(r)


@router.post("/stop")
async def post_stop(
    body: PackageRequest,
    device: str | None = Query(None, max_length=128),
) -> dict[str, Any]:
    transport = _resolve_transport(device)
    r = await app_cap.stop(transport, body.package)
    return _envelope(r)


@router.post("/clear-data")
async def post_clear_data(
    body: PackageRequest,
    device: str | None = Query(None, max_length=128),
) -> dict[str, Any]:
    transport = _resolve_transport(device)
    r = await app_cap.clear_data(transport, body.package)
    return _envelope(r)


@router.post("/uninstall")
async def post_uninstall(
    body: UninstallRequest,
    device: str | None = Query(None, max_length=128),
) -> dict[str, Any]:
    transport = _resolve_transport(device)
    r = await app_cap.uninstall(
        transport,
        body.package,
        keep_data=body.keep_data,
        allow_dangerous=body.allow_dangerous,
    )
    return _envelope(r)


# Hard ceiling for apk size — protects the server from accidental 500MB
# uploads. Most real APKs are <100MB.
_APK_MAX_BYTES = 500 * 1024 * 1024


@router.post("/install")
async def post_install(
    apk: UploadFile = File(...),
    device: str | None = Query(None, max_length=128),
    replace: bool = Query(True),
    grant_runtime: bool = Query(False),
    downgrade: bool = Query(False),
) -> dict[str, Any]:
    """Install an APK by streaming the upload to a tempfile then
    delegating to ``app_cap.install``. The tempfile is unlinked in
    ``finally``."""
    transport = _resolve_transport(device)

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
                f.write(chunk)
        r = await app_cap.install(
            transport,
            Path(tmp_path),
            replace=replace,
            grant_runtime=grant_runtime,
            downgrade=downgrade,
        )
        return _envelope(r)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
