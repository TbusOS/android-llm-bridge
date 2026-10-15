"""Web API: /api/bench/* — the bench agent's op table (ADR-059).

    GET  /api/bench/ops
        The connected agent's op table: every op with its parameters (kind,
        choices, range), whether it needs a board in fastboot, changes the
        device, takes an image, needs the job lock, and what gets recorded.
        `reported` is False for an agent from before the table — its rows are
        then inferred from caps and every one is treated as changing the
        device.

    POST /api/bench/ops/{op}
        Body: {"params": {...}, "image": <workspace path, image ops only>}
        Run one op. NDJSON stream in the same format as /api/flash/*: progress
        lines, then {"ev": "done", ...}. `data` on the done line carries a
        structured answer — for `usb-list`, each device with the mode the hub
        read from it, plus a `boards` summary.

The `/api/flash/*` endpoints stay: they are the same path under the names
the CLI and the web already use. Nothing here takes a command line or a path
on the bench host — the agent builds the argv from its own table.

    POST /api/bench/agent-update
        Body: {"config_text": <optional agent.conf text>}
        Send this hub's clients/windows-agent folder to the bench agent, let it
        restart on it, and wait until it reconnects (ADR-060). Same NDJSON
        stream; the done line's `data.outcome` says `updated`, and a failed
        line's code says whether the bench host rolled back
        (AGENT_UPDATE_ROLLED_BACK) or never came back (AGENT_UPDATE_NO_RETURN).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from alb.api.flash_route import _resolve_image, _stream
from alb.api.schema import API_VERSION
from alb.remote.agent_update import local_build, update_agent
from alb.remote.flash import get_flash_service
from alb.remote.registry import get_agent_registry

router = APIRouter(prefix="/api/bench", tags=["bench"])


class AgentUpdateBody(BaseModel):
    config_text: str | None = Field(
        None, description="replace the bench agent's agent.conf with this text"
    )


class OpBody(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)
    image: str = Field("", description="workspace-relative path; image ops only")


@router.get("/ops")
async def bench_ops() -> dict[str, Any]:
    svc = get_flash_service()
    return {
        "v": API_VERSION,
        "ok": True,
        "agent": svc.agent_summary(),
        # The agent file this hub would send; differs from agent.build when
        # the bench runs an older copy (`alb bench agent-update`).
        "bundle_build": local_build(),
        "reported": svc.ops_reported(),
        "partitions": svc.partitions(),
        "ops": [spec.as_dict() for spec in sorted(svc.ops().values(), key=lambda s: s.name)],
    }


@router.post("/ops/{op}")
async def bench_run(op: str, body: OpBody) -> StreamingResponse:
    image = _resolve_image(body.image) if body.image else None
    svc = get_flash_service()
    return await _stream(
        lambda on_event: svc.run_op(op, body.params, image=image, on_event=on_event)
    )


@router.post("/agent-update")
async def bench_agent_update(body: AgentUpdateBody) -> StreamingResponse:
    svc = get_flash_service()
    return await _stream(
        lambda on_event: update_agent(
            svc, get_agent_registry(), config_text=body.config_text, on_event=on_event
        )
    )
