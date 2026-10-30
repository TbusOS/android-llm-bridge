"""Web API: /api/flash/* — fastboot jobs on the connected agent (ADR-056).

Endpoints:

    GET  /api/flash/status
        Can this bench flash, and is a job already running? Answered from
        the agent's advertised capabilities, so it is instant rather than a
        timeout (§决定 7).

    POST /api/flash/devices
    POST /api/flash/getvar     Body: {"name": "" | "partition-size:cfg" | ...}
                               Passes the device's answer through untouched —
                               the verb is protocol level, the meaning of the
                               values is not (see FlashService.getvar).
    POST /api/flash/oem        Body: {"command": "at-lock-vboot" | "at-unlock-vboot"}
                               The agent owns the allowlist (oem commands
                               change the device; some are one-way).
    POST /api/flash/reboot     Body: {"target": "" | "bootloader" | ...}
    POST /api/flash/flash      Body: {"partition": str, "image": <workspace path>}
    POST /api/flash/plan       Body: {"manifest": <workspace path>, "skip": [...],
                                      "skip_actions": [...]}
                               Parse a flashing manifest into a checked plan and
                               return it with every problem found. Runs nothing.
    GET  /api/flash/jobs?limit=20
                               Recorded jobs, newest first, plus `running`
                               (the job in flight, or null). Read from the
                               job.json files, so a reloaded page sees the
                               outcome of a long flash it lost the stream of.
    GET  /api/flash/jobs/{id}  One record: the full job.json and the name of
                               its timeline file.
    POST /api/flash/plan/run   Same body + "fingerprint" (from /plan) and
                               "reboot_wait_s". Rebuilds the plan, refuses (409)
                               if it has problems or no longer matches the
                               fingerprint, otherwise runs every row under one
                               hold of the job lock (NDJSON stream).

Why the three job endpoints stream NDJSON instead of returning one JSON
object: a flash is the one operation in alb where the caller genuinely
cannot tell "slow" from "wedged" without progress, and where waiting
silently through a partition write is the worst possible UI. Each line is
one JSON object; every line but the last is `{"ev": "progress", ...}` and
the last is `{"ev": "done", ...}` carrying the verdict.

Why NDJSON and not a WebSocket: the CLI already has httpx and would
otherwise need a WebSocket client purely for this; the browser reads the
same stream with fetch + ReadableStream. One code path serves both.

The image is named by a WORKSPACE-RELATIVE path, never an absolute one —
the caller uploads through the existing `POST /workspace/files/upload` and
then refers to what landed. That keeps "which files may be flashed" a
property of the workspace rather than of whoever can reach this endpoint.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Callable, Coroutine
from pathlib import Path
from typing import Any, Protocol

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from alb.api.schema import API_VERSION
from alb.remote.flash import REBOOT_WAIT_S, FlashEvent, get_flash_service
from alb.remote.flash_plan import Plan, build_plan
from alb.remote.flash_timeline import JOB_DIR_RE
from alb.remote.protocol import JobEvent

router = APIRouter(prefix="/api/flash", tags=["flash"])

NDJSON = "application/x-ndjson"


class FlashBody(BaseModel):
    partition: str = Field(..., min_length=1, max_length=64)
    image: str = Field(..., min_length=1, description="workspace-relative path")


class RebootBody(BaseModel):
    target: str = Field("", max_length=32, description='"" = back to the system')


@router.get("/status")
async def flash_status() -> dict[str, Any]:
    return {"v": API_VERSION, "ok": True, **get_flash_service().status()}


def _resolve_image(rel: str) -> Path:
    """Map a workspace-relative path to a real file, or 400.

    Delegates to the files route's resolver so this endpoint inherits the
    same traversal hardening rather than growing a second, subtly different
    copy of it (L-035)."""
    from alb.api.files_route import _resolve_workspace_path

    path = _resolve_workspace_path(rel)
    if not path.is_file():
        raise HTTPException(status_code=400, detail=f"no such image in the workspace: {rel}")
    return path


class _Verdict(Protocol):
    def as_dict(self) -> dict[str, Any]: ...


JobRunner = Callable[[Callable[[FlashEvent], None]], Coroutine[Any, Any, _Verdict]]


async def _stream(run: JobRunner) -> StreamingResponse:
    """Run a job, emitting each progress event as it happens.

    The queue decouples the job from the client: a slow or vanished reader
    must not be able to stall a partition write that is already underway.
    """
    queue: asyncio.Queue[FlashEvent | None] = asyncio.Queue()

    def on_event(ev: FlashEvent) -> None:
        queue.put_nowait(ev)

    async def body() -> AsyncIterator[bytes]:
        task: asyncio.Task[_Verdict] = asyncio.create_task(run(on_event))
        try:
            while True:
                getter = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait({getter, task}, return_when=asyncio.FIRST_COMPLETED)
                if getter in done:
                    ev = getter.result()
                    if ev is not None:
                        yield _line(
                            {
                                "ev": JobEvent.PROGRESS.value,
                                "phase": ev.phase,
                                "done": ev.done,
                                "total": ev.total,
                                "text": ev.text,
                            }
                        )
                    continue
                getter.cancel()
                # The job finished; drain whatever progress landed in the
                # same tick so the last percent is not lost behind the verdict.
                while not queue.empty():
                    ev = queue.get_nowait()
                    if ev is not None:
                        yield _line(
                            {
                                "ev": JobEvent.PROGRESS.value,
                                "phase": ev.phase,
                                "done": ev.done,
                                "total": ev.total,
                                "text": ev.text,
                            }
                        )
                result = task.result()
                yield _line({"ev": JobEvent.DONE.value, **result.as_dict()})
                return
        finally:
            if not task.done():
                # The client hung up. Do NOT cancel the job — the device is
                # mid-write and abandoning it there is worse than finishing
                # into a stream nobody reads.
                pass

    return StreamingResponse(body(), media_type=NDJSON)


def _line(obj: dict[str, Any]) -> bytes:
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode()


@router.post("/devices")
async def flash_devices() -> StreamingResponse:
    service = get_flash_service()
    return await _stream(lambda cb: service.devices(on_event=cb))


class GetvarBody(BaseModel):
    # Empty = `getvar all`. The shape check lives on the AGENT (it owns the
    # argv), so this bound is only a sanity ceiling — no allowlist here,
    # because which variables exist is platform-specific and a hub-side list
    # would hide variables this device really has. That is exactly how the
    # hard-coded partition picker broke.
    name: str = Field("", max_length=64, description='"" = getvar all')


@router.post("/getvar")
async def flash_getvar(body: GetvarBody) -> StreamingResponse:
    service = get_flash_service()
    return await _stream(lambda cb: service.getvar(body.name, on_event=cb))


class OemBody(BaseModel):
    # Required, no default: an empty oem request is a mistake. The allowlist
    # lives on the AGENT, next to the argv it builds — this bound is only a
    # sanity ceiling, same reasoning as GetvarBody.
    command: str = Field(..., min_length=1, max_length=64, description="e.g. at-lock-vboot")


@router.post("/oem")
async def flash_oem(body: OemBody) -> StreamingResponse:
    service = get_flash_service()
    return await _stream(lambda cb: service.oem(body.command, on_event=cb))


@router.post("/reboot")
async def flash_reboot(body: RebootBody) -> StreamingResponse:
    service = get_flash_service()
    return await _stream(lambda cb: service.reboot(body.target, on_event=cb))


@router.post("/flash")
async def flash_partition(body: FlashBody) -> StreamingResponse:
    image = _resolve_image(body.image)
    service = get_flash_service()
    return await _stream(lambda cb: service.flash(body.partition, image, on_event=cb))


class PlanBody(BaseModel):
    manifest: str = Field(..., min_length=1, description="workspace-relative path")
    # Bounded only as a sanity ceiling; the plan itself rejects a skip that
    # matches no row, which is the check that actually protects anything.
    skip: list[str] = Field(default_factory=list, max_length=128)
    skip_actions: list[str] = Field(default_factory=list, max_length=8)


class PlanRunBody(PlanBody):
    # Required: running a plan means running the plan that was SHOWN. A
    # caller without a fingerprint has not looked, and would be confirming
    # nothing.
    fingerprint: str = Field(..., min_length=64, max_length=64)
    reboot_wait_s: float = Field(REBOOT_WAIT_S, ge=10.0, le=600.0)


async def _build(body: PlanBody) -> Plan:
    from alb.api.files_route import _resolve_workspace_path
    from alb.infra.workspace import workspace_root

    manifest = _resolve_workspace_path(body.manifest)
    if not manifest.is_file():
        raise HTTPException(
            status_code=400, detail=f"no such manifest in the workspace: {body.manifest}"
        )
    return await build_plan(
        manifest,
        name=body.manifest,
        root=workspace_root().resolve(),
        skip=body.skip,
        skip_actions=body.skip_actions,
        agent=get_flash_service().agent_view(),
    )


@router.post("/plan")
async def flash_plan(body: PlanBody) -> dict[str, Any]:
    plan = await _build(body)
    return {"v": API_VERSION, "ok": plan.ok, **plan.as_dict()}


@router.post("/plan/run")
async def flash_plan_run(body: PlanRunBody) -> StreamingResponse:
    plan = await _build(body)
    if not plan.ok:
        raise HTTPException(
            status_code=409,
            detail={"code": "FLASH_PLAN_REJECTED", "problems": plan.problems},
        )
    if plan.fingerprint != body.fingerprint:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "FLASH_PLAN_CHANGED",
                "problems": [
                    "the plan no longer matches the one that was shown — an image, the "
                    "manifest or the skips changed since; nothing was run"
                ],
            },
        )
    service = get_flash_service()
    return await _stream(
        lambda cb: service.run_plan(plan, reboot_wait_s=body.reboot_wait_s, on_event=cb)
    )


# ── job records (what a reloaded page reads after a long flash) ─────────
#
# `remote/flash_timeline` writes one directory per job under
# `workspace/flash/` (or `workspace/devices/<d>/flash/`): `timeline.jsonl`
# while it runs, `job.json` once it has a verdict. A page that was reloaded
# mid-flash lost the NDJSON stream; these two endpoints let it read the
# outcome from disk instead. Read-only.

_JOB_DIR_RE = JOB_DIR_RE
# `<device>~<dir>` for a job recorded under a device; a bare `<dir>` for the
# global record. `~` cannot occur in a device serial or a job directory name.
_JOB_ID_RE = re.compile(
    r"^(?:[A-Za-z0-9][A-Za-z0-9._:-]{0,63}~)?[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
)


def _job_roots() -> list[tuple[str | None, Path]]:
    from alb.infra.workspace import workspace_root

    root = workspace_root()
    roots: list[tuple[str | None, Path]] = [(None, root / "flash")]
    try:
        for d in sorted((root / "devices").iterdir()):
            if d.is_dir() and not d.is_symlink():
                roots.append((d.name, d / "flash"))
    except OSError:
        pass
    return roots


def _read_job_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _job_header(directory: Path) -> dict[str, Any]:
    """The `start` meta record of the job's timeline: label, op, wall clock."""
    try:
        with (directory / "timeline.jsonl").open("r", encoding="utf-8") as f:
            first = json.loads(f.readline())
    except (OSError, ValueError):
        return {}
    return first if isinstance(first, dict) else {}


def _iso_from_stamp(ts: str) -> str:
    # `2026-10-23T10-30-00` (UTC, filename-safe) -> ISO 8601.
    date, _, clock = ts.partition("T")
    return f"{date}T{clock.replace('-', ':')}+00:00"


def _row_counts(job: dict[str, Any] | None) -> dict[str, int]:
    counts = {"total": 0, "ok": 0, "failed": 0, "skipped": 0, "not_run": 0}
    if job is None:
        return counts
    rows = job.get("rows")
    if isinstance(rows, list):  # a plan: one entry per manifest row
        counts["total"] = len(rows)
        for r in rows:
            status = r.get("status") if isinstance(r, dict) else None
            key = "not_run" if status == "not-run" else status
            if key in counts:
                counts[key] += 1
        return counts
    # A single job counts as one row, so the page can show one shape.
    counts["total"] = 1
    counts["ok" if job.get("ok") else "failed"] = 1
    return counts


def _job_summary(device: str | None, directory: Path) -> dict[str, Any]:
    m = _JOB_DIR_RE.match(directory.name)
    job = _read_job_json(directory / "job.json")
    header = _job_header(directory)
    kind = m.group("kind") if m else directory.name
    return {
        "id": f"{device}~{directory.name}" if device else directory.name,
        "op": header.get("op") or kind.split("-", 1)[0],
        "label": header.get("label") or kind.replace("-", " "),
        "started_at": _iso_from_stamp(m.group("ts")) if m else None,
        # null = no job.json yet: still running, or the hub died mid-job.
        "ok": bool(job.get("ok")) if job is not None else None,
        "code": (job.get("code") or "") if job is not None else "",
        "failed_line": int(job.get("failed_line") or 0) if job is not None else 0,
        "rows": _row_counts(job),
    }


def _list_jobs_sync(limit: int) -> list[dict[str, Any]]:
    found: list[tuple[str, str, str | None, Path]] = []
    for device, base in _job_roots():
        try:
            entries = [p for p in base.iterdir() if p.is_dir() and not p.is_symlink()]
        except OSError:
            continue
        for p in entries:
            m = _JOB_DIR_RE.match(p.name)
            if m:
                found.append((m.group("ts"), p.name, device, p))
    found.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [_job_summary(device, p) for _, _, device, p in found[:limit]]


def _job_detail_sync(job_id: str) -> dict[str, Any] | None:
    from alb.infra.workspace import workspace_root

    device, sep, name = job_id.rpartition("~")
    root = workspace_root()
    base = root / "devices" / device / "flash" if sep else root / "flash"
    path = base / name
    if path.is_symlink() or not path.is_dir() or not _JOB_DIR_RE.match(name):
        return None
    timeline = path / "timeline.jsonl"
    rel: str | None = None
    if timeline.is_file():
        try:
            rel = timeline.resolve().relative_to(root.resolve()).as_posix()
        except (ValueError, OSError):
            rel = None
    return {
        **_job_summary(device or None, path),
        "job": _read_job_json(path / "job.json"),
        "timeline": "timeline.jsonl" if timeline.is_file() else None,
        "timeline_path": rel,
    }


@router.get("/jobs")
async def flash_jobs(limit: int = Query(20, ge=1, le=200)) -> dict[str, Any]:
    jobs = await asyncio.to_thread(_list_jobs_sync, limit)
    running = get_flash_service().status().get("job") or None
    return {"v": API_VERSION, "ok": True, "running": running, "jobs": jobs}


@router.get("/jobs/{job_id}")
async def flash_job(job_id: str) -> dict[str, Any]:
    if not _JOB_ID_RE.match(job_id):
        raise HTTPException(status_code=400, detail="invalid job id")
    detail = await asyncio.to_thread(_job_detail_sync, job_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="job not found")
    return {"v": API_VERSION, "ok": True, **detail}
