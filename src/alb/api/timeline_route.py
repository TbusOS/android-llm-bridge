"""Web API: /api/timeline/* -- merged logcat + dmesg + UART captures.

Wraps :mod:`alb.capabilities.timeline` so the web UI can start a capture,
watch it fill, and read it back, the way ``alb timeline capture`` / ``show``
do on the command line.

Endpoints:

    POST /api/timeline/capture?device=<serial>
        Body: {"duration_s": 1..600, "sources": ["logcat","dmesg","uart"],
               "reboot": "" | "system" | "bootloader",
               "logcat_buffers": ["main","system","crash","events"]}
        NDJSON stream: `{"ev":"progress","elapsed_s":..,"lines":{...}}` about
        once a second, then one `{"ev":"done", ...envelope}`.
        With `reboot` set the capture starts first and the reboot is issued
        about a second later, so the console is attached before the board
        resets.

    GET  /api/timeline/captures?device=&limit=50
    GET  /api/timeline/captures/{name}/lines?device=&sources=&grep=&t0=&t1=&limit=&offset=
    GET  /api/timeline/captures/{name}/density?device=&bucket_s=1
    GET  /api/timeline/captures/{name}/boot?device=

The UART comes from the hub's own serial transport (the same one the uart
routes use), never from a second way of opening the port.

All filesystem work runs through ``asyncio.to_thread`` (L-033): a capture
file can be tens of megabytes.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from time import perf_counter
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, StringConstraints

from alb.api.schema import API_VERSION
from alb.capabilities import power as power_cap
from alb.capabilities.boot_stages import detect_boot
from alb.capabilities.timeline import (
    ALL_SOURCES,
    READ_SOURCES,
    capture_timeline,
    iter_timeline,
)
from alb.capabilities.timeline_view import density, line_view
from alb.infra.result import Result, envelope_dict, envelope_transport_init_error
from alb.infra.workspace import is_safe_device, iso_timestamp, workspace_root
from alb.remote.flash_timeline import JOB_DIR_RE
from alb.transport.factory import build_transport

router = APIRouter(prefix="/api/timeline", tags=["timeline"])

NDJSON = "application/x-ndjson"

# `capture-<iso timestamp>` plus an optional `-N` when two captures start in
# the same second. The only names this API creates and the only ones it reads.
_NAME_RE = re.compile(r"^capture-\d{4}-\d\d-\d\dT\d\d-\d\d-\d\d(?:-\d{1,3})?$")

# How long after the capture starts the reboot is issued: long enough for the
# three readers to be attached, short enough that the boot is not missed.
_REBOOT_DELAY_S = 1.0
_PROGRESS_EVERY_S = 1.0

# Detached tasks must be referenced or the loop may drop them mid-run.
_BACKGROUND: set[asyncio.Task[Any]] = set()


# ─── helpers ───────────────────────────────────────────────────────
def _check_device(device: str | None) -> None:
    if device and not is_safe_device(device):
        # No-echo on reject (L-051): never reflect the raw serial back.
        raise HTTPException(status_code=400, detail="invalid device serial")


def _timeline_dir(device: str | None) -> Path:
    root = workspace_root()
    return root / "devices" / device / "timeline" if device else root / "timeline"


def _flash_dir(device: str | None) -> Path:
    root = workspace_root()
    return root / "devices" / device / "flash" if device else root / "flash"


def _check_name(name: str) -> None:
    if not (_NAME_RE.match(name) or JOB_DIR_RE.match(name)):
        raise HTTPException(status_code=400, detail="invalid capture name")


def _capture_path(device: str | None, name: str) -> Path:
    """The capture directory, or 400 / 404. Symlinks are refused: nothing
    this API wrote is a link, so a link is somebody else's data.

    `capture-<ts>` is a timeline recording; any other `<kind>-<ts>` is a
    flash job, which records the same one-clock timeline.jsonl (ADR-056)."""
    _check_device(device)
    _check_name(name)
    base = _timeline_dir(device) if _NAME_RE.match(name) else _flash_dir(device)
    path = base / name
    if path.is_symlink() or not (path / "timeline.jsonl").is_file():
        raise HTTPException(status_code=404, detail="capture not found")
    return path


def _new_capture_dir(device: str | None) -> Path:
    """Create the next free `capture-<ts>[-N]` directory (never reuse one)."""
    base = _timeline_dir(device)
    base.mkdir(parents=True, exist_ok=True)
    stamp = f"capture-{iso_timestamp()}"
    for n in range(1, 1000):
        candidate = base / (stamp if n == 1 else f"{stamp}-{n}")
        try:
            candidate.mkdir(exist_ok=False)
        except FileExistsError:
            continue
        return candidate
    raise FileExistsError("999 captures already started in this second")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _started_at(path: Path) -> str | None:
    """ISO start time: the `start` meta record's wall clock, else the name."""
    try:
        with (path / "timeline.jsonl").open("r", encoding="utf-8") as f:
            first = json.loads(f.readline())
        wall = first.get("wall") if isinstance(first, dict) else None
        if isinstance(wall, str):
            return wall
    except (OSError, ValueError):
        pass
    m = re.match(r"capture-(\d{4}-\d\d-\d\dT)(\d\d)-(\d\d)-(\d\d)", path.name)
    if m:
        return f"{m.group(1)}{m.group(2)}:{m.group(3)}:{m.group(4)}+00:00"
    return None


def _dir_size(path: Path) -> int:
    total = 0
    try:
        for p in path.iterdir():
            try:
                if p.is_file() and not p.is_symlink():
                    total += p.stat().st_size
            except OSError:
                continue
    except OSError:
        return 0
    return total


def _records(path: Path) -> Iterator[dict[str, Any]]:
    return iter_timeline(path)


# Boot detection reads the whole file, and the list endpoint asks for it once
# per capture. A finished capture never changes, so remember the answer by
# (path, mtime, size); bounded so a long-running hub does not grow without end.
_BOOT_CACHE: dict[tuple[str, int, int], dict[str, Any]] = {}
_BOOT_CACHE_MAX = 512


def _boot_of(path: Path) -> dict[str, Any]:
    file = path / "timeline.jsonl"
    try:
        st = file.stat()
    except OSError:
        return {"found": False, "total_s": 0.0, "milestones": [], "stages": []}
    key = (str(file), st.st_mtime_ns, st.st_size)
    hit = _BOOT_CACHE.get(key)
    if hit is not None:
        return hit
    report = detect_boot(_records(path)).to_dict()
    if len(_BOOT_CACHE) >= _BOOT_CACHE_MAX:
        _BOOT_CACHE.clear()
    _BOOT_CACHE[key] = report
    return report


_STAMP_RE = re.compile(r"(\d{4}-\d\d-\d\dT\d\d-\d\d-\d\d)(?:-(\d{1,3}))?$")


def _sort_key(path: Path) -> tuple[str, int]:
    """(utc stamp, same-second index) from a capture or job directory name —
    the two kinds share the stamp format, so they interleave by start time."""
    m = _STAMP_RE.search(path.name)
    return (m.group(1), int(m.group(2) or 1)) if m else ("", 0)


def _capture_dirs(device: str | None, per_kind: int | None = None) -> list[tuple[str, Path]]:
    """(kind, directory) for timeline recordings and flash jobs, newest first.

    `per_kind` caps each kind on its own before they are merged: jobs are
    far more numerous (every `fastboot devices` is one; 137 jobs next to 7
    recordings on the bench, 2026-10-30), and one shared cap would push the
    recordings out of the list."""
    found: list[tuple[str, Path]] = []
    for kind, base, pattern in (
        ("capture", _timeline_dir(device), _NAME_RE),
        ("flash", _flash_dir(device), JOB_DIR_RE),
    ):
        try:
            mine = [
                (kind, p)
                for p in base.iterdir()
                if pattern.match(p.name)
                and p.is_dir()
                and not p.is_symlink()
                and (p / "timeline.jsonl").is_file()
            ]
        except OSError:
            continue
        mine.sort(key=lambda kp: _sort_key(kp[1]), reverse=True)
        found += mine[:per_kind] if per_kind is not None else mine
    return sorted(found, key=lambda kp: _sort_key(kp[1]), reverse=True)


# A flash job has no summary.json, so its line counts come from one pass over
# the file; cached like boot detection, by (path, mtime, size).
_COUNT_CACHE: dict[tuple[str, int, int], dict[str, Any]] = {}


def _flash_stats(path: Path) -> dict[str, Any]:
    file = path / "timeline.jsonl"
    try:
        st = file.stat()
    except OSError:
        return {"sources": {}, "last_t": 0.0}
    key = (str(file), st.st_mtime_ns, st.st_size)
    hit = _COUNT_CACHE.get(key)
    if hit is not None:
        return hit
    counts: dict[str, int] = {}
    last_t = 0.0
    for rec in iter_timeline(path):
        src = str(rec.get("src") or "")
        counts[src] = counts.get(src, 0) + 1
        last_t = max(last_t, float(rec.get("t", 0.0)))
    stats = {"sources": counts, "last_t": last_t}
    if len(_COUNT_CACHE) >= _BOOT_CACHE_MAX:
        _COUNT_CACHE.clear()
    _COUNT_CACHE[key] = stats
    return stats


def _flash_entry(path: Path) -> dict[str, Any]:
    job = _read_json(path / "job.json")
    header = _read_header(path)
    stats = _flash_stats(path)
    m = _STAMP_RE.search(path.name)
    took = job.get("duration_s")
    return {
        "name": path.name,
        "kind": "flash",
        "label": str(header.get("label") or path.name),
        # null = no verdict on disk: still running, or the hub died mid-job
        "ok": bool(job.get("ok")) if job else None,
        "started_at": _iso_from_stamp(m.group(1)) if m else None,
        "duration_s": round(float(took), 3) if isinstance(took, (int, float)) else round(stats["last_t"], 3),
        "sources": {s: int(stats["sources"].get(s, 0)) for s in READ_SOURCES},
        "size_bytes": _dir_size(path),
        "has_boot": bool(_boot_of(path)["found"]),
    }


def _read_header(path: Path) -> dict[str, Any]:
    """The `start` meta record a flash job opens its timeline with."""
    try:
        with (path / "timeline.jsonl").open("r", encoding="utf-8") as f:
            first = json.loads(f.readline())
    except (OSError, ValueError):
        return {}
    return first if isinstance(first, dict) else {}


def _iso_from_stamp(stamp: str) -> str:
    date, _, clock = stamp.partition("T")
    return f"{date}T{clock.replace('-', ':')}+00:00"


def _list_sync(device: str | None, limit: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    # up to `limit` of each kind: recordings are never crowded out by jobs
    for kind, path in _capture_dirs(device, per_kind=limit):
        if kind == "flash":
            out.append(_flash_entry(path))
            continue
        summary = _read_json(path / "summary.json")
        raw_lines = summary.get("lines")
        lines: dict[str, Any] = raw_lines if isinstance(raw_lines, dict) else {}
        out.append(
            {
                "name": path.name,
                "kind": "capture",
                "started_at": _started_at(path),
                "duration_s": round(int(summary.get("duration_captured_ms") or 0) / 1000, 3),
                "sources": {s: int(lines.get(s, 0) or 0) for s in ALL_SOURCES},
                "size_bytes": _dir_size(path),
                "has_boot": bool(_boot_of(path)["found"]),
            }
        )
    return out


def _lines_sync(
    path: Path,
    sources: set[str] | None,
    grep: str | None,
    t0: float | None,
    t1: float | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    out: list[dict[str, Any]] = []
    skipped = 0
    truncated = False
    for rec in iter_timeline(path, sources=sources, grep=grep):
        t = float(rec.get("t", 0.0))
        if t1 is not None and t > t1:
            break  # time order: nothing later can match
        if t0 is not None and t < t0:
            continue
        if skipped < offset:
            skipped += 1
            continue
        if len(out) >= limit:
            truncated = True
            break
        out.append(line_view(rec))
    return {"lines": out, "truncated": truncated}


def _boot_sync(path: Path, device: str | None) -> dict[str, Any]:
    current = dict(_boot_of(path))
    previous: dict[str, Any] | None = None
    if current["found"]:
        # a flash job that rebooted the board recorded a boot too
        older = [p for _, p in _capture_dirs(device) if _sort_key(p) < _sort_key(path)][:20]
        for p in older:
            rep = _boot_of(p)
            if rep["found"]:
                previous = {
                    "name": p.name,
                    "total_s": rep["total_s"],
                    "milestones": rep["milestones"],
                }
                break
    return {**current, "previous": previous}


def _parse_sources(raw: str | None) -> set[str] | None:
    if not raw:
        return None
    wanted = {s.strip() for s in raw.split(",") if s.strip()}
    if not wanted or not wanted <= set(READ_SOURCES):
        raise HTTPException(status_code=400, detail="invalid sources")
    return wanted


# ─── read endpoints ────────────────────────────────────────────────
@router.get("/captures")
async def list_captures(
    device: str | None = Query(None, max_length=128),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    _check_device(device)
    data = await asyncio.to_thread(_list_sync, device, limit)
    return {"ok": True, "data": data}


@router.get("/captures/{name}/lines")
async def capture_lines(
    name: str,
    device: str | None = Query(None, max_length=128),
    sources: str | None = Query(None, max_length=64),
    grep: str | None = Query(None, max_length=256),
    t0: float | None = Query(None),
    t1: float | None = Query(None),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    wanted = _parse_sources(sources)
    path = await asyncio.to_thread(_capture_path, device, name)
    data = await asyncio.to_thread(_lines_sync, path, wanted, grep or None, t0, t1, limit, offset)
    return {"ok": True, "data": data}


def _density_sync(path: Path, bucket_s: float) -> dict[str, Any]:
    return density(_records(path), bucket_s)


@router.get("/captures/{name}/density")
async def capture_density(
    name: str,
    device: str | None = Query(None, max_length=128),
    bucket_s: float = Query(1.0, ge=0.1, le=60.0),
) -> dict[str, Any]:
    path = await asyncio.to_thread(_capture_path, device, name)
    data = await asyncio.to_thread(_density_sync, path, bucket_s)
    return {"ok": True, "data": data}


@router.get("/captures/{name}/boot")
async def capture_boot(
    name: str,
    device: str | None = Query(None, max_length=128),
) -> dict[str, Any]:
    path = await asyncio.to_thread(_capture_path, device, name)
    data = await asyncio.to_thread(_boot_sync, path, device)
    return {"ok": True, "data": data}


# ─── capture ───────────────────────────────────────────────────────
_LogcatBuffer = Annotated[str, StringConstraints(pattern=r"^[a-z]{1,16}$")]


class CaptureBody(BaseModel):
    duration_s: float = Field(..., ge=1, le=600)
    sources: list[Literal["logcat", "dmesg", "uart"]] = Field(
        ["logcat", "dmesg", "uart"], min_length=1, max_length=3
    )
    reboot: Literal["", "system", "bootloader"] = ""
    logcat_buffers: list[_LogcatBuffer] = Field(["main", "system", "crash", "events"], max_length=8)


def _line(obj: dict[str, Any]) -> bytes:
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode()


class _Tail:
    """Counts the line records a running capture has flushed so far.

    The capture flushes `timeline.jsonl` per received chunk (its documented
    contract), so reading the file from the last offset is a live view that
    needs no hook inside the capture itself.
    """

    def __init__(self, file: Path) -> None:
        self._file = file
        self._offset = 0
        self._carry = b""
        self.lines: dict[str, int] = dict.fromkeys(ALL_SOURCES, 0)

    def poll(self) -> dict[str, int]:
        try:
            with self._file.open("rb") as f:
                f.seek(self._offset)
                chunk = f.read()
        except OSError:
            return dict(self.lines)
        self._offset += len(chunk)
        data = self._carry + chunk
        *complete, self._carry = data.split(b"\n")
        for raw in complete:
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            if isinstance(rec, dict) and "line" in rec and not rec.get("note"):
                src = rec.get("src")
                if src in self.lines:
                    self.lines[src] += 1
        return dict(self.lines)


def _missing_sources(notes: list[str], wanted: list[str]) -> set[str]:
    """Sources the capture could not read at the end, from its own notes.

    A source that went away and came back (`uart link up` after
    `unavailable`) counts as present."""
    missing: set[str] = set()
    for note in notes:
        for src in wanted:
            if note.startswith(f"[alb] {src} unavailable"):
                missing.add(src)
        if note == "[alb] uart link up":
            missing.discard("uart")
    return missing


def _done_envelope(
    name: str, wanted: list[str], result: Result[Any], reboot: dict[str, Any] | None
) -> dict[str, Any]:
    env = envelope_dict(result)
    if result.ok and result.data is not None:
        summary = result.data
        missing = _missing_sources(list(summary.notes), wanted)
        env["data"] = {
            "name": name,
            "duration_s": round(summary.duration_captured_ms / 1000, 3),
            "sources": {
                s: {"lines": int(summary.lines.get(s, 0)), "missing": s in missing}
                for s in wanted
            },
            "notes": list(summary.notes),
        }
    else:
        env["name"] = name
    if reboot is not None:
        env["reboot"] = reboot
    return env


async def _do_reboot(
    transport: Any, serial: Any, mode: str, started: float
) -> dict[str, Any]:
    """Reboot after a short delay. Never raises: a failed reboot is reported
    next to the capture, which keeps running either way."""
    await asyncio.sleep(_REBOOT_DELAY_S)
    at_s = round(perf_counter() - started, 3)
    try:
        r = await power_cap.reboot(
            transport,
            mode="normal" if mode == "system" else mode,
            wait_boot=False,
            serial_fallback=serial,
        )
    except Exception as e:
        return {
            "ok": False,
            "mode": mode,
            "at_s": at_s,
            "error": {"code": "REBOOT_FAILED", "message": f"{type(e).__name__}: {e}"},
        }
    return {"mode": mode, "at_s": at_s, **envelope_dict(r)}


@router.post("/capture")
async def post_capture(
    body: CaptureBody,
    device: str | None = Query(None, max_length=128),
) -> Any:
    _check_device(device)
    wanted: list[str] = [s for s in ALL_SOURCES if s in body.sources]

    # Transports are resolved before streaming so a failure is an ordinary
    # envelope, not a stream that opens and immediately says "done: failed".
    transport: Any = None
    try:
        transport = build_transport(device_serial=device)
    except Exception as e:
        if set(wanted) & {"logcat", "dmesg"} or body.reboot:
            return envelope_transport_init_error(e, device=device)
    serial: Any = None
    if "uart" in wanted:
        try:
            serial = build_transport(override="serial", device_serial=device)
        except Exception:
            serial = None  # capture_timeline notes it in the timeline

    directory = await asyncio.to_thread(_new_capture_dir, device)
    name = directory.name
    started = perf_counter()

    async def stream() -> AsyncIterator[bytes]:
        cap = asyncio.create_task(
            capture_timeline(
                body.duration_s,
                sources=wanted,
                transport=transport,
                serial=serial,
                device=device,
                output=directory,
                logcat_buffers=body.logcat_buffers if "logcat" in wanted else None,
            ),
            name=f"timeline-capture-{name}",
        )
        _BACKGROUND.add(cap)
        cap.add_done_callback(_BACKGROUND.discard)
        reboot_task: asyncio.Task[dict[str, Any]] | None = None
        if body.reboot and transport is not None:
            # The console being captured is the board being rebooted, so the
            # serial link is a fair fallback when adb has gone.
            reboot_task = asyncio.create_task(
                _do_reboot(transport, serial if "uart" in wanted else None, body.reboot, started),
                name=f"timeline-reboot-{name}",
            )
            _BACKGROUND.add(reboot_task)
            reboot_task.add_done_callback(_BACKGROUND.discard)
        tail = _Tail(directory / "timeline.jsonl")
        # A client that hangs up closes this generator; the capture is NOT
        # cancelled -- it is bounded by duration_s and its files are the
        # product, a half-written one helps nobody.
        while not cap.done():
            await asyncio.wait({cap}, timeout=_PROGRESS_EVERY_S)
            counts = await asyncio.to_thread(tail.poll)
            yield _line(
                {
                    "ev": "progress",
                    "elapsed_s": round(perf_counter() - started, 1),
                    "lines": counts,
                }
            )
        reboot: dict[str, Any] | None = None
        if reboot_task is not None:
            reboot = await reboot_task
        try:
            result = cap.result()
        except Exception as e:
            yield _line(
                {
                    "ev": "done",
                    "v": API_VERSION,
                    "ok": False,
                    "name": name,
                    "error": {"code": "TIMELINE_CAPTURE_FAILED", "message": f"{type(e).__name__}: {e}"},
                }
            )
            return
        yield _line({"ev": "done", "v": API_VERSION, **_done_envelope(name, wanted, result, reboot)})

    return StreamingResponse(stream(), media_type=NDJSON)

