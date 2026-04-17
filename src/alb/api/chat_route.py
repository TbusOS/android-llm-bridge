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

from fastapi import APIRouter
from pydantic import BaseModel, Field

from alb.agent.backends import get_backend
from alb.agent.loop import AgentLoop
from alb.agent.session import ChatSession
from alb.infra.prompt_builder import default_agent_prompt

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
        return ChatResponse(
            ok=False,
            session_id="",
            error={"code": "BACKEND_INIT_FAILED", "message": str(e)},
        )

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
            return ChatResponse(
                ok=False,
                session_id=req.session_id,
                error={
                    "code": "SESSION_NOT_FOUND",
                    "message": f"no session: {req.session_id}",
                    "suggestion": "omit session_id to start a new one",
                },
            )
    else:
        session = ChatSession.create(backend=llm.name, model=llm.model)

    loop = AgentLoop(
        backend=llm,
        tools=specs,
        tool_executor=executor,
        max_turns=req.max_turns,
        system_prompt=prompt.as_text(),
    )

    result = await loop.run(req.message, session=session)

    if not result.ok and result.error:
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

    return ChatResponse(
        ok=True,
        reply=result.data,
        session_id=session.session_id,
        artifacts=[str(p) for p in (result.artifacts or [])],
        timing_ms=result.timing_ms or 0,
        model=llm.model,
        backend=llm.name,
    )


async def _empty_executor(name: str, args: dict) -> dict:
    return {"ok": False, "error": {"code": "TOOL_CALL_FAILED", "message": "tools disabled"}}
