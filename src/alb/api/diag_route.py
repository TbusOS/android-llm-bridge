"""Web API: /api/diag/* — bugreport / ANR / tombstone triggers + list.

Thin wrappers around :mod:`alb.capabilities.diagnose` so the Inspect
"Diag" tab can trigger the same artefact pulls the CLI ``alb diag *``
subcommands do, plus a small GET helper to enumerate the artefacts
that have already been collected for a device.

Endpoints:

  POST /api/diag/bugreport?device=<serial>
      Pulls a full bugreportz zip. Heavy (60-180s) — UI should use a
      long timeout / running spinner.

  POST /api/diag/anr?device=<serial>
      Body: {"clear_after": bool}
      Pulls /data/anr/*.txt and optionally clears.

  POST /api/diag/tombstone?device=<serial>
      Body: {"limit": int}
      Pulls /data/tombstones/* (newest N).

  GET  /api/diag/artifacts?device=<serial>
      List already-collected bugreports / anr / tombstones bundles
      under workspace/devices/<serial>/. Read-only; bounded by N=200
      per kind to keep payloads sane.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from alb.capabilities import diagnose as diag_cap
from alb.infra.workspace import is_safe_device, workspace_root
from alb.mcp.transport_factory import build_transport

router = APIRouter(prefix="/api/diag", tags=["diag"])


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


class AnrRequest(BaseModel):
    clear_after: bool = False


class TombstoneRequest(BaseModel):
    limit: int = Field(10, ge=1, le=200)


def _envelope(r: Any) -> dict[str, Any]:
    if not r.ok:
        return {
            "ok": False,
            "error": r.error.to_dict() if r.error else None,
            "timing_ms": r.timing_ms,
        }
    return {
        "ok": True,
        "data": r.data.to_dict() if r.data else {},
        "timing_ms": r.timing_ms,
    }


@router.post("/bugreport")
async def post_bugreport(
    device: str | None = Query(None, max_length=128),
) -> dict[str, Any]:
    transport = _resolve_transport(device)
    r = await diag_cap.bugreport(transport, device=device)
    return _envelope(r)


@router.post("/anr")
async def post_anr(
    body: AnrRequest,
    device: str | None = Query(None, max_length=128),
) -> dict[str, Any]:
    transport = _resolve_transport(device)
    r = await diag_cap.anr_pull(
        transport, clear_after=body.clear_after, device=device
    )
    return _envelope(r)


@router.post("/tombstone")
async def post_tombstone(
    body: TombstoneRequest,
    device: str | None = Query(None, max_length=128),
) -> dict[str, Any]:
    transport = _resolve_transport(device)
    r = await diag_cap.tombstone_pull(
        transport, limit=body.limit, device=device
    )
    return _envelope(r)


def _scan_artifacts_in_thread(
    root: Path, device: str, limit_per_kind: int = 200
) -> dict[str, Any]:
    """Enumerate workspace/devices/<device>/{bugreports,anr,tombstones}.

    Pure sync, called via asyncio.to_thread per L-033. Bugreports are
    flat ``.zip`` files; ANR / tombstone live in timestamped sub-dirs
    so we flatten them to (timestamp, file) pairs.
    """
    base = root / "devices" / device
    out: dict[str, list[dict[str, Any]]] = {
        "bugreports": [],
        "anr": [],
        "tombstones": [],
    }
    br = base / "bugreports"
    if br.exists():
        for p in sorted(br.iterdir(), reverse=True):
            if not p.is_file() or not p.name.endswith(".zip"):
                continue
            stat = p.stat()
            out["bugreports"].append(
                {
                    "path": str(p),
                    "name": p.name,
                    "size_bytes": stat.st_size,
                    "mtime": stat.st_mtime,
                }
            )
            if len(out["bugreports"]) >= limit_per_kind:
                break
    for kind in ("anr", "tombstones"):
        kdir = base / kind
        if not kdir.exists():
            continue
        for tdir in sorted(kdir.iterdir(), reverse=True):
            if not tdir.is_dir():
                continue
            files = [
                {
                    "path": str(f),
                    "name": f.name,
                    "size_bytes": f.stat().st_size,
                    "mtime": f.stat().st_mtime,
                }
                for f in sorted(tdir.iterdir())
                if f.is_file()
            ]
            out[kind].append(
                {
                    "bundle": tdir.name,
                    "path": str(tdir),
                    "files": files,
                    "count": len(files),
                }
            )
            if len(out[kind]) >= limit_per_kind:
                break
    return out


@router.get("/artifacts")
async def get_artifacts(
    device: str = Query(..., max_length=128),
    limit_per_kind: int = Query(200, ge=1, le=500),
) -> dict[str, Any]:
    """List already-collected diag bundles under workspace."""
    if not is_safe_device(device):
        raise HTTPException(
            status_code=400, detail=f"invalid device serial: {device!r}"
        )
    root = workspace_root()
    data = await asyncio.to_thread(
        _scan_artifacts_in_thread, root, device, limit_per_kind
    )
    return {"ok": True, "data": data}
