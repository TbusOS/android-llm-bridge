"""Agent layer — LLM orchestration on top of capabilities / MCP tools.

This package is an **architectural slot** reserved in M1 for M3 implementation.

Purpose:
    Let users chat with a device via `alb chat` (terminal) or the Web API's
    `/chat` endpoint, powered by a pluggable LLM backend — including **local
    small models on CPU-only servers** (Ollama / llama.cpp / OpenAI-compat /
    Anthropic).  The agent loop only does *tool routing* ("抓日志" → call
    `alb_logcat`); it does not analyse the logs itself — that responsibility
    stays with the user or with a larger model.

Design layers (see docs/agent.md for the full writeup):

    backend.py   — LLMBackend ABC + Message / ToolCall / ToolSpec / ChatResponse
    loop.py      — AgentLoop (ReAct-lite tool-calling iteration)
    session.py   — ChatSession (JSONL persistence under workspace/sessions/)

Status:
    SKELETON — ABC surface is stable so downstream code (CLI `alb chat`,
    FastAPI `/chat`) can be written against it, but no concrete backend is
    implemented yet.  Attempting to instantiate `AgentLoop` currently raises
    NotImplementedError.

See also:
    - docs/agent.md (full design)
    - docs/design-decisions.md ADR-016 (why local small models)
    - docs/project-plan.md M2/M3 (delivery phase)
"""

from __future__ import annotations

from alb.agent.backend import (
    ChatResponse,
    LLMBackend,
    Message,
    Role,
    ToolCall,
    ToolSpec,
)
from alb.agent.loop import AgentLoop
from alb.agent.session import ChatSession

__all__ = [
    "AgentLoop",
    "ChatResponse",
    "ChatSession",
    "LLMBackend",
    "Message",
    "Role",
    "ToolCall",
    "ToolSpec",
]
