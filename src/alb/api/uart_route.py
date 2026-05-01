"""UART capture endpoints (DEBT-022 PR-C.a).

Three endpoints surface the existing `capabilities.logging.capture_uart`
to the Web UI so users can press a button on the inspect page and see
N seconds of UART output without leaving the browser:

    POST /uart/capture?duration=N&device=<serial>
        Run a fresh capture. Forces SerialTransport (override="serial")
        regardless of the active default transport — UART is ortho.
        Returns { ok, lines, errors, filename, path, duration }.

    GET /uart/captures
        List existing *-uart.log under the current workspace's logs
        directory, newest first.

    GET /uart/captures/{name}
        Read one capture's text content (utf-8 with errors='replace'
        so binary noise doesn't crash the JSON encoder).

PR-C.b will add a streaming WS counterpart at `/uart/stream`; this
file owns only the stateless capture-style flow.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from alb.capabilities.logging import capture_uart
from alb.infra.workspace import workspace_root
from alb.mcp.transport_factory import build_transport

router = APIRouter()

# Mirror MCP-level cap (1..3600) but tighten to 5 min for HTTP — anything
# longer than that should use the streaming WS endpoint (PR-C.b) instead
# of holding an HTTP request hostage.
_HTTP_DURATION_MAX = 300


def _logs_dir(device: str | None) -> Path:
    """Resolve the workspace logs dir the same way capture_uart writes
    into. capture_uart uses workspace_path('logs', ..., device=device);
    we mirror that path here so listings show what was just written."""
    root = workspace_root()
    if device:
        return root / "devices" / device / "logs"
    return root / "logs"


def _is_uart_log_name(name: str) -> bool:
    """Filename gate — refuses anything that isn't a plain `*-uart.log`
    sibling of the current logs dir. Blocks path traversal + accidental
    leakage of unrelated workspace files."""
    if "/" in name or "\\" in name or ".." in name:
        return False
    if not name.endswith("-uart.log"):
        return False
    return True


@router.post("/uart/capture")
async def trigger_capture(
    duration: int = Query(30, ge=1, le=_HTTP_DURATION_MAX),
    device: str | None = Query(None),
) -> dict[str, Any]:
    """Run a fresh UART capture for `duration` seconds.

    Always forces SerialTransport — we don't read the env-default
    transport here because UART is the whole point of this endpoint.
    """
    try:
        t = build_transport(override="serial", device_serial=device)
    except Exception as exc:  # noqa: BLE001 — surface init failures inline
        return {
            "ok": False,
            "duration": duration,
            "error": f"{type(exc).__name__}: {exc}",
        }

    r = await capture_uart(t, duration=duration, device=device)
    if not r.ok:
        return {
            "ok": False,
            "duration": duration,
            "error": r.error.message if r.error else "capture_uart failed",
        }

    artifact: Path | None = r.artifacts[0] if r.artifacts else None
    return {
        "ok": True,
        "duration": duration,
        "lines": r.data.lines if r.data else 0,
        "errors": r.data.errors if r.data else 0,
        "filename": artifact.name if artifact else None,
        "path": str(artifact) if artifact else None,
    }


@router.get("/uart/captures")
async def list_captures(device: str | None = Query(None)) -> dict[str, Any]:
    """List captures, newest first. Always returns ok=true with possibly
    empty `captures` so the UI just shows an empty-state instead of a
    server error when no UART has been captured yet."""
    base = _logs_dir(device)
    if not base.exists():
        return {"ok": True, "device": device, "captures": []}

    entries = []
    for p in base.glob("*-uart.log"):
        try:
            stat = p.stat()
        except OSError:
            continue
        entries.append(
            {
                "name": p.name,
                "size_bytes": stat.st_size,
                "mtime": stat.st_mtime,
            }
        )
    entries.sort(key=lambda e: e["mtime"], reverse=True)
    return {"ok": True, "device": device, "captures": entries}


@router.get("/uart/captures/{name}")
async def read_capture(
    name: str,
    device: str | None = Query(None),
) -> dict[str, Any]:
    """Return one capture's text content.

    `errors='replace'` because UART buffers may contain raw bytes that
    aren't valid UTF-8 (kernel printk + bootloader noise + occasional
    control codes). We don't strip ANSI here — the frontend renders
    inside a <pre> with a monospace font, control chars stay visible
    as escape sequences (PR-C.b will add an xterm.js view for ANSI
    rendering)."""
    if not _is_uart_log_name(name):
        raise HTTPException(status_code=400, detail="invalid capture name")

    f = _logs_dir(device) / name
    if not f.exists():
        raise HTTPException(status_code=404, detail="capture not found")

    try:
        text = f.read_text(encoding="utf-8", errors="replace")
        size = f.stat().st_size
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "ok": True,
        "name": name,
        "size_bytes": size,
        "text": text,
    }
