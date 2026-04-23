"""Web API: Model Playground — raw chat with sampling controls.

Endpoints:

  GET  /playground/backends                 list registered backend ids
  GET  /playground/backends/{id}/models     installed models on that backend
  POST /playground/chat                     non-streaming chat
  WS   /playground/chat/ws                  streaming chat

The Playground bypasses AgentLoop — no tools, no auto-retry. It exists
so users can compare model parameters against each other under a single
UI without the agent layer's noise.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, ValidationError

from alb.agent.backend import BackendError, LLMBackend
from alb.agent.backends import get_backend
from alb.agent.playground import (
    PlaygroundParams,
    list_backend_models,
    playground_chat,
    playground_stream,
)
from alb.infra.registry import BACKENDS


def _backend_names() -> list[str]:
    return [b.name for b in BACKENDS]


def _backend_spec(name: str):  # noqa: ANN202 — internal helper
    for b in BACKENDS:
        if b.name == name:
            return b
    return None

router = APIRouter()


class PlaygroundMessage(BaseModel):
    role: str = Field("user", pattern="^(system|user|assistant|tool)$")
    content: str


class PlaygroundChatRequest(BaseModel):
    backend: str = "ollama"
    model: str | None = None
    base_url: str | None = None    # backend endpoint override
    messages: list[PlaygroundMessage]
    system: str | None = None

    # Sampling
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    repeat_penalty: float | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    seed: int | None = None
    stop: list[str] | None = None
    num_ctx: int | None = None
    num_predict: int | None = None

    # Behavior
    think: bool | None = None

    def to_params(self) -> PlaygroundParams:
        return PlaygroundParams(
            temperature=self.temperature,
            top_p=self.top_p,
            top_k=self.top_k,
            repeat_penalty=self.repeat_penalty,
            presence_penalty=self.presence_penalty,
            frequency_penalty=self.frequency_penalty,
            seed=self.seed,
            stop=self.stop,
            num_ctx=self.num_ctx,
            num_predict=self.num_predict,
            think=self.think,
        )

    def messages_dict(self) -> list[dict[str, str]]:
        return [{"role": m.role, "content": m.content} for m in self.messages]


# ─── Backend factory helper ────────────────────────────────────────


def _build_backend(req: PlaygroundChatRequest) -> LLMBackend:
    """Instantiate a backend the way `get_backend()` expects.

    Backend-specific kwargs (model, base_url) get passed through; unknown
    backends raise HTTPException so the client gets a clean 400.
    """
    if _backend_spec(req.backend) is None:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "UNKNOWN_BACKEND",
                "message": f"backend '{req.backend}' is not registered",
                "available": _backend_names(),
            },
        )

    kwargs: dict[str, Any] = {}
    if req.model:
        kwargs["model"] = req.model
    if req.base_url:
        kwargs["base_url"] = req.base_url
    try:
        return get_backend(req.backend, **kwargs)
    except (ValueError, ImportError) as e:
        raise HTTPException(
            status_code=501,
            detail={
                "code": "BACKEND_NOT_IMPLEMENTED",
                "message": str(e),
            },
        )


# ─── REST: backends + models ───────────────────────────────────────


@router.get("/playground/backends")
async def list_backends() -> dict[str, Any]:
    """Enumerate registered backends and their declared capabilities."""
    out: list[dict[str, Any]] = []
    for spec in BACKENDS:
        out.append({
            "name": spec.name,
            "status": spec.status,
            "runs_on_cpu": spec.runs_on_cpu,
            "supports_tool_calls": spec.supports_tool_calls,
            "requires": list(spec.requires),
            "description": spec.description,
        })
    return {"backends": out}


@router.get("/playground/backends/{backend}/models")
async def list_models(backend: str) -> dict[str, Any]:
    """Return the catalog the given backend exposes (e.g. Ollama /api/tags).

    Returns `{"models": []}` if the backend doesn't expose a catalog —
    the UI can then show a free-text model input.
    """
    if _backend_spec(backend) is None:
        raise HTTPException(status_code=404, detail=f"unknown backend '{backend}'")
    try:
        b = get_backend(backend)
    except (ValueError, ImportError) as e:
        raise HTTPException(
            status_code=501,
            detail={
                "code": "BACKEND_NOT_IMPLEMENTED",
                "message": str(e),
            },
        )
    try:
        models = await list_backend_models(b)
    except BackendError as e:
        raise HTTPException(
            status_code=502,
            detail={
                "code": e.code,
                "message": str(e),
                "suggestion": e.suggestion,
            },
        )
    return {"backend": backend, "models": models}


# ─── REST: non-streaming chat ──────────────────────────────────────


@router.post("/playground/chat")
async def playground_chat_endpoint(req: PlaygroundChatRequest) -> dict[str, Any]:
    backend = _build_backend(req)
    result = await playground_chat(
        backend,
        req.messages_dict(),
        params=req.to_params(),
        system=req.system,
    )
    return result.to_dict()


# ─── WS: streaming chat ────────────────────────────────────────────


@router.websocket("/playground/chat/ws")
async def playground_chat_ws(ws: WebSocket) -> None:
    """Streaming Playground chat.

    Protocol:
      C → S: PlaygroundChatRequest as JSON
      S → C: {"type":"token","delta":"..."}* then exactly one
             {"type":"done", ...} terminal event.
    Either side may close early.
    """
    await ws.accept()
    try:
        raw = await ws.receive_json()
    except WebSocketDisconnect:
        return

    try:
        req = PlaygroundChatRequest.model_validate(raw)
    except ValidationError as e:
        await ws.send_json({
            "type": "done",
            "ok": False,
            "content": "",
            "thinking": "",
            "finish_reason": "error",
            "model": "",
            "backend": "",
            "metrics": {},
            "error": {
                "code": "INVALID_REQUEST",
                "message": str(e),
                "suggestion": "see /docs for the request schema",
            },
        })
        with contextlib.suppress(Exception):
            await ws.close()
        return

    try:
        backend = _build_backend(req)
    except HTTPException as e:
        detail = e.detail if isinstance(e.detail, dict) else {"message": str(e.detail)}
        await ws.send_json({
            "type": "done",
            "ok": False,
            "content": "",
            "thinking": "",
            "finish_reason": "error",
            "model": req.model or "",
            "backend": req.backend,
            "metrics": {},
            "error": {
                "code": detail.get("code", "BACKEND_BUILD_FAILED"),
                "message": detail.get("message", "could not build backend"),
                "suggestion": "",
            },
        })
        with contextlib.suppress(Exception):
            await ws.close()
        return

    try:
        async for ev in playground_stream(
            backend,
            req.messages_dict(),
            params=req.to_params(),
            system=req.system,
        ):
            await ws.send_json(ev)
    except WebSocketDisconnect:
        return
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — catch-all so the WS always closes cleanly
        await ws.send_json({
            "type": "done",
            "ok": False,
            "content": "",
            "thinking": "",
            "finish_reason": "error",
            "model": req.model or "",
            "backend": req.backend,
            "metrics": {},
            "error": {
                "code": "PLAYGROUND_INTERNAL",
                "message": str(e),
                "suggestion": "",
            },
        })
    finally:
        with contextlib.suppress(Exception):
            await ws.close()
