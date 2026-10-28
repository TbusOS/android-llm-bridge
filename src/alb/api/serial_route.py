"""Web API: /api/serial/* -- things done over the UART console.

    POST /api/serial/break-in?device=<adb serial, optional>
        Body: {"reboot": "" | "auto" | "adb" | "serial", "timeout_s": 5..600}
        Stop the board in its bootloader: watch the UART for the autoboot
        countdown and type a key into it. Wraps
        :func:`alb.capabilities.breakin.break_in`, the code behind
        `alb serial break-in`.

        `reboot` says how the board is restarted once the watch is up:
          ""        nobody does it -- the operator resets the board by hand
          "adb"     `adb reboot`
          "serial"  `reboot` typed on the console
          "auto"    adb when an adb device answers, otherwise serial
        The reader is opened BEFORE the reboot, so the 1-3 s countdown is
        never missed.

        NDJSON: `{"ev":"progress","phase":"ready","reboot":"adb"|"serial"|null}`
        once the reader is up (the moment a human may press reset), then one
        `{"ev":"done", ...envelope}`. `ok` is true only for "stopped at a
        prompt"; every other outcome (BREAKIN_BOOTED_PAST, ...) is
        `ok: false` with the last received lines in `error.details.tail`.

The serial link is the hub's own shared session (`build_transport(
override="serial")`, as the uart routes do); other readers keep working
while this runs.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from alb.api.schema import API_VERSION
from alb.capabilities.breakin import break_in
from alb.infra.result import Result, envelope_dict, envelope_transport_init_error
from alb.infra.workspace import is_safe_device, workspace_root
from alb.transport.factory import build_transport

router = APIRouter(prefix="/api/serial", tags=["serial"])

NDJSON = "application/x-ndjson"

# Detached tasks must be referenced or the loop may drop them mid-run.
_BACKGROUND: set[asyncio.Task[Any]] = set()


class BreakInBody(BaseModel):
    reboot: Literal["", "auto", "adb", "serial"] = ""
    timeout_s: float = Field(120.0, ge=5, le=600)


def _line(obj: dict[str, Any]) -> bytes:
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode()


async def _adb_answers(adb_t: Any) -> bool:
    """Does an adb device answer right now? A probe must never raise."""
    try:
        r = await adb_t.shell("true", timeout=5)
    except Exception:
        return False
    return bool(r.ok)


def _rel(p: Path | None) -> str | None:
    if p is None:
        return None
    try:
        return Path(p).resolve().relative_to(workspace_root().resolve()).as_posix()
    except (ValueError, OSError):
        return None


@router.post("/break-in")
async def post_break_in(
    body: BreakInBody,
    device: str | None = Query(None, max_length=128),
) -> Any:
    if device and not is_safe_device(device):
        # L-051: no-echo on reject.
        raise HTTPException(status_code=400, detail="invalid device serial")
    try:
        serial = build_transport(override="serial", device_serial=device)
    except Exception as e:
        return envelope_transport_init_error(e, device=device)

    adb_t: Any = None
    if body.reboot in ("adb", "auto"):
        try:
            adb_t = build_transport(override="adb", device_serial=device)
        except Exception as e:
            if body.reboot == "adb":
                return envelope_transport_init_error(e, device=device)
            adb_t = None  # auto: no adb transport means serial

    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def stream() -> AsyncIterator[bytes]:
        mode: str | None = body.reboot or None
        if body.reboot == "auto":
            mode = "adb" if adb_t is not None and await _adb_answers(adb_t) else "serial"

        def on_ready() -> None:
            queue.put_nowait({"ev": "progress", "phase": "ready", "reboot": mode})

        task: asyncio.Task[Result[Any]] = asyncio.create_task(
            break_in(
                serial,
                reboot=mode,
                reboot_transport=adb_t if mode == "adb" else None,
                timeout_s=body.timeout_s,
                device=device,
                on_ready=on_ready,
            )
        )
        _BACKGROUND.add(task)  # a vanished client must not drop a running attempt
        task.add_done_callback(_BACKGROUND.discard)
        while True:
            getter = asyncio.create_task(queue.get())
            done, _ = await asyncio.wait({getter, task}, return_when=asyncio.FIRST_COMPLETED)
            if getter in done:
                yield _line(getter.result())
                continue
            getter.cancel()
            while not queue.empty():
                yield _line(queue.get_nowait())
            break
        try:
            result = task.result()
        except Exception as e:
            yield _line(
                {
                    "ev": "done",
                    "v": API_VERSION,
                    "ok": False,
                    "reboot_mode": mode,
                    "error": {"code": "BREAKIN_FAILED", "message": f"{type(e).__name__}: {e}"},
                }
            )
            return
        env = envelope_dict(result)
        env["reboot_mode"] = mode
        env["log"] = _rel(result.artifacts[0]) if result.artifacts else None
        yield _line({"ev": "done", "v": API_VERSION, **env})

    return StreamingResponse(stream(), media_type=NDJSON)
