"""Model Playground — raw chat with full sampling control.

Bypasses AgentLoop entirely. The Web UI's Playground panel needs:
  - One call per user message (no tool dispatch, no auto-retry)
  - Every Ollama / OpenAI sampling parameter exposed
  - Timing breakdown per response (tokens/s, eval/prompt durations)
  - Streaming token-by-token, with the metrics arriving on the final
    `done` event so the UI can update the metrics strip in one shot.

The CLI / REST / WS / MCP layers all wrap these two functions:
  - `playground_chat`     — non-streaming, returns PlaygroundResult
  - `playground_stream`   — async iterator yielding StreamEvent dicts
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from alb.agent.backend import (
    BackendError,
    LLMBackend,
    Message,
)


# ─── Parameter container ─────────────────────────────────────────────


@dataclass(frozen=True)
class PlaygroundParams:
    """All knobs the Playground UI exposes. None means "use backend default"."""

    # Sampling
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    repeat_penalty: float | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    seed: int | None = None
    stop: list[str] | None = None

    # Context
    num_ctx: int | None = None
    num_predict: int | None = None  # max output tokens; -1 = unlimited

    # Behavior
    think: bool | None = None  # ollama: emit thinking channel separately

    def clamped(self) -> "PlaygroundParams":
        """Return a copy with values clamped to safe ranges.

        We clamp instead of rejecting so a noisy Web UI doesn't 400 on
        every drift; the LLM behaves predictably with reasonable values.
        """
        def _clip(v: float | None, lo: float, hi: float) -> float | None:
            return None if v is None else max(lo, min(hi, v))

        def _clip_int(v: int | None, lo: int, hi: int) -> int | None:
            return None if v is None else max(lo, min(hi, v))

        return PlaygroundParams(
            temperature=_clip(self.temperature, 0.0, 2.0),
            top_p=_clip(self.top_p, 0.0, 1.0),
            top_k=_clip_int(self.top_k, 0, 1000),
            repeat_penalty=_clip(self.repeat_penalty, 0.0, 2.0),
            presence_penalty=_clip(self.presence_penalty, -2.0, 2.0),
            frequency_penalty=_clip(self.frequency_penalty, -2.0, 2.0),
            seed=self.seed,
            stop=list(self.stop) if self.stop else None,
            num_ctx=_clip_int(self.num_ctx, 0, 1_000_000),
            num_predict=self.num_predict,
            think=self.think,
        )

    def to_options(self) -> dict[str, Any]:
        """Pack into the `options` dict shape that backends accept."""
        opts: dict[str, Any] = {}
        if self.temperature is not None:
            opts["temperature"] = self.temperature
        if self.top_p is not None:
            opts["top_p"] = self.top_p
        if self.top_k is not None:
            opts["top_k"] = self.top_k
        if self.repeat_penalty is not None:
            opts["repeat_penalty"] = self.repeat_penalty
        if self.presence_penalty is not None:
            opts["presence_penalty"] = self.presence_penalty
        if self.frequency_penalty is not None:
            opts["frequency_penalty"] = self.frequency_penalty
        if self.seed is not None and self.seed != -1:
            opts["seed"] = self.seed
        if self.stop:
            opts["stop"] = list(self.stop)
        if self.num_ctx is not None:
            opts["num_ctx"] = self.num_ctx
        if self.num_predict is not None and self.num_predict != -1:
            opts["num_predict"] = self.num_predict
        return opts


# ─── Result + Metrics ───────────────────────────────────────────────


@dataclass(frozen=True)
class PlaygroundMetrics:
    input_tokens: int
    output_tokens: int
    total_tokens: int
    eval_duration_ms: int
    prompt_eval_duration_ms: int
    total_duration_ms: int
    load_duration_ms: int = 0

    @property
    def tokens_per_second(self) -> float:
        if self.eval_duration_ms <= 0:
            return 0.0
        return round(
            self.output_tokens / (self.eval_duration_ms / 1000.0), 1
        )

    def to_dict(self) -> dict[str, Any]:
        d = {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "eval_duration_ms": self.eval_duration_ms,
            "prompt_eval_duration_ms": self.prompt_eval_duration_ms,
            "total_duration_ms": self.total_duration_ms,
            "load_duration_ms": self.load_duration_ms,
            "tokens_per_second": self.tokens_per_second,
        }
        return d

    @classmethod
    def from_usage(cls, usage: dict[str, Any]) -> "PlaygroundMetrics":
        """Build from the dict an LLMBackend returns in ChatResponse.usage.

        Accepts both the legacy shape (input/output_tokens only) and the
        enriched shape (with *_duration_ms timing fields).
        """
        return cls(
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            total_tokens=int(usage.get("total_tokens") or 0),
            eval_duration_ms=int(usage.get("eval_duration_ms") or 0),
            prompt_eval_duration_ms=int(usage.get("prompt_eval_duration_ms") or 0),
            total_duration_ms=int(usage.get("total_duration_ms") or 0),
            load_duration_ms=int(usage.get("load_duration_ms") or 0),
        )


@dataclass(frozen=True)
class PlaygroundResult:
    ok: bool
    content: str
    thinking: str
    finish_reason: str
    model: str
    backend: str
    metrics: PlaygroundMetrics
    error: dict[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "content": self.content,
            "thinking": self.thinking,
            "finish_reason": self.finish_reason,
            "model": self.model,
            "backend": self.backend,
            "metrics": self.metrics.to_dict(),
            "error": self.error,
        }


# ─── Public API ─────────────────────────────────────────────────────


def _build_messages(
    messages: list[dict[str, str]] | list[Message],
    system: str | None,
) -> list[Message]:
    """Normalize input into a list[Message] with optional prepended system."""
    out: list[Message] = []
    if system:
        out.append(Message(role="system", content=system))
    for m in messages:
        if isinstance(m, Message):
            out.append(m)
        else:
            role = m.get("role", "user")
            content = m.get("content", "")
            # Don't add another system prompt if the caller passed one in
            # the conversation already AND we already inserted one above.
            if role == "system" and system and out and out[0].role == "system":
                continue
            out.append(Message(role=role, content=content))
    return out


async def playground_chat(
    backend: LLMBackend,
    messages: list[dict[str, str]] | list[Message],
    *,
    params: PlaygroundParams | None = None,
    system: str | None = None,
) -> PlaygroundResult:
    """One-shot non-streaming chat. All errors caught — returns ok=False."""
    p = (params or PlaygroundParams()).clamped()
    msgs = _build_messages(messages, system)
    extra: dict[str, Any] = {"options": p.to_options()}
    if p.think is not None:
        extra["think"] = p.think

    try:
        resp = await backend.chat(
            msgs,
            tools=None,  # raw mode: no tool injection
            temperature=(p.temperature if p.temperature is not None else 0.7),
            max_tokens=(p.num_predict if p.num_predict and p.num_predict > 0 else None),
            **extra,
        )
    except BackendError as e:
        return PlaygroundResult(
            ok=False,
            content="",
            thinking="",
            finish_reason="error",
            model=getattr(backend, "model", ""),
            backend=backend.name,
            metrics=PlaygroundMetrics(0, 0, 0, 0, 0, 0),
            error={
                "code": e.code,
                "message": str(e),
                "suggestion": e.suggestion,
            },
        )

    return PlaygroundResult(
        ok=True,
        content=resp.content,
        thinking=resp.thinking or "",
        finish_reason=str(resp.finish_reason or "stop"),
        model=resp.model or getattr(backend, "model", ""),
        backend=backend.name,
        metrics=PlaygroundMetrics.from_usage(resp.usage or {}),
    )


async def playground_stream(
    backend: LLMBackend,
    messages: list[dict[str, str]] | list[Message],
    *,
    params: PlaygroundParams | None = None,
    system: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Stream tokens, then a terminal `done` event with full metrics.

    Event shapes:
      {"type": "token",     "delta": "..."}
      {"type": "thinking",  "delta": "..."}
      {"type": "done",
       "ok":              true|false,
       "content":         "...",
       "thinking":        "...",
       "finish_reason":   "stop"|"length"|"error",
       "model":           "...",
       "backend":         "...",
       "metrics":         {...PlaygroundMetrics.to_dict()...},
       "error":           {code,message,suggestion} | null}
    """
    p = (params or PlaygroundParams()).clamped()
    msgs = _build_messages(messages, system)
    extra: dict[str, Any] = {"options": p.to_options()}
    if p.think is not None:
        extra["think"] = p.think

    if not getattr(backend, "supports_streaming", False):
        # Fall back to non-stream call, but emit the result as a single
        # done event so clients can use one code path.
        result = await playground_chat(
            backend, messages, params=params, system=system
        )
        yield _result_to_done_event(result)
        return

    try:
        async for ev in backend.stream(
            msgs,
            tools=None,
            temperature=(p.temperature if p.temperature is not None else 0.7),
            max_tokens=(p.num_predict if p.num_predict and p.num_predict > 0 else None),
            **extra,
        ):
            t = ev.get("type")
            if t == "token":
                yield {"type": "token", "delta": ev.get("delta", "")}
            elif t == "done":
                metrics = PlaygroundMetrics.from_usage(ev.get("usage") or {})
                yield {
                    "type": "done",
                    "ok": True,
                    "content": ev.get("content", ""),
                    "thinking": ev.get("thinking", ""),
                    "finish_reason": ev.get("finish_reason", "stop"),
                    "model": ev.get("model") or getattr(backend, "model", ""),
                    "backend": backend.name,
                    "metrics": metrics.to_dict(),
                    "error": None,
                }
                return
            else:
                # Pass through unknown events (e.g. thinking deltas if the
                # backend ever emits them) so future event types just work.
                yield ev
    except BackendError as e:
        yield {
            "type": "done",
            "ok": False,
            "content": "",
            "thinking": "",
            "finish_reason": "error",
            "model": getattr(backend, "model", ""),
            "backend": backend.name,
            "metrics": PlaygroundMetrics(0, 0, 0, 0, 0, 0).to_dict(),
            "error": {
                "code": e.code,
                "message": str(e),
                "suggestion": e.suggestion,
            },
        }


def _result_to_done_event(r: PlaygroundResult) -> dict[str, Any]:
    return {
        "type": "done",
        "ok": r.ok,
        "content": r.content,
        "thinking": r.thinking,
        "finish_reason": r.finish_reason,
        "model": r.model,
        "backend": r.backend,
        "metrics": r.metrics.to_dict(),
        "error": r.error,
    }


# ─── Backend discovery for /playground/backends/{x}/models ─────────


async def list_backend_models(backend: LLMBackend) -> list[dict[str, Any]]:
    """Optional: backends with a model catalog expose `list_models()`.

    Not all backends do (e.g. Anthropic has a fixed list). Return [] if
    unsupported so the UI can fall back to a free-text input.
    """
    fn = getattr(backend, "list_models", None)
    if fn is None:
        return []
    return await fn()
