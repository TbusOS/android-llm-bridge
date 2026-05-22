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
from alb.infra.result import envelope_dict, envelope_transport_init_error
from alb.infra.workspace import is_safe_device, workspace_root
from alb.mcp.transport_factory import build_transport

router = APIRouter(prefix="/api/diag", tags=["diag"])


def _resolve_transport(
    device: str | None,
) -> tuple[Any | None, dict[str, Any] | None]:
    """See ``power_route._resolve_transport``: tuple-return so handlers
    surface ``build_transport`` failure as envelope shape (b)."""
    if device and not is_safe_device(device):
        raise HTTPException(
            status_code=400, detail=f"invalid device serial: {device!r}"
        )
    try:
        return build_transport(device_serial=device), None
    except Exception as e:  # noqa: BLE001
        return None, envelope_transport_init_error(e, device=device)


class AnrRequest(BaseModel):
    clear_after: bool = False


class TombstoneRequest(BaseModel):
    limit: int = Field(10, ge=1, le=200)


@router.post("/bugreport")
async def post_bugreport(
    device: str | None = Query(None, max_length=128),
) -> dict[str, Any]:
    transport, err = _resolve_transport(device)
    if err is not None:
        return err
    r = await diag_cap.bugreport(transport, device=device)
    return envelope_dict(r)


@router.post("/anr")
async def post_anr(
    body: AnrRequest,
    device: str | None = Query(None, max_length=128),
) -> dict[str, Any]:
    transport, err = _resolve_transport(device)
    if err is not None:
        return err
    r = await diag_cap.anr_pull(
        transport, clear_after=body.clear_after, device=device
    )
    return envelope_dict(r)


@router.post("/tombstone")
async def post_tombstone(
    body: TombstoneRequest,
    device: str | None = Query(None, max_length=128),
) -> dict[str, Any]:
    transport, err = _resolve_transport(device)
    if err is not None:
        return err
    r = await diag_cap.tombstone_pull(
        transport, limit=body.limit, device=device
    )
    return envelope_dict(r)


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

    def _rel(p: Path) -> str | None:
        """Return path relative to workspace root with forward slashes.

        The frontend's `workspaceDownloadUrl()` builds
        `/workspace/files/download/<rel>` so anchors point straight at
        the streaming endpoint. Exposing the absolute filesystem path
        would leak server layout (information disclosure MID, fixed in
        commit 4618600).

        Security finding (5/22 audit MID): originally returned
        `p.as_posix()` on ValueError, re-leaking the very abs path the
        refactor was hiding. Now returns None and callers SKIP the
        entry — defence-in-depth against the symlink-pre-filter ever
        missing one.
        """
        try:
            return p.relative_to(root).as_posix()
        except ValueError:
            return None

    br = base / "bugreports"
    if br.exists():
        for p in sorted(br.iterdir(), reverse=True):
            # is_symlink() short-circuits before is_file() so we refuse
            # to follow any link that may point outside the device's
            # artifact dir (defence-in-depth even though the dir is
            # ours).
            if p.is_symlink() or not p.is_file() or not p.name.endswith(".zip"):
                continue
            # TOCTOU + filesystem race: between is_symlink/is_file and
            # stat, the entry could be unlinked. Skip rather than 500.
            try:
                st = p.stat()
            except OSError:
                continue
            rel = _rel(p)
            if rel is None:
                continue
            out["bugreports"].append(
                {
                    "path": rel,
                    "name": p.name,
                    "size_bytes": st.st_size,
                    "mtime": st.st_mtime,
                }
            )
            if len(out["bugreports"]) >= limit_per_kind:
                break
    for kind in ("anr", "tombstones"):
        kdir = base / kind
        if not kdir.exists():
            continue
        for tdir in sorted(kdir.iterdir(), reverse=True):
            if tdir.is_symlink() or not tdir.is_dir():
                continue
            tdir_rel = _rel(tdir)
            if tdir_rel is None:
                continue
            files: list[dict[str, Any]] = []
            for f in sorted(tdir.iterdir()):
                if f.is_symlink() or not f.is_file():
                    continue
                # Cache stat() — previously called twice per file
                # (.st_size and .st_mtime). Same TOCTOU guard as above.
                try:
                    st = f.stat()
                except OSError:
                    continue
                rel = _rel(f)
                if rel is None:
                    continue
                files.append(
                    {
                        "path": rel,
                        "name": f.name,
                        "size_bytes": st.st_size,
                        "mtime": st.st_mtime,
                    }
                )
            out[kind].append(
                {
                    "bundle": tdir.name,
                    "path": tdir_rel,
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
