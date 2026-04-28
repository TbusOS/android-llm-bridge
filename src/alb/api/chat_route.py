"""`POST /chat` — non-streaming JSON endpoint for the local AgentLoop.

The same `AgentLoop + OllamaBackend + MCP tool executor` stack that powers
`alb chat` (CLI) powers this HTTP route. Callers include the future Web UI
and third-party clients who prefer HTTP over stdio MCP.

Streaming (WebSocket `/chat/ws`) is M3 — this route is non-streaming only.

Request:
    {
      "message": "帮我看下设备连通性",
      "session_id": "20260417-abc..." | null,   // create new if null
      "backend":   "ollama" | ...,              // default "ollama"
      "model":     "gemma4:26b" | null,         // default: backend default / env var
      "ollama_url": "http://..." | null,        // default: env var or backend default
      "max_turns": 8,                           // default 8
      "tools":     true                         // false = plain chat, no MCP tools
    }

Response (success):
    {
      "ok":          true,
      "reply":       "...",
      "session_id":  "20260417-abc...",
      "artifacts":   ["/abs/path/..."],
      "timing_ms":   4312,
      "model":       "gemma4:26b",
      "backend":     "ollama"
    }

Response (error) — still HTTP 200 (follows our Result envelope):
    {
      "ok":    false,
      "error": {"code": "BACKEND_UNREACHABLE", "message": "...", "suggestion": "..."},
      "session_id": "..."
    }
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, ValidationError

from alb.agent.backends import get_backend
from alb.agent.loop import AgentLoop
from alb.agent.session import ChatSession
from alb.infra.event_bus import get_bus, make_event
from alb.infra.prompt_builder import default_agent_prompt

_SUMMARY_MAX = 120

router = APIRouter()


class ChatRequest(BaseModel):
    message: str = Field(..., description="User input for this turn.")
    session_id: str | None = Field(None, description="Resume session; None creates one.")
    backend: str = Field("ollama", description="LLM backend name.")
    model: str | None = Field(None, description="Model tag. Defaults to backend default.")
    ollama_url: str | None = Field(None, description="Override Ollama URL (Ollama backend only).")
    max_turns: int = Field(8, ge=1, le=32)
    tools: bool = Field(True, description="If False, plain chat with no MCP tools.")


class ChatResponse(BaseModel):
    ok: bool
    reply: str | None = None
    session_id: str
    artifacts: list[str] = Field(default_factory=list)
    timing_ms: int = 0
    model: str = ""
    backend: str = ""
    error: dict[str, Any] | None = None


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """Run one agent turn and return the reply + session metadata."""
    built = await _build_agent(req)
    if isinstance(built, dict):  # error payload
        return ChatResponse(
            ok=False,
            session_id=built.get("session_id", ""),
            error=built["error"],
        )
    loop, session, llm = built

    await _publish_chat_event(
        session.session_id, "user", _truncate(req.message)
    )

    result = await loop.run(req.message, session=session)

    if not result.ok and result.error:
        await _publish_chat_event(
            session.session_id,
            "error",
            _truncate(result.error.message),
            data={
                "code": result.error.code,
                "timing_ms": result.timing_ms or 0,
                "model": llm.model,
                "backend": llm.name,
            },
        )
        return ChatResponse(
            ok=False,
            session_id=session.session_id,
            timing_ms=result.timing_ms or 0,
            model=llm.model,
            backend=llm.name,
            error={
                "code": result.error.code,
                "message": result.error.message,
                "suggestion": result.error.suggestion,
            },
        )

    await _publish_chat_event(
        session.session_id,
        "done",
        f"agent done · {result.timing_ms or 0}ms",
        data={
            "timing_ms": result.timing_ms or 0,
            "model": llm.model,
            "backend": llm.name,
            "artifact_count": len(result.artifacts or []),
        },
    )

    return ChatResponse(
        ok=True,
        reply=result.data,
        session_id=session.session_id,
        artifacts=[str(p) for p in (result.artifacts or [])],
        timing_ms=result.timing_ms or 0,
        model=llm.model,
        backend=llm.name,
    )


@router.websocket("/chat/ws")
async def chat_ws(ws: WebSocket) -> None:
    """Streaming chat over WebSocket.

    Protocol:
        C → S: JSON body matching ChatRequest schema.
        S → C: A stream of StreamEvent dicts (see AgentLoop.run_stream
               docstring) terminated by a single {"type": "done", ...} event.
        S closes after 'done'. Either side may close early.
    """
    await ws.accept()
    try:
        raw = await ws.receive_json()
        try:
            req = ChatRequest.model_validate(raw)
        except ValidationError as e:
            await ws.send_json(
                {
                    "type": "done",
                    "ok": False,
                    "error": {"code": "INVALID_REQUEST", "message": str(e)},
                    "session_id": "",
                }
            )
            return

        built = await _build_agent(req)
        if isinstance(built, dict):  # error payload
            await ws.send_json(
                {
                    "type": "done",
                    "ok": False,
                    "error": built["error"],
                    "session_id": built.get("session_id", ""),
                }
            )
            return
        loop, session, llm = built

        await _publish_chat_event(
            session.session_id, "user", _truncate(req.message)
        )

        async for event in loop.run_stream(req.message, session=session):
            # Enrich terminal event with backend metadata for client convenience
            if event.get("type") == "done":
                event.setdefault("model", llm.model)
                event.setdefault("backend", llm.name)

            kind, summary = _summarize_stream_event(event)
            if kind:
                data: dict[str, Any] = {}
                if event.get("type") == "tool_call_start":
                    data = {"name": event.get("name"), "id": event.get("id")}
                elif event.get("type") == "tool_call_end":
                    data = {"name": event.get("name"), "id": event.get("id"),
                            "ok": (event.get("result") or {}).get("ok", True)}
                elif event.get("type") == "done":
                    data = {"timing_ms": event.get("timing_ms", 0),
                            "model": llm.model, "backend": llm.name,
                            "usage": event.get("usage") or {}}
                await _publish_chat_event(
                    session.session_id, kind, summary, data=data or None
                )

            await ws.send_json(event)
    except WebSocketDisconnect:
        return
    finally:
        try:
            await ws.close()
        except Exception:  # noqa: BLE001 — already disconnected is fine
            pass


async def _build_agent(
    req: ChatRequest,
) -> tuple[AgentLoop, ChatSession, Any] | dict[str, Any]:
    """Shared setup for POST /chat and WS /chat/ws.

    Returns (loop, session, llm) on success, or {"error": {...}, "session_id": "..."}
    on failure (same shape both callers expect to wrap).
    """
    # Env-var defaults (mirror CLI behaviour)
    model = req.model or os.environ.get("ALB_OLLAMA_MODEL")
    ollama_url = req.ollama_url or os.environ.get("ALB_OLLAMA_URL")

    backend_kwargs: dict[str, Any] = {}
    if model:
        backend_kwargs["model"] = model
    if ollama_url and req.backend == "ollama":
        backend_kwargs["base_url"] = ollama_url

    try:
        llm = get_backend(req.backend, **backend_kwargs)
    except (ValueError, ImportError) as e:
        return {"error": {"code": "BACKEND_INIT_FAILED", "message": str(e)}, "session_id": ""}

    specs: list = []
    executor = _empty_executor
    if req.tools:
        from alb.mcp.executor import make_agent_tools

        specs, executor = await make_agent_tools()

    prompt = default_agent_prompt(
        device_serial=None,
        transport_name="auto",
        workspace_root=Path.cwd() / "workspace",
        tool_count=len(specs),
    )

    if req.session_id:
        session = ChatSession.load(req.session_id)
        if not session.meta_file.exists():
            return {
                "error": {
                    "code": "SESSION_NOT_FOUND",
                    "message": f"no session: {req.session_id}",
                    "suggestion": "omit session_id to start a new one",
                },
                "session_id": req.session_id,
            }
    else:
        session = ChatSession.create(backend=llm.name, model=llm.model)

    loop = AgentLoop(
        backend=llm,
        tools=specs,
        tool_executor=executor,
        max_turns=req.max_turns,
        system_prompt=prompt.as_text(),
    )
    return loop, session, llm


async def _empty_executor(name: str, args: dict) -> dict:
    return {"ok": False, "error": {"code": "TOOL_CALL_FAILED", "message": "tools disabled"}}


def _truncate(s: str, n: int = _SUMMARY_MAX) -> str:
    s = (s or "").replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


async def _publish_chat_event(
    session_id: str,
    kind: str,
    summary: str,
    *,
    data: dict[str, Any] | None = None,
) -> None:
    """Best-effort publish to the audit bus. Failures are swallowed —
    a misbehaving bus must not break a chat turn."""
    try:
        await get_bus().publish(
            make_event(
                session_id=session_id,
                source="chat",
                kind=kind,
                summary=summary,
                data=data,
            )
        )
    except Exception:  # noqa: BLE001 — bus is best-effort
        pass


def _summarize_stream_event(event: dict[str, Any]) -> tuple[str, str]:
    """Map an AgentLoop stream event to (kind, summary) for the bus.

    Returns ("", "") for events the bus shouldn't see (notably `token`,
    which is too dense to broadcast)."""
    et = event.get("type")
    if et == "tool_call_start":
        return "tool_call_start", f"tool_call: {event.get('name', '?')}"
    if et == "tool_call_end":
        result = event.get("result") or {}
        ok = result.get("ok", True)
        suffix = "" if ok else " (err)"
        return "tool_call_end", f"tool_result: {event.get('name', '?')}{suffix}"
    if et == "done":
        if event.get("ok", True):
            return "done", f"agent done · {event.get('timing_ms', 0)}ms"
        err = (event.get("error") or {}).get("message", "agent error")
        return "error", _truncate(err)
    return "", ""
