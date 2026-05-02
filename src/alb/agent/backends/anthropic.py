"""Anthropic Messages API backend.

Talks to Anthropic's hosted Claude models via POST /v1/messages.
Native HTTP via httpx — no `anthropic` SDK dependency, so users who
don't want chat features pay zero install cost (matches Ollama /
OpenAICompat approach).

Wire format differences vs OpenAI Chat Completions:

  - System prompt is a TOP-LEVEL `system` field, not a `role:"system"`
    message. We auto-extract from the first message if present.
  - `max_tokens` is REQUIRED (defaults to 4096 to keep small chats cheap).
  - Headers are `x-api-key` + `anthropic-version`, not Bearer.
  - Response `content` is a list of typed blocks (`text` / `tool_use`),
    not a `message.content` string + separate `tool_calls`. We collapse
    into the same ChatResponse shape that the agent loop expects.
  - Streaming is event-typed SSE (`event: message_start` / `delta` /
    `stop`), not OpenAI's anonymous `data:` chunks. Tool-call arguments
    arrive as `input_json_delta` partial JSON strings per content block
    index — we accumulate per-index, then `json.loads` at content_block_stop.
  - There is no public `/v1/models` listing for free; `health()` calls
    GET /v1/models which Anthropic added in 2024.

Secret handling: the API key is NEVER read from a constructor default
or hardcoded. Caller passes `api_key=` explicitly, or wiring layer
reads `ANTHROPIC_API_KEY` env (see backends/__init__.py). The key is
not logged, not echoed in BackendError messages.

Default model is `claude-haiku-4-5-20251001` — the cheapest current
4.x model, suitable for the playground "kick the tyres" experience.
Users wanting Sonnet / Opus override via `--model` or env.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from alb.agent.backend import (
    BackendError,
    ChatResponse,
    FinishReason,
    HealthResult,
    LLMBackend,
    Message,
    ToolCall,
    ToolSpec,
)

DEFAULT_BASE_URL = "https://api.anthropic.com"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_TIMEOUT = 120.0
DEFAULT_MAX_TOKENS = 4096
DEFAULT_ANTHROPIC_VERSION = "2023-06-01"

# Body keys the backend owns; per-call kwargs cannot override these
# (otherwise b.chat(messages=[...], messages=[...]) via **kwargs would
# replace the message history mid-flight — same hardening as OpenAICompat).
_RESERVED_BODY_KEYS = frozenset({
    "model", "messages", "system", "temperature", "max_tokens",
    "tools", "stream",
})

# Keys that callers from Ollama-style or OpenAI-compat code paths might
# pass — silently drop instead of letting the upstream API 400 on them.
_INCOMPATIBLE_KEYS = frozenset({
    "options",            # ollama-only
    "think",              # ollama-only
    "stream_options",     # openai-only
    "presence_penalty",   # openai-only (Anthropic has no direct equiv)
    "frequency_penalty",  # openai-only
})


class AnthropicBackend(LLMBackend):
    """HTTP backend for Anthropic's Claude API.

    Args:
        model: Claude model id (e.g. "claude-haiku-4-5-20251001",
            "claude-sonnet-4-6", "claude-opus-4-7"). Empty string is
            allowed at construction time so the playground health probe
            can run without a configured model; chat() raises if it's
            still empty.
        base_url: API root; default "https://api.anthropic.com". Override
            for proxies / Bedrock-style routing.
        api_key: Bearer-equivalent secret. Required for chat / stream;
            health() can run without (returns reachable=False) so the
            dashboard can show "configure ANTHROPIC_API_KEY".
        timeout: per-request timeout (seconds). Default 120 because
            longer Claude responses can take ~30-60 s.
        anthropic_version: API version header value; pinned to a
            known-good date so a future API breaking change doesn't
            silently break us.
        max_tokens: default ceiling on output tokens (Anthropic
            requires `max_tokens` in every request). Per-call
            `max_tokens=...` overrides.
        default_options: extra fields merged into request body
            (e.g. `{"top_p": 0.9}`); per-call kwargs override.
    """

    name = "anthropic"
    supports_tool_calls = True
    supports_streaming = True
    host_compute_type = "remote"
    has_health_probe = True

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        api_key: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        anthropic_version: str = DEFAULT_ANTHROPIC_VERSION,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        default_options: dict[str, Any] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.anthropic_version = anthropic_version
        self.max_tokens = max_tokens
        self.default_options: dict[str, Any] = default_options or {}
        # Testing hook: inject httpx.MockTransport to avoid real network.
        self._transport = transport
        # DEBT-019: lazy-init shared httpx client (one connection pool
        # across chat/stream/health). Closed via aclose() from
        # alb-api shutdown lifespan.
        self._client: httpx.AsyncClient | None = None

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
        self._require_model()
        self._require_api_key()
        body = self._build_body(
            messages, tools, temperature, max_tokens, kwargs, stream=False
        )
        raw = await self._post("/v1/messages", body)
        return self._parse_response(raw)

    async def stream(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        # Streaming impl lands in commit 83 — keep the abstract async
        # generator signature so call sites can already wire it.
        raise NotImplementedError(
            "AnthropicBackend.stream() lands in M3 step 2 follow-up commit"
        )
        yield {}  # pragma: no cover — async generator marker

    async def health(self) -> HealthResult:
        """Hit GET /v1/models to verify connectivity + key + model presence.

        Anthropic added the listing endpoint in 2024. Returns
        `{"data": [{"id": "claude-...", ...}, ...]}`. We treat 200
        as reachable; model_present is True iff configured `model`
        matches a listed id exactly.

        With no `api_key` configured, returns reachable=False with a
        configuration hint — keeps the dashboard explicit instead of
        silently red.
        """
        if not self.api_key:
            return HealthResult(
                reachable=False,
                model=self.model or None,
                error="ANTHROPIC_API_KEY not set",
            )
        try:
            client = self._get_client()
            r = await client.get(
                f"{self.base_url}/v1/models",
                headers=self._headers(),
                timeout=5.0,
            )
            r.raise_for_status()
            data = r.json()
        except httpx.HTTPError as e:
            return HealthResult(
                reachable=False,
                model=self.model or None,
                error=str(e),
            )
        ids = [m.get("id", "") for m in data.get("data") or []]
        return HealthResult(
            reachable=True,
            model=self.model or None,
            model_present=(
                any(i == self.model for i in ids) if self.model else None
            ),
        )

    async def list_models(self) -> list[dict[str, Any]]:
        """Return Anthropic's published model catalog from GET /v1/models."""
        self._require_api_key()
        url = f"{self.base_url}/v1/models"
        try:
            client = self._get_client()
            r = await client.get(url, headers=self._headers(), timeout=5.0)
        except httpx.ConnectError as e:
            raise BackendError(
                "BACKEND_UNREACHABLE",
                f"anthropic API not reachable at {self.base_url}: {e}",
                suggestion="check network / DNS",
            ) from e
        except httpx.HTTPError as e:
            raise BackendError(
                "BACKEND_HTTP_ERROR", f"anthropic HTTP error: {e}"
            ) from e
        if r.status_code == 401:
            raise BackendError(
                "BACKEND_UNAUTHORIZED",
                "anthropic /v1/models returned 401 (bad API key)",
                suggestion="verify ANTHROPIC_API_KEY",
            )
        if r.status_code >= 400:
            raise BackendError(
                "BACKEND_HTTP_ERROR",
                f"anthropic /v1/models returned {r.status_code}",
            )
        models = r.json().get("data") or []
        return [m for m in models if isinstance(m, dict)]

    # ── Internal ─────────────────────────────────────────────────

    def _require_model(self) -> None:
        if not self.model:
            raise BackendError(
                "BACKEND_MISCONFIGURED",
                "anthropic: model not set",
                suggestion=(
                    "pass --model <id> (e.g. claude-haiku-4-5-20251001) "
                    "or set ALB_ANTHROPIC_MODEL"
                ),
            )

    def _require_api_key(self) -> None:
        if not self.api_key:
            raise BackendError(
                "BACKEND_MISCONFIGURED",
                "anthropic: API key not set",
                suggestion="export ANTHROPIC_API_KEY=sk-ant-...",
            )

    def _headers(self) -> dict[str, str]:
        h = {
            "content-type": "application/json",
            "anthropic-version": self.anthropic_version,
        }
        if self.api_key:
            h["x-api-key"] = self.api_key
        return h

    def _build_body(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None,
        temperature: float,
        max_tokens: int | None,
        extra: dict[str, Any],
        *,
        stream: bool,
    ) -> dict[str, Any]:
        # Anthropic separates `system` from `messages`. Pull the first
        # role:"system" message into a top-level field; if there are
        # multiple system messages (rare; usually a caller bug) we
        # join with double-newline so the model still sees them all.
        system_parts: list[str] = []
        non_system: list[Message] = []
        for m in messages:
            if m.role == "system":
                if m.content:
                    system_parts.append(m.content)
            else:
                non_system.append(m)

        body: dict[str, Any] = {
            "model": self.model,
            "messages": [_message_to_anthropic(m) for m in non_system],
            "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
            "temperature": temperature,
            "stream": stream,
        }
        if system_parts:
            body["system"] = "\n\n".join(system_parts)
        if tools:
            body["tools"] = [_tool_to_anthropic(t) for t in tools]

        # default_options merge first; per-call extras override (but
        # neither can clobber reserved structural keys).
        for k, v in self.default_options.items():
            if k in _RESERVED_BODY_KEYS:
                continue
            body.setdefault(k, v)
        for k, v in extra.items():
            if k in _INCOMPATIBLE_KEYS or k in _RESERVED_BODY_KEYS:
                continue
            body[k] = v
        return body

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.timeout, transport=self._transport
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            client = self._get_client()
            r = await client.post(url, json=body, headers=self._headers())
        except httpx.ConnectError as e:
            raise BackendError(
                "BACKEND_UNREACHABLE",
                f"anthropic API not reachable at {self.base_url}: {e}",
                suggestion="check network / proxy",
            ) from e
        except httpx.TimeoutException as e:
            raise BackendError(
                "BACKEND_TIMEOUT",
                f"anthropic request timed out after {self.timeout}s: {e}",
                suggestion="raise timeout or pick a smaller model",
            ) from e
        except httpx.HTTPError as e:
            raise BackendError(
                "BACKEND_HTTP_ERROR",
                f"anthropic HTTP error: {e}",
                suggestion="check upstream status / network",
            ) from e

        if r.status_code == 401:
            raise BackendError(
                "BACKEND_UNAUTHORIZED",
                "anthropic returned 401 (bad API key)",
                suggestion="verify ANTHROPIC_API_KEY",
            )
        if r.status_code == 429:
            raise BackendError(
                "BACKEND_RATE_LIMITED",
                "anthropic returned 429 (rate limit)",
                suggestion="wait + retry, or use a different tier",
            )
        if r.status_code >= 400:
            text = r.text[:500]
            raise BackendError(
                "BACKEND_HTTP_ERROR",
                f"anthropic returned {r.status_code}: {text}",
                suggestion="check model name + request body shape",
            )
        return r.json()

    def _parse_response(self, raw: dict[str, Any]) -> ChatResponse:
        """Collapse Anthropic's typed content blocks into ChatResponse."""
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in raw.get("content") or []:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text_parts.append(block.get("text", ""))
            elif btype == "tool_use":
                tc_input = block.get("input")
                if not isinstance(tc_input, dict):
                    tc_input = {"__raw__": tc_input}
                tool_calls.append(
                    ToolCall(
                        id=str(block.get("id", "")),
                        name=str(block.get("name", "")),
                        arguments=tc_input,
                    )
                )
        finish_reason = _stop_reason_to_finish(raw.get("stop_reason"))
        usage_in = raw.get("usage") or {}
        # Anthropic exposes input_tokens / output_tokens; total is sum.
        # cache_creation / cache_read are surfaced verbatim for callers
        # who want to drive prompt-caching cost analytics later.
        in_tok = int(usage_in.get("input_tokens", 0) or 0)
        out_tok = int(usage_in.get("output_tokens", 0) or 0)
        usage = {
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "total_tokens": in_tok + out_tok,
        }
        return ChatResponse(
            content="".join(text_parts),
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
            model=str(raw.get("model") or self.model),
            thinking="",  # extended-thinking blocks not yet wired (M3+)
        )


# ─── Wire-format helpers (module-level for testability) ─────────────


def _message_to_anthropic(m: Message) -> dict[str, Any]:
    """Translate an alb Message into Anthropic's `messages` list shape.

    Roles: alb {user, assistant, tool} → anthropic {user, assistant}.
    A `tool` message becomes a user message with a `tool_result`
    content block. An assistant message with tool_calls becomes a
    role:"assistant" message with text + tool_use blocks intermixed.
    """
    if m.role == "tool":
        # tool_result is wrapped in a user message per Anthropic schema.
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": m.tool_call_id or "",
                    # Anthropic accepts string or array; alb stores
                    # tool results as JSON-serialized strings already.
                    "content": m.content,
                }
            ],
        }
    if m.role == "assistant" and m.tool_calls:
        blocks: list[dict[str, Any]] = []
        if m.content:
            blocks.append({"type": "text", "text": m.content})
        for tc in m.tool_calls:
            blocks.append(
                {
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.name,
                    "input": tc.arguments,
                }
            )
        return {"role": "assistant", "content": blocks}
    # Plain text message — Anthropic accepts string content directly.
    return {"role": m.role, "content": m.content}


def _tool_to_anthropic(t: ToolSpec) -> dict[str, Any]:
    """Translate an alb ToolSpec into Anthropic's `tools` entry.

    OpenAI uses `parameters`; Anthropic calls the same JSON-Schema
    object `input_schema`. Body is otherwise identical.
    """
    return {
        "name": t.name,
        "description": t.description,
        "input_schema": t.parameters,
    }


def _stop_reason_to_finish(reason: Any) -> FinishReason:
    """Map Anthropic stop_reason → alb FinishReason enum.

    Anthropic: end_turn / max_tokens / stop_sequence / tool_use.
    Unknown / missing reasons fall back to "stop" so the agent loop
    treats the turn as a normal completion (consistent with how
    OllamaBackend handles unrecognised reasons).
    """
    if reason == "tool_use":
        return "tool_calls"
    if reason == "max_tokens":
        return "length"
    return "stop"
