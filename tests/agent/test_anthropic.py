"""Tests for AnthropicBackend (HTTP mocked via httpx.MockTransport).

Mirrors test_openai_compat.py — same fixture pattern, no real network.
Secret never enters tests: `api_key="test-key"` is throwaway, the mock
transport never validates it.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from alb.agent.backend import (
    BackendError,
    Message,
    ToolCall,
    ToolSpec,
)
from alb.agent.backends.anthropic import (
    AnthropicBackend,
    _message_to_anthropic,
    _stop_reason_to_finish,
    _tool_to_anthropic,
)


def _mock(handler: callable) -> httpx.MockTransport:  # type: ignore[valid-type]
    return httpx.MockTransport(handler)


# ─── chat() ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_happy_path_no_tools() -> None:
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["headers"] = dict(req.headers)
        captured["body"] = json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "id": "msg_01",
                "type": "message",
                "role": "assistant",
                "model": "claude-haiku-4-5-20251001",
                "content": [{"type": "text", "text": "Hello!"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 12, "output_tokens": 3},
            },
        )

    b = AnthropicBackend(api_key="test-key", transport=_mock(handler))
    resp = await b.chat([Message(role="user", content="Hi")])

    assert resp.content == "Hello!"
    assert resp.tool_calls == []
    assert resp.finish_reason == "stop"
    assert resp.usage == {
        "input_tokens": 12,
        "output_tokens": 3,
        "total_tokens": 15,
    }
    assert resp.model == "claude-haiku-4-5-20251001"

    # Wire-format invariants
    assert captured["url"].endswith("/v1/messages")
    assert captured["headers"]["x-api-key"] == "test-key"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    body = captured["body"]
    assert body["model"] == "claude-haiku-4-5-20251001"
    assert body["messages"] == [{"role": "user", "content": "Hi"}]
    assert "system" not in body  # no system message in input
    assert body["max_tokens"] == 4096  # default
    assert body["stream"] is False


@pytest.mark.asyncio
async def test_chat_extracts_system_message_to_top_level() -> None:
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "id": "msg_02",
                "type": "message",
                "role": "assistant",
                "model": "claude-haiku-4-5-20251001",
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 5, "output_tokens": 1},
            },
        )

    b = AnthropicBackend(api_key="k", transport=_mock(handler))
    await b.chat([
        Message(role="system", content="You are concise."),
        Message(role="user", content="Hi"),
    ])

    body = captured["body"]
    assert body["system"] == "You are concise."
    # System NOT included in the messages list — Anthropic rejects that.
    assert body["messages"] == [{"role": "user", "content": "Hi"}]


@pytest.mark.asyncio
async def test_chat_with_tools_emits_tool_calls() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        # Verify tools wire format: parameters → input_schema.
        assert body["tools"] == [
            {
                "name": "get_weather",
                "description": "...",
                "input_schema": {"type": "object"},
            }
        ]
        return httpx.Response(
            200,
            json={
                "id": "msg_03",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-6",
                "content": [
                    {"type": "text", "text": "Looking up..."},
                    {
                        "type": "tool_use",
                        "id": "toolu_abc",
                        "name": "get_weather",
                        "input": {"city": "Tokyo"},
                    },
                ],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 30, "output_tokens": 18},
            },
        )

    b = AnthropicBackend(
        model="claude-sonnet-4-6",
        api_key="k",
        transport=_mock(handler),
    )
    resp = await b.chat(
        [Message(role="user", content="Weather?")],
        tools=[ToolSpec(name="get_weather", description="...",
                        parameters={"type": "object"})],
    )

    assert resp.content == "Looking up..."
    assert resp.finish_reason == "tool_calls"
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0] == ToolCall(
        id="toolu_abc", name="get_weather", arguments={"city": "Tokyo"}
    )


@pytest.mark.asyncio
async def test_chat_max_tokens_per_call_overrides_default() -> None:
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "id": "x", "type": "message", "role": "assistant",
                "model": "claude-haiku-4-5-20251001",
                "content": [{"type": "text", "text": ""}],
                "stop_reason": "max_tokens",
                "usage": {"input_tokens": 1, "output_tokens": 100},
            },
        )

    b = AnthropicBackend(api_key="k", transport=_mock(handler))
    resp = await b.chat([Message(role="user", content="x")], max_tokens=100)
    assert captured["body"]["max_tokens"] == 100
    assert resp.finish_reason == "length"


@pytest.mark.asyncio
async def test_chat_unauthorized_maps_to_backend_error() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, json={"type": "error", "error": {"type": "authentication_error"}}
        )

    b = AnthropicBackend(api_key="bad", transport=_mock(handler))
    with pytest.raises(BackendError) as exc_info:
        await b.chat([Message(role="user", content="x")])
    assert exc_info.value.code == "BACKEND_UNAUTHORIZED"


@pytest.mark.asyncio
async def test_chat_rate_limited_maps_to_backend_error() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate_limit"})

    b = AnthropicBackend(api_key="k", transport=_mock(handler))
    with pytest.raises(BackendError) as exc_info:
        await b.chat([Message(role="user", content="x")])
    assert exc_info.value.code == "BACKEND_RATE_LIMITED"


@pytest.mark.asyncio
async def test_chat_without_api_key_raises_misconfigured() -> None:
    b = AnthropicBackend(api_key=None)  # no transport — must fail early
    with pytest.raises(BackendError) as exc_info:
        await b.chat([Message(role="user", content="x")])
    assert exc_info.value.code == "BACKEND_MISCONFIGURED"


@pytest.mark.asyncio
async def test_chat_without_model_raises_misconfigured() -> None:
    b = AnthropicBackend(model="", api_key="k")
    with pytest.raises(BackendError) as exc_info:
        await b.chat([Message(role="user", content="x")])
    assert exc_info.value.code == "BACKEND_MISCONFIGURED"


# ─── Body construction edge cases ───────────────────────────────────


@pytest.mark.asyncio
async def test_chat_drops_incompatible_keys_silently() -> None:
    """Ollama-style `options` / OpenAI-style `presence_penalty` etc.
    must not reach the upstream API or it would 400 us."""
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "id": "x", "type": "message", "role": "assistant",
                "model": "claude-haiku-4-5-20251001",
                "content": [], "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 0},
            },
        )

    b = AnthropicBackend(api_key="k", transport=_mock(handler))
    await b.chat(
        [Message(role="user", content="x")],
        options={"foo": "bar"},
        presence_penalty=0.5,
        stream_options={"include_usage": True},
        think=True,
        # These are OK Anthropic-recognised:
        top_p=0.9,
        top_k=40,
    )

    body = captured["body"]
    for forbidden in ("options", "presence_penalty", "stream_options",
                      "frequency_penalty", "think"):
        assert forbidden not in body, f"{forbidden} leaked into body"
    assert body["top_p"] == 0.9
    assert body["top_k"] == 40


@pytest.mark.asyncio
async def test_chat_reserved_keys_cannot_be_overridden_by_kwargs() -> None:
    """A caller who tries to swap `messages` via **kwargs gets ignored."""
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "id": "x", "type": "message", "role": "assistant",
                "model": "claude-haiku-4-5-20251001",
                "content": [], "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 0},
            },
        )

    b = AnthropicBackend(api_key="k", transport=_mock(handler))
    # Only kwargs Python actually routes through **kwargs can collide
    # — `messages` / `model` / `tools` / `temperature` / `max_tokens`
    # are positional / named params and Python rejects double-pass at
    # call site. The remaining reserved keys (`system`, `stream`)
    # CAN sneak through **kwargs and our guard must drop them.
    await b.chat(
        [Message(role="user", content="real")],
        max_tokens=200,
        system="injected-system-prompt",  # reserved, must not reach body
        stream=True,                       # reserved, would break wire format
    )

    body = captured["body"]
    assert body["messages"] == [{"role": "user", "content": "real"}]
    assert "system" not in body  # injected `system` is reserved → blocked
    assert body["stream"] is False  # constructor's structural value wins


# ─── Message + tool translation helpers ─────────────────────────────


def test_message_to_anthropic_user_text() -> None:
    out = _message_to_anthropic(Message(role="user", content="hi"))
    assert out == {"role": "user", "content": "hi"}


def test_message_to_anthropic_assistant_with_tool_calls() -> None:
    m = Message(
        role="assistant",
        content="thinking...",
        tool_calls=[ToolCall(id="t1", name="f", arguments={"a": 1})],
    )
    out = _message_to_anthropic(m)
    assert out == {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "thinking..."},
            {"type": "tool_use", "id": "t1", "name": "f", "input": {"a": 1}},
        ],
    }


def test_message_to_anthropic_assistant_tool_calls_only_no_text() -> None:
    m = Message(
        role="assistant",
        content="",
        tool_calls=[ToolCall(id="t1", name="f", arguments={})],
    )
    out = _message_to_anthropic(m)
    # text block omitted when content is empty
    assert out["content"] == [
        {"type": "tool_use", "id": "t1", "name": "f", "input": {}}
    ]


def test_message_to_anthropic_tool_result() -> None:
    m = Message(role="tool", content='{"ok":true}', tool_call_id="t1")
    out = _message_to_anthropic(m)
    # Anthropic wraps tool_result in a user message
    assert out == {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "t1",
                "content": '{"ok":true}',
            }
        ],
    }


def test_tool_to_anthropic_renames_parameters_to_input_schema() -> None:
    t = ToolSpec(
        name="f",
        description="d",
        parameters={"type": "object", "properties": {}},
    )
    out = _tool_to_anthropic(t)
    assert out == {
        "name": "f",
        "description": "d",
        "input_schema": {"type": "object", "properties": {}},
    }


@pytest.mark.parametrize(
    "reason,expected",
    [
        ("end_turn", "stop"),
        ("stop_sequence", "stop"),
        ("max_tokens", "length"),
        ("tool_use", "tool_calls"),
        ("anything-else", "stop"),
        (None, "stop"),
    ],
)
def test_stop_reason_to_finish_mapping(reason, expected) -> None:
    assert _stop_reason_to_finish(reason) == expected


# ─── _parse_response edge cases ─────────────────────────────────────


@pytest.mark.asyncio
async def test_parse_response_handles_non_dict_input_in_tool_use() -> None:
    """If Anthropic ever sends a non-dict `input` (it shouldn't, but
    defensive), we wrap it in {"__raw__": ...} same as openai-compat
    does for malformed function args, so the agent loop doesn't crash."""

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "x", "type": "message", "role": "assistant",
                "model": "claude-haiku-4-5-20251001",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "f",
                        "input": "not-a-dict",  # wrong type, defensive path
                    }
                ],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    b = AnthropicBackend(api_key="k", transport=_mock(handler))
    resp = await b.chat([Message(role="user", content="x")])
    assert resp.tool_calls[0].arguments == {"__raw__": "not-a-dict"}


# ─── health() ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_no_api_key_returns_unreachable_with_hint() -> None:
    b = AnthropicBackend(api_key=None)
    h = await b.health()
    assert h.reachable is False
    assert "ANTHROPIC_API_KEY" in (h.error or "")


@pytest.mark.asyncio
async def test_health_lists_models_and_marks_present() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "claude-haiku-4-5-20251001"},
                    {"id": "claude-sonnet-4-6"},
                ]
            },
        )

    b = AnthropicBackend(api_key="k", transport=_mock(handler))
    h = await b.health()
    assert h.reachable is True
    assert h.model == "claude-haiku-4-5-20251001"
    assert h.model_present is True


@pytest.mark.asyncio
async def test_health_model_absent_marks_not_present() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "claude-other"}]})

    b = AnthropicBackend(
        model="claude-haiku-4-5-20251001",
        api_key="k",
        transport=_mock(handler),
    )
    h = await b.health()
    assert h.reachable is True
    assert h.model_present is False


@pytest.mark.asyncio
async def test_health_http_error_returns_unreachable() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="oops")

    b = AnthropicBackend(api_key="k", transport=_mock(handler))
    h = await b.health()
    assert h.reachable is False
    assert h.error  # carries the httpx.HTTPStatusError repr


# ─── stream() — placeholder until commit 83 ─────────────────────────


@pytest.mark.asyncio
async def test_stream_raises_until_implemented() -> None:
    b = AnthropicBackend(api_key="k")
    with pytest.raises(NotImplementedError):
        async for _ in b.stream([Message(role="user", content="x")]):
            pass
