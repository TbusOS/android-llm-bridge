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
import contextlib
import json
import os
import posixpath
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from alb.api.schema import API_VERSION
from alb.capabilities.filesync import pull as filesync_pull
from alb.capabilities.filesync import push as filesync_push
from alb.infra.workspace import iso_timestamp, workspace_root
from alb.mcp.transport_factory import build_transport
from alb.transport.base import TransferEvent

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


# ─── MID-6 streaming push/pull WS endpoints ─────────────────────────
#
# Protocol (mirrors /uart/stream / /terminal/ws style):
#
#   C → S (first JSON, required, 2.0 s timeout):
#       push:  {"local": "<workspace-rel>", "remote": "<device-path>",
#               "force": false}
#       pull:  {"remote": "<device-path>", "local": "<workspace-rel>?"}
#
#   S → C (on accept, ready frame):
#       {"v": API_VERSION, "type": "ready", "serial": "...",
#        "direction": "push"|"pull", "local": "...", "remote": "..."}
#
#   S → C (during transfer, 0..N frames):
#       {"type": "progress", "percent": 50.0,
#        "bytes_transferred": 12345, "file": "/sdcard/foo"}
#
#   C → S (any time, optional):
#       {"type": "cancel"}  → server SIGTERMs adb subprocess; closes
#       with reason="cancelled"
#
#   S → C (terminal, exactly one):
#       {"type": "closed", "reason": "done"|"cancelled"|
#                          "init_failed"|"bad_config"|"sensitive_path"|
#                          "unsupported_transport"|"timeout"|"error",
#        "ok": bool, "bytes_transferred": int, "duration_ms": int,
#        "error": "..."}
#
# Single close-frame guarantee per L-026: the inner pump never sends
# the closed frame; it returns / sets `_TransferCloseState.reason`,
# and the outer finally is the only sender.

_TRANSFER_CONFIG_TIMEOUT_S = 2.0


@dataclass
class _TransferCloseState:
    """Outer-finally close-frame coordinator for the transfer WS pumps.
    Same pattern as _CloseState in uart_stream_route.py — keeps L-026
    "exactly one close frame" invariant when multiple tasks (the pump
    + the recv loop) can decide the connection should end."""

    reason: str = "done"
    ok: bool = False
    bytes_transferred: int = 0
    duration_ms: int = 0
    error: str | None = None
    cancelled: bool = False


@router.websocket("/devices/{serial}/files/push/stream")
async def device_push_stream(ws: WebSocket, serial: str) -> None:
    """Streaming push with progress + cancel (MID-6)."""
    await ws.accept()
    await _run_transfer_stream(ws, serial, direction="push")


@router.websocket("/devices/{serial}/files/pull/stream")
async def device_pull_stream(ws: WebSocket, serial: str) -> None:
    """Streaming pull with progress + cancel (MID-6)."""
    await ws.accept()
    await _run_transfer_stream(ws, serial, direction="pull")


async def _run_transfer_stream(
    ws: WebSocket, serial: str, *, direction: str
) -> None:
    """Common WS lifecycle for push and pull streaming.

    Returns nothing; the outer finally is the single source of close
    frames (L-026). All paths set `cs.reason` + `cs.error` and return;
    the finally block sends the unified `closed` JSON frame.
    """
    cs = _TransferCloseState()
    try:
        config = await _read_transfer_config(ws)
        if not isinstance(config, dict):
            cs.reason = "bad_config"
            cs.error = "missing or invalid first message (expected JSON)"
            return

        # Validate shared fields.
        remote = config.get("remote")
        if not isinstance(remote, str) or not _is_safe_remote_path(remote):
            cs.reason = "bad_config"
            cs.error = "invalid 'remote' path"
            return

        local_arg = config.get("local")

        # Resolve local path. push REQUIRES it; pull defaults under
        # workspace/devices/<serial>/pulls/.
        local_abs: Path | None = None
        if direction == "push":
            if not isinstance(local_arg, str) or not local_arg.strip():
                cs.reason = "bad_config"
                cs.error = "missing 'local'"
                return
            try:
                local_abs = _resolve_workspace_path(local_arg)
            except HTTPException as exc:
                cs.reason = "bad_config"
                cs.error = str(exc.detail)
                return
            if not local_abs.exists():
                cs.reason = "bad_config"
                cs.error = f"local does not exist: {local_arg}"
                return
            # HITL gate: sensitive prefixes (mirrors POST endpoint).
            if _is_sensitive_remote(remote) and not bool(config.get("force")):
                cs.reason = "sensitive_path"
                cs.error = (
                    f"target prefix is sensitive ({remote}); "
                    "reconnect with force=true after user confirmation"
                )
                return
        else:  # pull
            if isinstance(local_arg, str) and local_arg.strip():
                try:
                    local_abs = _resolve_workspace_path(local_arg)
                except HTTPException as exc:
                    cs.reason = "bad_config"
                    cs.error = str(exc.detail)
                    return
                local_abs.parent.mkdir(parents=True, exist_ok=True)
            else:
                root = workspace_root().resolve()
                base = remote.rstrip("/").rsplit("/", 1)[-1] or "pull"
                local_abs = (
                    root / "devices" / serial / "pulls"
                    / f"{base}-{iso_timestamp()}"
                )
                local_abs.parent.mkdir(parents=True, exist_ok=True)

        # Build transport + verify it supports streaming.
        try:
            t = build_transport(device_serial=serial)
        except Exception as exc:  # noqa: BLE001
            cs.reason = "init_failed"
            cs.error = f"{type(exc).__name__}: {exc}"
            return

        method = "push_stream" if direction == "push" else "pull_stream"
        if not hasattr(t, method):
            cs.reason = "unsupported_transport"
            cs.error = f"transport {type(t).__name__} has no {method}()"
            return

        await ws.send_json({
            "v": API_VERSION,
            "type": "ready",
            "serial": serial,
            "direction": direction,
            "local": str(local_abs) if local_abs else "",
            "remote": remote,
        })

        # Run the streaming transfer + a parallel recv loop that
        # listens for {type:"cancel"} from the client.
        async with _serial_lock(serial):
            await _pump_transfer(
                ws, t, direction, local_abs, remote, cs,
            )
    except WebSocketDisconnect:
        # Client tab closed mid-transfer — pump will have caught this
        # via the cancel race and torn down adb. Mark cancelled so the
        # final closed frame (if reachable) reflects truth, but don't
        # try to send it on a closed socket.
        cs.cancelled = True
        cs.reason = "cancelled"
        return
    finally:
        # Single close-frame on this WS, no matter which path got us here.
        with contextlib.suppress(Exception):
            await ws.send_json({
                "type": "closed",
                "reason": cs.reason,
                "ok": cs.ok,
                "bytes_transferred": cs.bytes_transferred,
                "duration_ms": cs.duration_ms,
                "error": cs.error,
            })
        with contextlib.suppress(Exception):
            await ws.close()


async def _pump_transfer(
    ws: WebSocket,
    transport: Any,
    direction: str,
    local_abs: Path | None,
    remote: str,
    cs: _TransferCloseState,
) -> None:
    """Drive transport.push_stream / pull_stream and forward events.

    Producer/consumer pattern:
      - producer task tails the streaming generator, pushes
        ("event", e) tuples onto a queue, finally pushes ("end", None)
      - recv task listens for client cancel control frame, pushes
        ("cancel", None) on cancel
      - main loop reads from queue with await; first kind tells us
        what to do
    Cancel → break → outer finally calls inner.aclose() → adb
    subprocess SIGTERM/SIGKILL via the streaming generator's finally.
    """
    if direction == "push":
        assert local_abs is not None
        gen = transport.push_stream(local_abs, remote)
    else:
        assert local_abs is not None
        gen = transport.pull_stream(remote, local_abs)

    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

    async def _producer() -> None:
        try:
            async for event in gen:
                await queue.put(("event", event))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — surface as terminal
            await queue.put(("error", e))
        finally:
            await queue.put(("end", None))

    async def _recv() -> None:
        # Catch broad: starlette/anyio raises EndOfStream (NOT
        # WebSocketDisconnect) on peer close; we want to treat all
        # of these as "client gone, stop pumping" without leaking
        # warnings.
        try:
            while True:
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    await queue.put(("cancel", None))
                    return
                text = msg.get("text")
                if not text:
                    continue
                try:
                    obj = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict) and obj.get("type") == "cancel":
                    cs.cancelled = True
                    await queue.put(("cancel", None))
                    return
        except (WebSocketDisconnect, asyncio.CancelledError):
            return
        except Exception:  # noqa: BLE001
            with contextlib.suppress(Exception):
                await queue.put(("cancel", None))
            return

    producer_task = asyncio.create_task(_producer())
    recv_task = asyncio.create_task(_recv())

    try:
        while True:
            kind, payload = await queue.get()
            if kind == "cancel":
                cs.reason = "cancelled"
                cs.ok = False
                cs.error = "cancelled by client"
                return
            if kind == "error":
                cs.reason = "error"
                cs.error = f"{type(payload).__name__}: {payload}"
                return
            if kind == "end":
                # Producer exhausted without a "done" event — shouldn't
                # happen with a well-behaved transport, but cover it.
                if cs.reason == "done" and cs.ok:
                    return
                cs.reason = "error"
                cs.error = cs.error or "transfer ended without a done event"
                return
            assert kind == "event"
            event: TransferEvent = payload
            if event.kind == "progress":
                with contextlib.suppress(Exception):
                    await ws.send_json({
                        "type": "progress",
                        "percent": event.percent,
                        "bytes_transferred": event.bytes_transferred,
                        "file": event.file,
                    })
                continue
            # event.kind == "done"
            cs.reason = "done"
            cs.ok = bool(event.ok)
            cs.bytes_transferred = int(event.bytes_transferred)
            cs.duration_ms = int(event.duration_ms)
            cs.error = event.error
            # Don't return yet — let the producer reach EOF normally
            # (next loop iter will get ("end", None)) so the inner
            # generator's finally runs without aclose racing.
            # But if it doesn't end soon, that's fine — the outer
            # finally calls aclose.
            return
    finally:
        # Producer's `async for event in gen` is iterating; cancel
        # propagates GeneratorExit into gen's current yield — the
        # inner generator's finally terminates the adb subprocess.
        if not producer_task.done():
            producer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await producer_task
        if not recv_task.done():
            recv_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await recv_task
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await gen.aclose()


async def _read_transfer_config(ws: WebSocket) -> dict[str, Any] | None:
    """Read the first JSON message; tolerate slow / missing input."""
    try:
        first = await asyncio.wait_for(
            ws.receive(), timeout=_TRANSFER_CONFIG_TIMEOUT_S,
        )
    except (asyncio.TimeoutError, WebSocketDisconnect):
        return None
    text = first.get("text") if isinstance(first, dict) else None
    if not text:
        return None
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


# Re-export so tests can patch deterministic timestamps if needed.
__all__ = ["router", "iso_timestamp"]
