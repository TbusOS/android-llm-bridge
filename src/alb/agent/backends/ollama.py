"""OllamaBackend — HTTP client for local Ollama daemon.

API reference: https://github.com/ollama/ollama/blob/main/docs/api.md

Endpoint: POST {base_url}/api/chat
Body (simplified):
    {
        "model": "qwen2.5:3b",
        "messages": [{"role": "...", "content": "...", "tool_calls": [...]}],
        "tools":    [{"type": "function", "function": {name, description, parameters}}],
        "stream":   false,
        "options":  {"temperature": 0.2, "num_predict": 512}
    }

Response (non-streaming, done=true):
    {
        "model": "qwen2.5:3b",
        "message": {
            "role": "assistant",
            "content": "...",
            "tool_calls": [{"function": {"name": "...", "arguments": {...}}}]
        },
        "done": true,
        "done_reason": "stop" | "length" | "load",
        "prompt_eval_count": N,
        "eval_count":        N
    }

Tool-calling support landed in Ollama 0.4 (2024-11).  Older servers return
plain assistant text with no tool_calls key; we detect and treat as
finish_reason="stop".
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import httpx

from alb.agent.backend import (
    BackendError,
    ChatResponse,
    FinishReason,
    LLMBackend,
    Message,
    ToolCall,
    ToolSpec,
)

DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5:3b"
DEFAULT_TIMEOUT = 120.0  # CPU inference on 3B can legitimately take 60-90s
DEFAULT_THINK = False  # reasoning models (gpt-oss, qwen3-thinking) — skip chain-of-thought by default for agent/tool use


class OllamaBackend(LLMBackend):
    """Local Ollama HTTP backend.

    Args:
        model: Ollama model tag (e.g. "qwen2.5:3b", "llama3.2:3b").
        base_url: Ollama daemon URL; default http://localhost:11434.
        timeout: per-request timeout in seconds; CPU inference is slow.
        default_options: extra fields for the `options` object
            (see Ollama API ModelFile options).
        think: whether to enable the model's chain-of-thought / reasoning
            channel (Ollama 0.9+, for models like gpt-oss, qwen3-thinking).
            Default False — for tool-calling agents, reasoning traces just
            waste output tokens. Pass `think=True` for interactive chat where
            the user wants to see the model's reasoning. Ignored by
            non-thinking models.
    """

    name = "ollama"
    supports_tool_calls = True
    supports_streaming = False  # streaming lands in M3; chat() only for now
    runs_on_cpu = True

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        default_options: dict[str, Any] | None = None,
        think: bool = DEFAULT_THINK,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.default_options: dict[str, Any] = default_options or {}
        self.think = think
        # Testing hook: inject httpx.MockTransport to avoid real network.
        self._transport = transport

    # ── Public API ──────────────────────────────────────────────
    async def chat(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        body = self._build_body(messages, tools, temperature, max_tokens, kwargs)
        raw = await self._post("/api/chat", body)
        return self._parse_response(raw)

    async def health(self) -> dict[str, Any]:
        """Hit `/api/tags` to check connectivity + whether our model is present."""
        try:
            async with httpx.AsyncClient(
                timeout=5.0, transport=self._transport
            ) as client:
                r = await client.get(f"{self.base_url}/api/tags")
                r.raise_for_status()
                data = r.json()
        except httpx.HTTPError as e:
            return {
                "backend": self.name,
                "model": self.model,
                "base_url": self.base_url,
                "reachable": False,
                "error": str(e),
            }
        names = [m.get("name", "") for m in data.get("models", [])]
        return {
            "backend": self.name,
            "model": self.model,
            "base_url": self.base_url,
            "reachable": True,
            "model_present": any(n == self.model or n.startswith(f"{self.model}:") for n in names),
            "installed_models": names,
        }

    # ── Internal ─────────────────────────────────────────────────
    def _build_body(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None,
        temperature: float,
        max_tokens: int | None,
        extra: dict[str, Any],
    ) -> dict[str, Any]:
        options: dict[str, Any] = {"temperature": temperature}
        if max_tokens is not None:
            options["num_predict"] = max_tokens
        options.update(self.default_options)
        options.update(extra.get("options", {}))

        body: dict[str, Any] = {
            "model": self.model,
            "messages": [_message_to_ollama(m) for m in messages],
            "stream": False,
            "think": extra.get("think", self.think),
            "options": options,
        }
        if tools:
            body["tools"] = [_tool_to_ollama(t) for t in tools]
        return body

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, transport=self._transport
            ) as client:
                r = await client.post(url, json=body)
        except httpx.ConnectError as e:
            raise BackendError(
                "BACKEND_UNREACHABLE",
                f"ollama daemon not reachable at {self.base_url}: {e}",
                suggestion="start ollama (`ollama serve`) or set base_url",
            ) from e
        except httpx.TimeoutException as e:
            raise BackendError(
                "BACKEND_TIMEOUT",
                f"ollama request timed out after {self.timeout}s: {e}",
                suggestion="raise timeout or pick a smaller model",
            ) from e
        except httpx.HTTPError as e:
            raise BackendError(
                "BACKEND_HTTP_ERROR",
                f"ollama HTTP error: {e}",
                suggestion="check ollama server logs",
            ) from e

        if r.status_code >= 400:
            raise BackendError(
                "BACKEND_HTTP_ERROR",
                f"ollama returned {r.status_code}: {r.text[:500]}",
                suggestion="check model name and request body; see ollama logs",
            )
        return r.json()  # type: ignore[no-any-return]

    def _parse_response(self, raw: dict[str, Any]) -> ChatResponse:
        msg = raw.get("message") or {}
        content: str = msg.get("content", "") or ""
        thinking: str = msg.get("thinking", "") or ""
        # Some reasoning models (gpt-oss with Ollama 0.18) return the final
        # answer in `thinking` rather than `content` even when think=false.
        # Promote so AgentLoop sees a non-empty reply.
        if not content and thinking:
            content = thinking

        tool_calls: list[ToolCall] = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            name = fn.get("name") or ""
            args = fn.get("arguments") or {}
            if not name:
                continue
            # Ollama returns dict already; older / other servers may return str
            if isinstance(args, str):
                try:
                    import json

                    args = json.loads(args) if args.strip() else {}
                except json.JSONDecodeError:
                    args = {"__raw__": args}
            tool_calls.append(
                ToolCall(id=tc.get("id") or f"tc_{uuid4().hex[:8]}", name=name, arguments=args)
            )

        finish_reason: FinishReason = "tool_calls" if tool_calls else _classify_done(raw)

        usage = {
            "input_tokens": int(raw.get("prompt_eval_count") or 0),
            "output_tokens": int(raw.get("eval_count") or 0),
            "total_tokens": int(raw.get("prompt_eval_count") or 0) + int(raw.get("eval_count") or 0),
        }
        return ChatResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
            model=raw.get("model") or self.model,
            thinking=thinking,
        )


# ─── Wire-format helpers ─────────────────────────────────────────────


def _classify_done(raw: dict[str, Any]) -> FinishReason:
    reason = raw.get("done_reason") or ""
    if reason == "stop":
        return "stop"
    if reason == "length":
        return "length"
    if reason == "load":
        # "load" means model was just loaded; the response is still valid
        return "stop"
    return "stop"


def _message_to_ollama(m: Message) -> dict[str, Any]:
    """Translate our Message dataclass into Ollama's message dict shape."""
    d: dict[str, Any] = {"role": m.role, "content": m.content}
    if m.tool_calls:
        d["tool_calls"] = [
            {"function": {"name": tc.name, "arguments": tc.arguments}}
            for tc in m.tool_calls
        ]
    # role=="tool" carries name + tool_call_id; Ollama accepts both as additional fields
    if m.name is not None:
        d["name"] = m.name
    return d


def _tool_to_ollama(t: ToolSpec) -> dict[str, Any]:
    """Translate ToolSpec into Ollama's function-calling tool shape."""
    return {
        "type": "function",
        "function": {
            "name": t.name,
            "description": t.description,
            "parameters": t.parameters,
        },
    }
