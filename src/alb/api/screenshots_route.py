"""Screenshot list/read endpoints (functional LOW-1 close).

Two endpoints surface the per-device screenshot history that
`capabilities.ui.screenshot` already persists under
`workspace/devices/<serial>/screenshots/<ts>.png`. The Web inspect
page can render a sidebar of past shots without re-capturing.

    GET /devices/{serial}/screenshots
        List existing *.png under that serial's screenshots dir,
        newest first. Each entry: name / size_bytes / mtime / width /
        height (parsed from PNG header — only first 24 bytes read).

    GET /devices/{serial}/screenshots/{name}
        Stream the PNG to the browser as `image/png` (FileResponse).
        Path-traversal guarded: `name` must be a bare *.png filename.

POST /devices/{serial}/screenshot stays as-is (devices_route.py) —
the inline base64 path is what the UI uses immediately after a fresh
capture, then this route fills the sidebar via list invalidation.

Mirrors uart_route.py shape (capture/list/read triple) so the front-
end can reuse the same query/mutation pattern.
"""

from __future__ import annotations

import asyncio
import struct
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from alb.infra.safe_path import resolve_under
from alb.infra.workspace import _SAFE_DEVICE_RE, workspace_root

router = APIRouter()


def _screenshots_dir(serial: str) -> Path:
    """Mirror `capabilities.ui.screenshot`'s output path.

    L-035: reject `..` / absolute / non-safe `serial` BEFORE building the
    Path. `base.resolve()` in `resolve_under` would otherwise flatten an
    escaped base (`devices/../etc/screenshots` → `etc/screenshots`),
    making `resolved.relative_to(base.resolve())` succeed for a file in
    the escaped target.
    """
    if not _SAFE_DEVICE_RE.match(serial):
        raise HTTPException(
            status_code=400,
            detail=f"invalid device serial: {serial!r}",
        )
    return workspace_root() / "devices" / serial / "screenshots"


def _is_screenshot_name(name: str) -> bool:
    """Filename gate — refuses path traversal + non-png siblings.
    Same conservative shape as uart_route._is_uart_log_name."""
    if "/" in name or "\\" in name or ".." in name:
        return False
    if not name.endswith(".png"):
        return False
    return True


def _safe_resolve_screenshot(serial: str, name: str) -> Path:
    """Defer to shared infra helper — single source of truth for the
    "single-filename slot inside a known dir" case (DEBT-032 抽出)."""
    return resolve_under(
        _screenshots_dir(serial),
        name,
        is_valid_name=_is_screenshot_name,
        not_found_detail="screenshot not found",
        invalid_name_detail="invalid screenshot name",
        escape_detail="path escapes screenshots dir",
    )


def _peek_png_dims(p: Path) -> tuple[int, int] | None:
    """Read width/height from PNG header — first 24 bytes only.

    PNG layout: 8-byte signature + IHDR chunk. Width at [16:20],
    height [20:24] as big-endian uint32. Returns None on any failure
    (truncated file / wrong signature / OS error) — list endpoint
    treats a single bad file as missing dims, not a 500.
    """
    try:
        with p.open("rb") as f:
            head = f.read(24)
    except OSError:
        return None
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    try:
        w, h = struct.unpack(">II", head[16:24])
    except struct.error:
        return None
    return int(w), int(h)


@router.get("/devices/{serial}/screenshots")
async def list_screenshots(serial: str) -> dict[str, Any]:
    """List screenshots for a device, newest first.

    Always returns ok=true with possibly empty `screenshots` so the UI
    just shows an empty-state instead of a server error when no
    capture has happened yet (mirrors /uart/captures).
    """
    base = _screenshots_dir(serial)
    # perf-audit 2026-05-08 LOW: glob + N×(stat + 24-byte read) is fast
    # but still sync FS in async path. Hand off to worker thread (L-033
    # io_to_thread sweep). Cheap on small N, future-proofs against an
    # unbounded screenshots history that becomes hot path.
    entries = await asyncio.to_thread(_list_screenshots_entries, base)
    return {"ok": True, "serial": serial, "screenshots": entries}


def _list_screenshots_entries(base: Path) -> list[dict[str, Any]]:
    """glob + per-entry stat + PNG dim peek in one worker pass."""
    if not base.exists():
        return []
    entries: list[dict[str, Any]] = []
    for p in base.glob("*.png"):
        try:
            stat = p.stat()
        except OSError:
            continue
        dims = _peek_png_dims(p)
        entries.append(
            {
                "name": p.name,
                "size_bytes": stat.st_size,
                "mtime": stat.st_mtime,
                "width": dims[0] if dims else None,
                "height": dims[1] if dims else None,
            }
        )
    entries.sort(key=lambda e: e["mtime"], reverse=True)
    return entries


@router.get("/devices/{serial}/screenshots/{name}")
async def read_screenshot(serial: str, name: str) -> FileResponse:
    """Return one screenshot's PNG bytes as `image/png`."""
    f = _safe_resolve_screenshot(serial, name)
    return FileResponse(
        path=str(f),
        filename=name,
        media_type="image/png",
    )
