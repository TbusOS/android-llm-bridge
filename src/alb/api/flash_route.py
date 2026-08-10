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
    POST /api/flash/reboot     Body: {"target": "" | "bootloader" | ...}
    POST /api/flash/flash      Body: {"partition": str, "image": <workspace path>}

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
from collections.abc import AsyncIterator, Callable, Coroutine
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from alb.api.schema import API_VERSION
from alb.remote.flash import FlashEvent, FlashResult, get_flash_service
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


JobRunner = Callable[[Callable[[FlashEvent], None]], Coroutine[Any, Any, FlashResult]]


async def _stream(run: JobRunner) -> StreamingResponse:
    """Run a job, emitting each progress event as it happens.

    The queue decouples the job from the client: a slow or vanished reader
    must not be able to stall a partition write that is already underway.
    """
    queue: asyncio.Queue[FlashEvent | None] = asyncio.Queue()

    def on_event(ev: FlashEvent) -> None:
        queue.put_nowait(ev)

    async def body() -> AsyncIterator[bytes]:
        task: asyncio.Task[FlashResult] = asyncio.create_task(run(on_event))
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
                result: FlashResult = task.result()
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


@router.post("/reboot")
async def flash_reboot(body: RebootBody) -> StreamingResponse:
    service = get_flash_service()
    return await _stream(lambda cb: service.reboot(body.target, on_event=cb))


@router.post("/flash")
async def flash_partition(body: FlashBody) -> StreamingResponse:
    image = _resolve_image(body.image)
    service = get_flash_service()
    return await _stream(lambda cb: service.flash(body.partition, image, on_event=cb))
