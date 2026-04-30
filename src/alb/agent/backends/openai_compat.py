"""OpenAI-compatible HTTP backend.

Talks to any server implementing OpenAI's `/v1/chat/completions` +
`/v1/models` contracts: vLLM, llamafile, LM Studio, text-generation-
webui, llama.cpp's built-in server, plus most cloud providers
(DeepSeek, Together, OpenAI itself).

Same shape as `OllamaBackend` (chat / stream / health + tool calling
+ HealthResult / has_health_probe per ADR-024). Wire format differs
in three places:

  - Path: `/chat/completions` (vs Ollama's `/api/chat`)
  - Streaming: SSE `data: {json}\\n\\n` (vs Ollama's NDJSON)
  - Tool-call response: `function.arguments` is a JSON STRING, not a
    dict (we parse + fall back to `{"__raw__": ...}` on bad JSON to
    keep the agent loop running).

Construction defaults aim at the most common self-hosted setup
(vLLM / llamafile listen on 8080 by default). LM Studio users at
:1234 override `base_url`. Cloud users override both `base_url` and
`api_key`.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

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

DEFAULT_BASE_URL = "http://localhost:8080/v1"
DEFAULT_MODEL = ""  # OpenAI-compat is BYO-model; leave empty so chat() raises a clear error if user forgets
DEFAULT_TIMEOUT = 120.0  # CPU inference behind vLLM/llamafile is slow

# Body keys that callers must NOT override via per-call kwargs or
# default_options — they're structural to the request shape and we
# set them explicitly. Without this guard, `b.chat(..., messages=[])`
# could be replaced via **kwargs, or a default_options entry could
# silently swap the model.
_RESERVED_BODY_KEYS = frozenset({
    "model", "messages", "temperature", "max_tokens", "tools", "stream",
})

# Keys passed by Ollama-aware callers that are meaningless to
# openai-compat — drop them silently rather than surfacing wire-format
# errors from the upstream server.
_OLLAMA_ONLY_KEYS = frozenset({"options", "think"})


class OpenAICompatBackend(LLMBackend):
    """OpenAI-compatible HTTP backend.

    Args:
        model: Model id the server expects (e.g. "gpt-4o-mini",
            "Qwen/Qwen2.5-7B-Instruct", "default" for LM Studio).
            Empty string is allowed at construction time so the
            playground health probe can run; chat() raises if it's
            still empty.
        base_url: Server URL up to and including `/v1`; default
            `http://localhost:8080/v1` (vLLM / llamafile). LM Studio
            uses `http://localhost:1234/v1`.
        api_key: Bearer token; omit for self-hosted servers that
            don't require auth.
        timeout: per-request timeout (seconds).
        default_options: extra fields merged into request body
            (e.g. `{"top_p": 0.9}`); per-call kwargs override.
    """

    name = "openai-compat"
    supports_tool_calls = True
    supports_streaming = True
    runs_on_cpu = True
    has_health_probe = True

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        api_key: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        default_options: dict[str, Any] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.default_options: dict[str, Any] = default_options or {}
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
        self._require_model()
        body = self._build_body(
            messages, tools, temperature, max_tokens, kwargs, stream=False
        )
        raw = await self._post("/chat/completions", body)
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
        """Stream chat response as Server-Sent Events.

        OpenAI streaming framing: each event is one `data: {json}\\n\\n`
        line; the final event is `data: [DONE]\\n\\n`. Content arrives
        via `choices[0].delta.content`; tool-call arguments are
        fragmented across multiple deltas (per `tool_calls[i].function.
        arguments` partial strings) and must be accumulated by index.
        Usage is sent on the final non-DONE event when we set
        `stream_options.include_usage=true`.

        Scope notes (real OpenAI-compatible servers all stay inside
        this scope today; expand if a server proves us wrong):
          - Single-line `data:` only — multi-line concatenation per
            SSE spec is not implemented (vLLM / LM Studio / llamafile
            / OpenAI proper / Together / DeepSeek / OpenRouter all
            emit single-line)
          - `event:` / `id:` / `retry:` lines and `:` comments are
            silently ignored (some providers send `: keepalive` to
            avoid proxy timeouts; that's fine — we just drop it)
        """
        self._require_model()
        body = self._build_body(
            messages, tools, temperature, max_tokens, kwargs, stream=True
        )

        content_parts: list[str] = []
        # Per-index accumulators for streamed tool calls.
        tc_buffers: dict[int, dict[str, Any]] = {}
        finish_reason: FinishReason = "stop"
        usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        model_reply = self.model

        url = f"{self.base_url}/chat/completions"
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, transport=self._transport
            ) as client:
                async with client.stream(
                    "POST", url, json=body, headers=self._headers()
                ) as r:
                    if r.status_code >= 400:
                        text = (await r.aread()).decode(
                            "utf-8", errors="replace"
                        )[:500]
                        raise BackendError(
                            "BACKEND_HTTP_ERROR",
                            f"openai-compat stream returned {r.status_code}: {text}",
                            suggestion="check model name + auth header",
                        )
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if not payload or payload == "[DONE]":
                            continue
                        try:
                            chunk = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        choices = chunk.get("choices") or []
                        if choices:
                            delta = choices[0].get("delta") or {}
                            delta_c = delta.get("content") or ""
                            if delta_c:
                                content_parts.append(delta_c)
                                # OpenAI servers don't expose per-chunk
                                # token counts, so we report 1 per
                                # delta — same convention as Ollama.
                                # tps_sample is approximate.
                                yield {
                                    "type": "token",
                                    "delta": delta_c,
                                    "tokens": 1,
                                }
                            for tc_delta in delta.get("tool_calls") or []:
                                _accumulate_tool_call(tc_buffers, tc_delta)
                            fr = choices[0].get("finish_reason")
                            if fr:
                                finish_reason = _normalize_finish_reason(fr)
                        if chunk.get("model"):
                            model_reply = chunk["model"]
                        if chunk.get("usage"):
                            usage = _build_usage_dict(chunk["usage"])
        except httpx.ConnectError as e:
            raise BackendError(
                "BACKEND_UNREACHABLE",
                f"openai-compat server not reachable at {self.base_url}: {e}",
                suggestion="start the server or set --base-url",
            ) from e
        except httpx.TimeoutException as e:
            raise BackendError(
                "BACKEND_TIMEOUT",
                f"openai-compat stream timed out after {self.timeout}s: {e}",
                suggestion="raise timeout or pick a smaller model",
            ) from e
        except httpx.HTTPError as e:
            # Catches mid-stream RemoteProtocolError / ReadError / etc.
            # so a server that drops the connection halfway through a
            # response still surfaces as a structured BackendError
            # rather than a raw httpx exception leaking to AgentLoop.
            raise BackendError(
                "BACKEND_HTTP_ERROR",
                f"openai-compat stream interrupted: {e}",
                suggestion="check server logs for the partial response",
            ) from e

        tool_calls = _materialize_tool_calls(tc_buffers)
        if tool_calls:
            finish_reason = "tool_calls"
        content = "".join(content_parts)

        yield {
            "type": "done",
            "content": content,
            "tool_calls": [tc.to_dict() for tc in tool_calls],
            "finish_reason": finish_reason,
            "usage": usage,
            "model": model_reply,
            "thinking": "",
        }

    async def health(self) -> HealthResult:
        """Hit `/v1/models` to check connectivity + model presence.

        OpenAI servers return `{"data": [{"id": "...", ...}, ...]}`.
        We treat a 200 as reachable; model_present is True when the
        configured `model` matches one of the listed ids exactly.
        Cloud providers (OpenAI proper) sometimes don't list private
        fine-tunes — None is returned for model_present in that case
        (when self.model is empty).
        """
        try:
            async with httpx.AsyncClient(
                timeout=5.0, transport=self._transport
            ) as client:
                r = await client.get(
                    f"{self.base_url}/models", headers=self._headers()
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
        """Return the installed model catalog from `/v1/models`.

        Each entry passes through verbatim (id / object / created /
        owned_by); raises BackendError on connectivity / HTTP failure
        so the playground REST layer can surface a structured error.
        """
        url = f"{self.base_url}/models"
        try:
            async with httpx.AsyncClient(
                timeout=5.0, transport=self._transport
            ) as client:
                r = await client.get(url, headers=self._headers())
        except httpx.ConnectError as e:
            raise BackendError(
                "BACKEND_UNREACHABLE",
                f"openai-compat not reachable at {self.base_url}: {e}",
                suggestion="start the server (vLLM/LM Studio/llamafile)",
            ) from e
        except httpx.HTTPError as e:
            raise BackendError(
                "BACKEND_HTTP_ERROR", f"openai-compat HTTP error: {e}"
            ) from e
        if r.status_code >= 400:
            raise BackendError(
                "BACKEND_HTTP_ERROR",
                f"openai-compat /v1/models returned {r.status_code}",
            )
        models = r.json().get("data") or []
        return [m for m in models if isinstance(m, dict)]

    # ── Internal ─────────────────────────────────────────────────

    def _require_model(self) -> None:
        if not self.model:
            raise BackendError(
                "BACKEND_MISCONFIGURED",
                "openai-compat: model not set",
                suggestion=(
                    "pass --model <id> or set ALB_OPENAI_COMPAT_MODEL"
                ),
            )

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
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
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [_message_to_openai(m) for m in messages],
            "temperature": temperature,
            "stream": stream,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if tools:
            body["tools"] = [_tool_to_openai(t) for t in tools]
        # default_options merge first, per-call extras win — but we
        # never let a caller silently override the structural fields
        # we just set above. Otherwise `b.chat(..., messages=[...])`
        # via **kwargs would replace the message history mid-flight.
        for k, v in self.default_options.items():
            if k in _RESERVED_BODY_KEYS:
                continue
            body.setdefault(k, v)
        for k, v in extra.items():
            if k in _OLLAMA_ONLY_KEYS:
                # ollama-specific (`options` nested dict, `think`
                # toggle); ignore for openai-compat
                continue
            if k in _RESERVED_BODY_KEYS:
                continue
            body[k] = v
        if stream:
            # ask server to emit usage on the final event so we can
            # populate ChatResponse.usage from the stream
            body.setdefault("stream_options", {"include_usage": True})
        return body

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, transport=self._transport
            ) as client:
                r = await client.post(url, json=body, headers=self._headers())
        except httpx.ConnectError as e:
            raise BackendError(
                "BACKEND_UNREACHABLE",
                f"openai-compat server not reachable at {self.base_url}: {e}",
                suggestion="start the server or set --base-url",
            ) from e
        except httpx.TimeoutException as e:
            raise BackendError(
                "BACKEND_TIMEOUT",
                f"openai-compat request timed out after {self.timeout}s: {e}",
                suggestion="raise timeout or pick a smaller model",
            ) from e
        except httpx.HTTPError as e:
            raise BackendError(
                "BACKEND_HTTP_ERROR",
                f"openai-compat HTTP error: {e}",
                suggestion="check server logs",
            ) from e

        if r.status_code >= 400:
            raise BackendError(
                "BACKEND_HTTP_ERROR",
                f"openai-compat returned {r.status_code}: {r.text[:500]}",
                suggestion="check model name and request body",
            )
        return r.json()  # type: ignore[no-any-return]

    def _parse_response(self, raw: dict[str, Any]) -> ChatResponse:
        choices = raw.get("choices") or []
        if not choices:
            raise BackendError(
                "BACKEND_HTTP_ERROR",
                "openai-compat returned no choices",
                suggestion="check server logs and model output",
            )
        msg = choices[0].get("message") or {}
        content: str = msg.get("content") or ""

        tool_calls: list[ToolCall] = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            tc_name = fn.get("name") or ""
            args_raw = fn.get("arguments")
            if not tc_name:
                continue
            args: dict[str, Any]
            if isinstance(args_raw, dict):
                args = args_raw
            elif isinstance(args_raw, str):
                try:
                    args = json.loads(args_raw) if args_raw.strip() else {}
                except json.JSONDecodeError:
                    args = {"__raw__": args_raw}
            else:
                args = {}
            tool_calls.append(
                ToolCall(
                    id=tc.get("id") or f"tc_{uuid4().hex[:8]}",
                    name=tc_name,
                    arguments=args,
                )
            )

        finish_reason: FinishReason = "tool_calls" if tool_calls else (
            _normalize_finish_reason(choices[0].get("finish_reason"))
        )
        usage = _build_usage_dict(raw.get("usage") or {})
        return ChatResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
            model=raw.get("model") or self.model,
            thinking="",
        )


# ─── Wire-format helpers ─────────────────────────────────────────────


def _normalize_finish_reason(reason: str | None) -> FinishReason:
    if reason in {"stop", "length"}:
        return reason  # type: ignore[return-value]
    if reason == "tool_calls":
        return "tool_calls"
    if reason == "content_filter":
        # Map content filter to "error" so AgentLoop can surface it
        # consistently with other policy-related stops.
        return "error"
    return "stop"


def _message_to_openai(m: Message) -> dict[str, Any]:
    """Translate Message dataclass → OpenAI message dict.

    OpenAI tool-result messages carry `tool_call_id` (required) +
    `content` (the JSON-stringified result). Assistant messages with
    tool calls embed `function.arguments` as a JSON STRING — opposite
    of Ollama, which uses a dict. We serialise on the way out.
    """
    if m.role == "tool":
        return {
            "role": "tool",
            "tool_call_id": m.tool_call_id or "",
            "content": m.content,
        }
    d: dict[str, Any] = {"role": m.role, "content": m.content}
    if m.tool_calls:
        d["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    # default=str: best-effort serialise non-JSON-safe
                    # values (datetime / Decimal / dataclass instances)
                    # the caller may have stuffed into arguments. Falls
                    # back to repr-like strings rather than raising
                    # TypeError mid-chat — same forgiveness as
                    # OllamaBackend's parse path.
                    "arguments": json.dumps(tc.arguments, default=str),
                },
            }
            for tc in m.tool_calls
        ]
    if m.name is not None and m.role != "tool":
        d["name"] = m.name
    return d


def _tool_to_openai(t: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": t.name,
            "description": t.description,
            "parameters": t.parameters,
        },
    }


def _build_usage_dict(usage: dict[str, Any]) -> dict[str, Any]:
    """Normalise OpenAI's usage block into the ABC's contract.

    OpenAI uses `prompt_tokens` / `completion_tokens` / `total_tokens`;
    we re-key to `input_tokens` / `output_tokens` / `total_tokens` to
    match the rest of alb (Ollama's _build_usage_dict already does
    this). Duration fields aren't reported by OpenAI servers, so they
    stay 0 — MetricSampler relies on token deltas, not durations.
    """
    in_t = int(usage.get("prompt_tokens") or 0)
    out_t = int(usage.get("completion_tokens") or 0)
    total = int(usage.get("total_tokens") or (in_t + out_t))
    return {
        "input_tokens": in_t,
        "output_tokens": out_t,
        "total_tokens": total,
        "load_duration_ms": 0,
        "prompt_eval_duration_ms": 0,
        "eval_duration_ms": 0,
        "total_duration_ms": 0,
    }


def _accumulate_tool_call(
    buffers: dict[int, dict[str, Any]], delta: dict[str, Any]
) -> None:
    """Stream-side tool-call accumulator.

    OpenAI's SSE splits one tool-call across N deltas, distinguished
    by `index`. Each delta contributes some subset of {id, function.
    name, function.arguments-fragment}. We grow per-index buffers so
    that on stream end we can emit a list of complete ToolCall.
    """
    idx = int(delta.get("index", 0) or 0)
    buf = buffers.setdefault(idx, {"id": "", "name": "", "arguments": ""})
    if delta.get("id"):
        buf["id"] = delta["id"]
    fn = delta.get("function") or {}
    if fn.get("name"):
        buf["name"] = fn["name"]
    args_frag = fn.get("arguments")
    if args_frag:
        buf["arguments"] += args_frag


def _materialize_tool_calls(
    buffers: dict[int, dict[str, Any]],
) -> list[ToolCall]:
    out: list[ToolCall] = []
    for idx in sorted(buffers):
        buf = buffers[idx]
        if not buf.get("name"):
            continue
        args_str = buf.get("arguments") or ""
        try:
            args = json.loads(args_str) if args_str.strip() else {}
        except json.JSONDecodeError:
            args = {"__raw__": args_str}
        out.append(
            ToolCall(
                id=buf.get("id") or f"tc_{uuid4().hex[:8]}",
                name=buf["name"],
                arguments=args,
            )
        )
    return out
