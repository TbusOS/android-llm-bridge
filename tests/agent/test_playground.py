"""Tests for the Model Playground module + Ollama backend usage timings."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from alb.agent.backend import (
    BackendError,
    ChatResponse,
    LLMBackend,
    Message,
    ToolSpec,
)
from alb.agent.backends.ollama import _build_usage_dict
from alb.agent.playground import (
    PlaygroundMetrics,
    PlaygroundParams,
    PlaygroundResult,
    list_backend_models,
    playground_chat,
    playground_stream,
)


# ─── PlaygroundParams ────────────────────────────────────────────


def test_params_clamp_temperature() -> None:
    p = PlaygroundParams(temperature=5.0).clamped()
    assert p.temperature == 2.0
    p = PlaygroundParams(temperature=-1.0).clamped()
    assert p.temperature == 0.0


def test_params_clamp_top_p() -> None:
    assert PlaygroundParams(top_p=2.0).clamped().top_p == 1.0
    assert PlaygroundParams(top_p=-0.5).clamped().top_p == 0.0


def test_params_clamp_passthrough_none() -> None:
    p = PlaygroundParams().clamped()
    assert p.temperature is None
    assert p.top_p is None


def test_params_to_options_drops_none() -> None:
    p = PlaygroundParams(temperature=0.7, top_k=40)
    opts = p.to_options()
    assert opts == {"temperature": 0.7, "top_k": 40}


def test_params_to_options_includes_stop_and_ctx() -> None:
    p = PlaygroundParams(stop=["</s>"], num_ctx=8192, num_predict=512)
    opts = p.to_options()
    assert opts["stop"] == ["</s>"]
    assert opts["num_ctx"] == 8192
    assert opts["num_predict"] == 512


def test_params_seed_minus_one_omitted() -> None:
    # seed=-1 means "random" — don't pass it through to Ollama
    p = PlaygroundParams(seed=-1)
    assert "seed" not in p.to_options()


def test_params_num_predict_minus_one_omitted() -> None:
    # num_predict=-1 means "no cap" — don't pass it through
    p = PlaygroundParams(num_predict=-1)
    assert "num_predict" not in p.to_options()


# ─── PlaygroundMetrics ───────────────────────────────────────────


def test_metrics_tokens_per_second() -> None:
    m = PlaygroundMetrics(
        input_tokens=10, output_tokens=300, total_tokens=310,
        eval_duration_ms=2000, prompt_eval_duration_ms=200,
        total_duration_ms=2300,
    )
    assert m.tokens_per_second == 150.0


def test_metrics_zero_duration_safe() -> None:
    m = PlaygroundMetrics(0, 0, 0, 0, 0, 0)
    assert m.tokens_per_second == 0.0


def test_metrics_from_usage_legacy_shape() -> None:
    # Old usage dict that doesn't carry timing fields
    m = PlaygroundMetrics.from_usage({"input_tokens": 5, "output_tokens": 8, "total_tokens": 13})
    assert m.input_tokens == 5
    assert m.output_tokens == 8
    assert m.eval_duration_ms == 0


def test_metrics_from_usage_full_shape() -> None:
    m = PlaygroundMetrics.from_usage({
        "input_tokens": 10, "output_tokens": 100, "total_tokens": 110,
        "eval_duration_ms": 1000, "prompt_eval_duration_ms": 100,
        "total_duration_ms": 1100, "load_duration_ms": 50,
    })
    assert m.tokens_per_second == 100.0


def test_metrics_to_dict_includes_derived() -> None:
    m = PlaygroundMetrics(0, 60, 60, 1000, 100, 1100)
    d = m.to_dict()
    assert d["tokens_per_second"] == 60.0
    assert d["total_duration_ms"] == 1100


# ─── Ollama _build_usage_dict — timing extraction ────────────────


def test_build_usage_dict_full() -> None:
    raw = {
        "prompt_eval_count": 8, "eval_count": 100,
        "prompt_eval_duration": 200_000_000,    # 200 ms
        "eval_duration": 1_500_000_000,         # 1500 ms
        "total_duration": 1_750_000_000,         # 1750 ms
        "load_duration": 50_000_000,             # 50 ms
    }
    u = _build_usage_dict(raw)
    assert u["input_tokens"] == 8
    assert u["output_tokens"] == 100
    assert u["total_tokens"] == 108
    assert u["prompt_eval_duration_ms"] == 200
    assert u["eval_duration_ms"] == 1500
    assert u["total_duration_ms"] == 1750
    assert u["load_duration_ms"] == 50


def test_build_usage_dict_missing_durations() -> None:
    u = _build_usage_dict({"prompt_eval_count": 1, "eval_count": 2})
    assert u["eval_duration_ms"] == 0
    assert u["total_duration_ms"] == 0


# ─── playground_chat happy path with a fake backend ──────────────


class _FakeBackend(LLMBackend):
    name = "fake"
    model = "fake-model"
    supports_tool_calls = False
    supports_streaming = True

    def __init__(
        self,
        *,
        reply: str = "ok",
        usage: dict[str, Any] | None = None,
        raise_error: BackendError | None = None,
    ) -> None:
        self._reply = reply
        self._usage = usage or {
            "input_tokens": 5, "output_tokens": 10, "total_tokens": 15,
            "eval_duration_ms": 500, "prompt_eval_duration_ms": 100,
            "total_duration_ms": 600,
        }
        self._raise = raise_error
        self.last_messages: list[Message] = []
        self.last_kwargs: dict[str, Any] = {}

    async def chat(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        if self._raise:
            raise self._raise
        self.last_messages = list(messages)
        self.last_kwargs = {"temperature": temperature, "max_tokens": max_tokens, **kwargs}
        return ChatResponse(
            content=self._reply, finish_reason="stop", model=self.model,
            usage=self._usage, thinking="",
        )

    async def stream(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        **kwargs: Any,
    ):
        if self._raise:
            raise self._raise
        self.last_messages = list(messages)
        self.last_kwargs = {"temperature": temperature, "max_tokens": max_tokens, **kwargs}
        for tok in ("hel", "lo"):
            yield {"type": "token", "delta": tok}
        yield {
            "type": "done",
            "content": "hello",
            "thinking": "",
            "finish_reason": "stop",
            "model": self.model,
            "usage": self._usage,
        }


@pytest.mark.asyncio
async def test_playground_chat_happy_path() -> None:
    b = _FakeBackend(reply="hello world")
    r = await playground_chat(
        b, [{"role": "user", "content": "hi"}],
        params=PlaygroundParams(temperature=0.5, top_p=0.9),
    )
    assert r.ok
    assert r.content == "hello world"
    assert r.metrics.output_tokens == 10
    assert r.metrics.tokens_per_second == 20.0  # 10 / 0.5s
    assert b.last_messages[0].role == "user"
    # Sampling kwargs reach backend via options dict
    assert b.last_kwargs["temperature"] == 0.5
    assert "options" in b.last_kwargs
    assert b.last_kwargs["options"]["top_p"] == 0.9


@pytest.mark.asyncio
async def test_playground_chat_with_system_prompt() -> None:
    b = _FakeBackend()
    await playground_chat(
        b, [{"role": "user", "content": "hi"}],
        system="You are concise.",
    )
    roles = [m.role for m in b.last_messages]
    assert roles == ["system", "user"]
    assert b.last_messages[0].content == "You are concise."


@pytest.mark.asyncio
async def test_playground_chat_backend_error_returns_ok_false() -> None:
    b = _FakeBackend(raise_error=BackendError(
        "BACKEND_UNREACHABLE", "ollama down", suggestion="start ollama",
    ))
    r = await playground_chat(b, [{"role": "user", "content": "hi"}])
    assert not r.ok
    assert r.error is not None
    assert r.error["code"] == "BACKEND_UNREACHABLE"
    assert r.error["suggestion"] == "start ollama"


# ─── playground_stream ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_playground_stream_emits_tokens_then_done() -> None:
    b = _FakeBackend()
    events = []
    async for ev in playground_stream(b, [{"role": "user", "content": "hi"}]):
        events.append(ev)
    types = [e["type"] for e in events]
    assert types[-1] == "done"
    token_deltas = [e["delta"] for e in events if e["type"] == "token"]
    assert token_deltas == ["hel", "lo"]
    done = events[-1]
    assert done["ok"] is True
    assert done["content"] == "hello"
    assert done["metrics"]["output_tokens"] == 10
    assert done["metrics"]["tokens_per_second"] == 20.0


@pytest.mark.asyncio
async def test_playground_stream_backend_error_yields_done_false() -> None:
    b = _FakeBackend(raise_error=BackendError("BACKEND_HTTP_ERROR", "boom"))
    events = []
    async for ev in playground_stream(b, [{"role": "user", "content": "hi"}]):
        events.append(ev)
    assert len(events) == 1
    assert events[0]["type"] == "done"
    assert events[0]["ok"] is False
    assert events[0]["error"]["code"] == "BACKEND_HTTP_ERROR"


@pytest.mark.asyncio
async def test_playground_stream_falls_back_to_chat_when_no_streaming_support() -> None:
    class _NoStreamBackend(_FakeBackend):
        supports_streaming = False

        async def stream(self, *args, **kwargs):
            raise AssertionError("should not be called when supports_streaming=False")
            yield {}  # pragma: no cover

    b = _NoStreamBackend(reply="single shot")
    events = []
    async for ev in playground_stream(b, [{"role": "user", "content": "hi"}]):
        events.append(ev)
    assert len(events) == 1
    assert events[0]["type"] == "done"
    assert events[0]["content"] == "single shot"


# ─── list_backend_models passthrough ─────────────────────────────


@pytest.mark.asyncio
async def test_list_backend_models_passthrough() -> None:
    b = AsyncMock()
    b.list_models = AsyncMock(return_value=[{"name": "qwen2.5:3b", "size": 100}])
    out = await list_backend_models(b)
    assert out == [{"name": "qwen2.5:3b", "size": 100}]


@pytest.mark.asyncio
async def test_list_backend_models_returns_empty_if_unsupported() -> None:
    class _Bare:
        pass
    out = await list_backend_models(_Bare())  # type: ignore[arg-type]
    assert out == []
