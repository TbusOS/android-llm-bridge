"""File browser endpoints for the inspect Files tab (DEBT-022 PR-H).

Five endpoints surface device + workspace file IO so the user can pull
artifacts off a device, push files onto it, and walk both trees in the
browser without dropping to a shell.

    GET  /devices/{serial}/files?path=/sdcard/
        ls -la on the device, parsed into structured entries.
        Always forces the device serial through build_transport.

    GET  /workspace/files?path=devices/<serial>/pulls
        Local workspace listing. Path is workspace-root-relative; we
        refuse anything that resolves outside workspace_root.

    POST /devices/{serial}/files/pull   {remote, local?}
        device → workspace via filesync.pull. `local` is workspace-
        relative; defaults to pulls/<basename>-<ts>.

    POST /devices/{serial}/files/push   {local, remote, force?}
        workspace → device via filesync.push. HITL gate: pushes that
        target a sensitive prefix (/system, /vendor, /data, /dev,
        /proc, /sys, /persist, /oem) come back as
        ok=false / requires_confirm=true unless `force=true` was set.
        The UI surfaces the warning then re-submits with force.

    GET  /workspace/files/download/{path:path}
        Raw bytes — FileResponse stream so big pulls don't fan out
        through JSON+base64. Path must resolve inside workspace_root.

We deliberately do not invoke the M1 PermissionEngine for filesync —
it currently only inspects shell `cmd` strings. The path-prefix HITL
here covers the write-side risk surface until the engine grows
filesync rules (tracked under the broader permissions backlog).
"""

from __future__ import annotations

import asyncio
import os
import posixpath
import shlex
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import FileResponse

from alb.capabilities.filesync import pull as filesync_pull
from alb.capabilities.filesync import push as filesync_push
from alb.infra.workspace import iso_timestamp, workspace_root
from alb.mcp.transport_factory import build_transport

router = APIRouter()

# Per-serial mutex for push/pull. Without this, two browser tabs racing
# `POST /files/push` for the same device collide on adb (functional
# audit 2026-05-02 HIGH 6). Held only for the duration of the transfer
# — different serials run in parallel as expected.
_FILE_OP_LOCKS: dict[str, asyncio.Lock] = {}


def _serial_lock(serial: str) -> asyncio.Lock:
    lock = _FILE_OP_LOCKS.get(serial)
    if lock is None:
        lock = asyncio.Lock()
        _FILE_OP_LOCKS[serial] = lock
    return lock


# Outer timeout for filesync push/pull. USB drop mid-transfer otherwise
# hangs the FastAPI worker indefinitely + blocks subsequent same-serial
# requests via the per-serial lock above (functional audit HIGH 5).
# 5 minutes covers a 1 GB pull at ~3 MB/s (USB 2.0 floor) — anything
# slower probably IS a stall worth aborting.
_FILE_OP_TIMEOUT_S = 300

# Paths that require an explicit user confirm before a push lands —
# these are the prefixes where a wrong byte can soft-brick a board or
# overwrite something the bootloader cares about. Anything outside
# this list (incl. /sdcard, /data/local/tmp) writes without prompting.
_SENSITIVE_REMOTE_PREFIXES: tuple[str, ...] = (
    "/system",
    "/vendor",
    "/data",  # /data/local/tmp is excepted below
    "/dev",
    "/proc",
    "/sys",
    "/persist",
    "/oem",
    "/boot",
    "/recovery",
    "/metadata",
)

# Whitelisted exception inside /data — the standard scratch dir.
_SENSITIVE_REMOTE_EXEMPTIONS: tuple[str, ...] = (
    "/data/local/tmp",
)

# Cap directory listings so we don't ship 50k entries to the browser
# when the user accidentally opens /. The UI shows the truncation hint.
_MAX_ENTRIES = 2000


# ─── Path validation helpers ─────────────────────────────────────────
def _is_safe_remote_path(p: str) -> bool:
    """Reject obvious shell-injection / traversal attempts.

    shlex.quote handles the actual shell escaping when we build the
    `ls` command, but a few patterns are nonsense for a path browser
    and we'd rather 400 than execute them.

    Also rejects any `..` segment — adb resolves it on-device and a
    permitted prefix like `/data/local/tmp/../system/lib/foo.so`
    would otherwise bypass the `_is_sensitive_remote` HITL gate.
    """
    if not p or not p.startswith("/"):
        return False
    if "\x00" in p or "\n" in p or "\r" in p:
        return False
    if len(p) > 4096:
        return False
    # Reject any `..` segment — defense in depth against HITL bypass.
    if any(seg == ".." for seg in p.split("/")):
        return False
    return True


def _resolve_workspace_path(rel: str) -> Path:
    """Resolve a workspace-relative path and assert it stays inside.

    `..`, absolute paths, and symlinks pointing outside the workspace
    are all rejected (HTTPException 400). Returns the absolute Path
    that the caller can stat / open.
    """
    root = workspace_root().resolve()
    # Strip leading slashes so a user pasting `/devices/...` still
    # resolves under the workspace root rather than the filesystem
    # root. Preserve everything else.
    rel = rel.lstrip("/")
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=f"path escapes workspace: {rel}"
        ) from exc
    return candidate


def _is_sensitive_remote(remote: str) -> bool:
    """Return True if `remote` lands in a path that warrants a confirm.

    /data/local/tmp/foo → False (scratch is fine)
    /data/anything-else → True
    /sdcard/...         → False

    `..` segments are normalized first so a permitted prefix like
    /data/local/tmp/../system can't bypass the HITL gate. Belt + suspenders
    with `_is_safe_remote_path` which already rejects `..` outright.
    """
    norm = posixpath.normpath(remote.rstrip("/") or "/")
    for ex in _SENSITIVE_REMOTE_EXEMPTIONS:
        if norm == ex or norm.startswith(ex + "/"):
            return False
    for prefix in _SENSITIVE_REMOTE_PREFIXES:
        if norm == prefix or norm.startswith(prefix + "/"):
            return True
    return False


# ─── ls -la parser ───────────────────────────────────────────────────
def _parse_ls_line(line: str) -> dict[str, Any] | None:
    """Parse one `ls -la` line.

    Format (Android toybox default + GNU coreutils both produce
    YYYY-MM-DD HH:MM by default for non-recent files):
        drwxr-xr-x  2 root root    4096 2026-04-30 12:34 dirname
        -rw-r--r--  1 root root  123456 2026-04-30 12:34 file with spaces
        lrwxrwxrwx  1 root root      11 2026-04-30 12:34 link -> /target

    Returns None for the `total N` header / blank lines.
    """
    line = line.rstrip("\n").rstrip("\r")
    if not line or line.startswith("total "):
        return None

    # Split into at most 8 parts so multi-word filenames stay intact.
    parts = line.split(None, 7)
    if len(parts) < 8:
        return None

    mode, _nlinks, owner, group, size_s, date, time_s, rest = parts
    # Symlink: split on " -> " so target is preserved separately.
    name: str
    target: str | None = None
    if mode.startswith("l") and " -> " in rest:
        name, target = rest.split(" -> ", 1)
    else:
        name = rest

    try:
        size = int(size_s)
    except ValueError:
        size = 0

    is_dir = mode.startswith("d")
    is_link = mode.startswith("l")
    return {
        "name": name,
        "is_dir": is_dir,
        "is_link": is_link,
        "link_target": target,
        "size": size,
        "mode": mode,
        "owner": owner,
        "group": group,
        "mtime": f"{date} {time_s}",
    }


# ─── Endpoints ──────────────────────────────────────────────────────
@router.get("/devices/{serial}/files")
async def device_files(
    serial: str,
    path: str = Query("/sdcard/", description="Absolute device path"),
) -> dict[str, Any]:
    """List a device directory. Always 200 with `ok=false` on errors."""
    if not _is_safe_remote_path(path):
        return {"ok": False, "serial": serial, "transport": None,
                "path": path, "entries": [], "error": "invalid path"}

    try:
        t = build_transport(device_serial=serial)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "serial": serial, "transport": None,
                "path": path, "entries": [],
                "error": f"{type(exc).__name__}: {exc}"}

    # Plain `ls -la` — Android toybox doesn't accept --time-style; default
    # output is already YYYY-MM-DD HH:MM which the parser handles.
    cmd = f"ls -la {shlex.quote(path)}"
    r = await t.shell(cmd, timeout=30)
    if not r.ok:
        return {
            "ok": False,
            "serial": serial,
            "transport": type(t).__name__,
            "path": path,
            "entries": [],
            "error": (r.stderr or "ls failed").strip(),
            "exit_code": r.exit_code,
        }

    # Collect everything, sort, then cap. Sorting before cap keeps
    # directories first regardless of toybox's inode-order output —
    # otherwise a 2000+ entry dir could ship with all dirs hidden
    # past the cutoff.
    entries: list[dict[str, Any]] = []
    for raw in r.stdout.splitlines():
        e = _parse_ls_line(raw)
        if e is None:
            continue
        if e["name"] in (".", ".."):
            continue
        entries.append(e)

    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
    truncated = len(entries) > _MAX_ENTRIES
    if truncated:
        entries = entries[:_MAX_ENTRIES]
    return {
        "ok": True,
        "serial": serial,
        "transport": type(t).__name__,
        "path": path,
        "entries": entries,
        "truncated": truncated,
    }


@router.get("/workspace/files")
async def workspace_files(
    path: str = Query("", description="Workspace-root-relative path"),
) -> dict[str, Any]:
    """List a workspace directory. Symlinks resolved against ws root."""
    try:
        target = _resolve_workspace_path(path)
    except HTTPException as exc:
        return {"ok": False, "path": path, "entries": [], "error": exc.detail}

    if not target.exists():
        return {"ok": False, "path": path, "entries": [],
                "error": "path does not exist"}
    if not target.is_dir():
        return {"ok": False, "path": path, "entries": [],
                "error": "path is not a directory"}

    # MID-3: use os.scandir so 50k-file directories don't pay 50k
    # extra stat() calls before truncation — DirEntry caches is_dir /
    # is_symlink from the readdir() syscall, so we sort + slice on
    # cheap metadata and only lstat the entries we keep.
    truncated = False
    try:
        with os.scandir(target) as it:
            scanned = list(it)
    except OSError as exc:
        return {"ok": False, "path": path, "entries": [],
                "error": f"{type(exc).__name__}: {exc}"}

    def _sort_key(e: os.DirEntry[str]) -> tuple[bool, str]:
        try:
            is_dir = e.is_dir(follow_symlinks=False)
        except OSError:
            is_dir = False
        return (not is_dir, e.name.lower())

    scanned.sort(key=_sort_key)
    if len(scanned) > _MAX_ENTRIES:
        truncated = True
        scanned = scanned[:_MAX_ENTRIES]

    entries: list[dict[str, Any]] = []
    for child in scanned:
        try:
            # lstat (not stat) so symlinks pointing OUT of workspace
            # don't leak target file size / mtime to the client.
            # security audit 2026-05-02 finding MID 3.
            stat = child.stat(follow_symlinks=False)
        except OSError:
            continue
        # Only follow symlinks for is_dir / is_file determination if
        # they resolve inside workspace_root — otherwise treat the
        # symlink itself as the entry (won't be opened anyway since
        # workspace_download has its own resolve+relative_to gate).
        try:
            is_link = child.is_symlink()
        except OSError:
            is_link = False
        try:
            is_dir = (not is_link) and child.is_dir()
            is_file = (not is_link) and child.is_file()
        except OSError:
            is_dir = False
            is_file = False
        entries.append({
            "name": child.name,
            "is_dir": is_dir,
            "is_link": is_link,
            "size": stat.st_size if is_file else 0,
            "mtime_epoch": stat.st_mtime,
        })

    root = workspace_root().resolve()
    rel_to_root = str(target.relative_to(root)) if target != root else ""
    return {
        "ok": True,
        "path": rel_to_root,
        "absolute_path": str(target),
        "entries": entries,
        "truncated": truncated,
    }


@router.post("/devices/{serial}/files/pull")
async def device_pull(
    serial: str,
    body: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """Pull a device path → workspace. `local` defaults under pulls/."""
    remote = body.get("remote")
    if not isinstance(remote, str) or not _is_safe_remote_path(remote):
        return {"ok": False, "serial": serial, "transport": None,
                "error": "invalid 'remote' path"}

    local_rel = body.get("local")
    local_abs: Path | None = None
    if isinstance(local_rel, str) and local_rel.strip():
        try:
            local_abs = _resolve_workspace_path(local_rel)
        except HTTPException as exc:
            return {"ok": False, "serial": serial, "transport": None,
                    "error": exc.detail}
        local_abs.parent.mkdir(parents=True, exist_ok=True)

    try:
        t = build_transport(device_serial=serial)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "serial": serial, "transport": None,
                "error": f"{type(exc).__name__}: {exc}"}

    # Per-serial lock + outer timeout (functional audit HIGH 5+6).
    try:
        async with _serial_lock(serial):
            r = await asyncio.wait_for(
                filesync_pull(t, remote, local_abs, device=serial),
                timeout=_FILE_OP_TIMEOUT_S,
            )
    except asyncio.TimeoutError:
        return {
            "ok": False, "serial": serial, "transport": type(t).__name__,
            "remote": remote,
            "error": f"pull timeout after {_FILE_OP_TIMEOUT_S}s — USB drop?",
        }
    if not r.ok:
        return {
            "ok": False,
            "serial": serial,
            "transport": type(t).__name__,
            "remote": remote,
            "error": r.error.message if r.error else "pull failed",
        }

    root = workspace_root().resolve()
    pulled = Path(r.data.local).resolve() if r.data else None
    pulled_rel = (
        str(pulled.relative_to(root)) if pulled and pulled.is_relative_to(root)
        else (str(pulled) if pulled else None)
    )
    return {
        "ok": True,
        "serial": serial,
        "transport": type(t).__name__,
        "remote": remote,
        "local": str(pulled) if pulled else None,
        "local_workspace_rel": pulled_rel,
        "duration_ms": r.data.duration_ms if r.data else 0,
    }


@router.post("/devices/{serial}/files/push")
async def device_push(
    serial: str,
    body: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """Push a workspace file → device. HITL gate on sensitive prefixes."""
    local_rel = body.get("local")
    remote = body.get("remote")
    force = bool(body.get("force"))

    if not isinstance(local_rel, str) or not local_rel.strip():
        return {"ok": False, "serial": serial, "transport": None,
                "error": "missing 'local'"}
    if not isinstance(remote, str) or not _is_safe_remote_path(remote):
        return {"ok": False, "serial": serial, "transport": None,
                "error": "invalid 'remote' path"}

    try:
        local_abs = _resolve_workspace_path(local_rel)
    except HTTPException as exc:
        return {"ok": False, "serial": serial, "transport": None,
                "error": exc.detail}
    if not local_abs.exists():
        return {"ok": False, "serial": serial, "transport": None,
                "error": f"local does not exist: {local_rel}"}

    if _is_sensitive_remote(remote) and not force:
        return {
            "ok": False,
            "serial": serial,
            "transport": None,
            "remote": remote,
            "requires_confirm": True,
            "error": (
                f"target prefix is sensitive ({remote}); resubmit with"
                " force=true after user confirmation"
            ),
        }

    try:
        t = build_transport(device_serial=serial)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "serial": serial, "transport": None,
                "error": f"{type(exc).__name__}: {exc}"}

    # Per-serial lock + outer timeout (functional audit HIGH 5+6).
    try:
        async with _serial_lock(serial):
            r = await asyncio.wait_for(
                filesync_push(t, local_abs, remote, verify=False),
                timeout=_FILE_OP_TIMEOUT_S,
            )
    except asyncio.TimeoutError:
        return {
            "ok": False, "serial": serial, "transport": type(t).__name__,
            "local": str(local_abs), "remote": remote,
            "error": f"push timeout after {_FILE_OP_TIMEOUT_S}s — USB drop?",
        }
    if not r.ok:
        return {
            "ok": False,
            "serial": serial,
            "transport": type(t).__name__,
            "local": str(local_abs),
            "remote": remote,
            "error": r.error.message if r.error else "push failed",
        }

    return {
        "ok": True,
        "serial": serial,
        "transport": type(t).__name__,
        "local": str(local_abs),
        "remote": remote,
        "bytes_transferred": r.data.bytes_transferred if r.data else 0,
        "duration_ms": r.data.duration_ms if r.data else 0,
    }


@router.get("/workspace/files/download/{path:path}")
async def workspace_download(path: str) -> FileResponse:
    """Stream a workspace file to the browser as a download."""
    target = _resolve_workspace_path(path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="not a workspace file")
    return FileResponse(
        path=str(target),
        filename=target.name,
        media_type="application/octet-stream",
    )


# Re-export so tests can patch deterministic timestamps if needed.
__all__ = ["router", "iso_timestamp"]
