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

import asyncio
import contextlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, ValidationError

from alb.agent.backends import get_backend
from alb.agent.loop import AgentLoop, ToolExecutor
from alb.agent.session import ChatSession
from alb.infra.event_bus import get_bus, make_event
from alb.infra.metric_sampler import TokenSampler
from alb.infra.prompt_builder import default_agent_prompt
from alb.infra.workspace import InvalidSessionId

_SUMMARY_MAX = 120
# SEC-2: bound how long the chat WS waits for its first (request) frame.
_FIRST_FRAME_TIMEOUT_S = 30.0

router = APIRouter()


class ChatRequest(BaseModel):
    message: str = Field(..., description="User input for this turn.")
    session_id: str | None = Field(None, description="Resume session; None creates one.")
    backend: str = Field("ollama", description="LLM backend name.")
    model: str | None = Field(None, description="Model tag. Defaults to backend default.")
    ollama_url: str | None = Field(None, description="Override Ollama URL (Ollama backend only).")
    max_turns: int = Field(8, ge=1, le=32)
    tools: bool = Field(True, description="If False, plain chat with no MCP tools.")
    confirm_tools: bool = Field(
        False,
        description="WS only: ask the client before every tool call and run it only "
        "if approved. Off by default — board commands run without asking (user "
        "decision 2026-10-23); this is the opt-in for the model's own calls.",
    )


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
        # SEC-2: the first frame must be valid JSON and arrive promptly.
        # Without this a client that sends garbage raises JSONDecodeError
        # (uncaught), and one that connects but never sends holds the
        # socket open indefinitely.
        try:
            raw = await asyncio.wait_for(
                ws.receive_json(), timeout=_FIRST_FRAME_TIMEOUT_S
            )
        except (json.JSONDecodeError, asyncio.TimeoutError):
            await ws.send_json(
                {
                    "type": "done",
                    "ok": False,
                    "error": {
                        "code": "INVALID_REQUEST",
                        "message": "expected a JSON ChatRequest as the first frame",
                    },
                    "session_id": "",
                }
            )
            return
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
        confirms: _Confirmations | None = None
        if req.confirm_tools and req.tools:
            confirms = _Confirmations(ws)
            loop.tool_executor = confirms.wrap(loop.tool_executor)

        await _publish_chat_event(
            session.session_id, "user", _truncate(req.message)
        )

        # ADR-049: tag the sampler with the backend so the MetricStore can
        # group throughput by backend (dashboard be-card sparkline).
        sampler = TokenSampler(session_id=session.session_id, backend=llm.name)
        sampler.start()
        reader = asyncio.create_task(confirms.read_replies()) if confirms else None
        try:
            # Hook sampler directly to the backend's raw token events via
            # AgentLoop's on_raw_token callback, so we get accurate token
            # counts (1 per Ollama chunk) instead of guessing from delta
            # char length. ADR-021.
            stream_iter = loop.run_stream(
                req.message,
                session=session,
                on_raw_token=lambda n: sampler.observe(n),
            )
            async for event in stream_iter:
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
        finally:
            if reader is not None:
                reader.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await reader
            if confirms is not None:
                confirms.deny_all()
            await sampler.close()
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
        try:
            session = ChatSession.load(req.session_id)
        except InvalidSessionId as e:
            return {
                "error": {
                    "code": "INVALID_SESSION_ID",
                    "message": str(e),
                    "suggestion": "use [A-Za-z0-9][A-Za-z0-9_-]* (<= 128 chars)",
                },
                "session_id": req.session_id,
            }
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
        session = ChatSession.create(backend=llm.name, model=llm.model, source="web")

    loop = AgentLoop(
        backend=llm,
        tools=specs,
        tool_executor=executor,
        max_turns=req.max_turns,
        system_prompt=prompt.as_text(),
    )
    return loop, session, llm


# How long a tool call waits for the person to answer before it is refused.
_CONFIRM_TIMEOUT_S = 600.0


class _Confirmations:
    """Ask the client before each tool call (ChatRequest.confirm_tools).

    S → C  {"type": "tool_confirm_request", "id", "name", "arguments"}
    C → S  {"type": "tool_confirm", "id", "approve": true|false}

    A call that is declined, unanswered for _CONFIRM_TIMEOUT_S, or left
    pending when the socket goes away does not run; the model gets an error
    result it can read and explain."""

    def __init__(self, ws: WebSocket) -> None:
        self._ws = ws
        self._pending: dict[str, asyncio.Future[bool]] = {}

    def wrap(self, run: ToolExecutor) -> ToolExecutor:
        async def asking(name: str, args: dict[str, Any]) -> dict[str, Any]:
            cid = uuid.uuid4().hex[:12]
            fut: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
            self._pending[cid] = fut
            try:
                await self._ws.send_json(
                    {"type": "tool_confirm_request", "id": cid, "name": name, "arguments": args}
                )
                approved = await asyncio.wait_for(fut, timeout=_CONFIRM_TIMEOUT_S)
            except TimeoutError:
                return _declined(name, "TOOL_CONFIRM_TIMEOUT", "nobody answered in time")
            except Exception:  # the socket is gone: nothing runs
                return _declined(name, "TOOL_DECLINED", "the page went away before answering")
            finally:
                self._pending.pop(cid, None)
            if not approved:
                return _declined(name, "TOOL_DECLINED", "the user declined this call")
            return await run(name, args)

        return asking

    async def read_replies(self) -> None:
        try:
            while True:
                msg = await self._ws.receive_json()
                if isinstance(msg, dict) and msg.get("type") == "tool_confirm":
                    fut = self._pending.get(str(msg.get("id")))
                    if fut is not None and not fut.done():
                        fut.set_result(msg.get("approve") is True)
        finally:
            self.deny_all()

    def deny_all(self) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_result(False)


def _declined(name: str, code: str, why: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": f"{name} was not run: {why}",
            "suggestion": "tell the user what this call would do and ask how to go on",
        },
    }


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
