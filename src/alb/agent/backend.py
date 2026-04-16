"""LLMBackend ABC — pluggable LLM driver (local CPU or remote API).

Same architectural role as `alb.transport.base.Transport`: a stable interface
so the rest of the stack doesn't care whether we're talking to Ollama (local
CPU), llama.cpp (embedded), OpenAI-compatible (vLLM / llamafile), or
Anthropic's Claude API.

Scope intentionally narrow — backends only need to handle:

    1. `chat(messages, tools)` — one-shot completion, optionally returning
       structured tool-call requests.
    2. `stream(...)`           — optional streaming variant for terminal/Web chat.
    3. `health()`              — for `alb status` / readiness checks.

Anything richer (function sandboxing, multi-agent spawn, memory compaction)
lives in `alb.agent.loop`, not here.

Status: SKELETON — interface is authoritative; no concrete backend shipped.
Concrete backends land in M2 (OllamaBackend / OpenAICompatBackend) and M3
(LlamaCppBackend / AnthropicBackend).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal

# ─── Message primitives ──────────────────────────────────────────────

Role = Literal["system", "user", "assistant", "tool"]

FinishReason = Literal["stop", "tool_calls", "length", "error"]


@dataclass(frozen=True)
class ToolCall:
    """A structured tool invocation request emitted by the model."""

    id: str
    name: str
    arguments: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}


@dataclass(frozen=True)
class Message:
    """One entry in the chat history.

    `tool_calls` is populated when role == "assistant" and the model asked to
    call tools.  `tool_call_id` + `name` + `content` populate the reply
    messages when role == "tool" (content = JSON-serialised tool result).
    """

    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            d["tool_calls"] = [tc.to_dict() for tc in self.tool_calls]
        if self.tool_call_id is not None:
            d["tool_call_id"] = self.tool_call_id
        if self.name is not None:
            d["name"] = self.name
        return d


@dataclass(frozen=True)
class ToolSpec:
    """JSON-schema description of a tool the model may call.

    Shape matches OpenAI / Anthropic / Ollama function-calling conventions,
    so backends can forward `parameters` verbatim.
    """

    name: str
    description: str
    parameters: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


@dataclass(frozen=True)
class ChatResponse:
    """Unified backend reply."""

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: FinishReason = "stop"
    usage: dict[str, int] = field(default_factory=dict)  # input_tokens / output_tokens / total
    model: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
            "finish_reason": self.finish_reason,
            "usage": self.usage,
            "model": self.model,
        }


# ─── Backend ABC ─────────────────────────────────────────────────────


class LLMBackend(ABC):
    """All concrete LLM backends implement this ABC.

    Contract:
      - Methods are async (chat may take seconds on CPU-only small models).
      - Errors should be raised as `BackendError` (see below); the caller
        (`AgentLoop`) translates to `Result`.  Raising is fine because the
        agent layer always wraps us — unlike capability code which must never
        raise.
      - `supports_tool_calls` is load-bearing: the agent loop skips tool
        schema injection for backends that don't support it (e.g. a tiny
        1B model without function-calling training).
    """

    name: str = "base"
    model: str = ""  # concrete model id, e.g. "qwen2.5:3b", "claude-opus-4-6"
    supports_tool_calls: bool = False
    supports_streaming: bool = False
    runs_on_cpu: bool = False  # True for llama.cpp / Ollama CPU builds

    @abstractmethod
    async def chat(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        """One-shot completion.  Returns tool calls if the model asked for any."""

    async def stream(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Streaming completion.  Default: NotImplementedError.

        Subclasses with `supports_streaming = True` override this.
        """
        raise NotImplementedError(f"{self.name} does not support streaming")
        yield ""  # pragma: no cover — makes this an async generator for typing

    async def health(self) -> dict[str, Any]:
        """Connectivity & model-loaded snapshot for `alb status`."""
        return {
            "backend": self.name,
            "model": self.model,
            "reachable": False,
            "note": "health() not implemented by concrete backend",
        }


class BackendError(RuntimeError):
    """Raised by concrete backends on unrecoverable errors.

    The agent loop translates this into a `Result(ok=False, error=...)`.
    Use `code` matching `docs/errors.md` (e.g. "BACKEND_UNREACHABLE",
    "BACKEND_TIMEOUT", "BACKEND_TOOL_SCHEMA_INVALID").
    """

    def __init__(self, code: str, message: str, *, suggestion: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.suggestion = suggestion
