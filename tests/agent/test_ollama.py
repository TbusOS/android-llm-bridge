"""Tests for OllamaBackend (HTTP mocked via httpx.MockTransport)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from alb.agent.backend import BackendError, Message, ToolSpec
from alb.agent.backends.ollama import OllamaBackend


def _mock_transport(handler: callable) -> httpx.MockTransport:  # type: ignore[valid-type]
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_chat_happy_path_no_tools() -> None:
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["body"] = json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "model": "qwen2.5:3b",
                "message": {"role": "assistant", "content": "hi there"},
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 7,
                "eval_count": 5,
            },
        )

    b = OllamaBackend(model="qwen2.5:3b", transport=_mock_transport(handler))
    resp = await b.chat([Message(role="user", content="hi")])

    assert resp.content == "hi there"
    assert resp.finish_reason == "stop"
    assert resp.tool_calls == []
    # Usage now also carries Ollama timing fields (ms); just spot-check
    # the token counts that this test cares about.
    assert resp.usage["input_tokens"] == 7
    assert resp.usage["output_tokens"] == 5
    assert resp.usage["total_tokens"] == 12
    assert resp.model == "qwen2.5:3b"

    # request shape
    assert captured["url"].endswith("/api/chat")
    assert captured["body"]["model"] == "qwen2.5:3b"
    assert captured["body"]["stream"] is False
    assert captured["body"]["messages"] == [{"role": "user", "content": "hi"}]
    assert captured["body"]["options"]["temperature"] == 0.2
    assert "tools" not in captured["body"]


@pytest.mark.asyncio
async def test_chat_injects_tools() -> None:
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "model": "qwen2.5:3b",
                "message": {"role": "assistant", "content": ""},
                "done": True,
                "done_reason": "stop",
            },
        )

    b = OllamaBackend(transport=_mock_transport(handler))
    tool = ToolSpec(
        name="alb_logcat",
        description="collect logcat",
        parameters={"type": "object", "properties": {"duration": {"type": "integer"}}},
    )
    await b.chat([Message(role="user", content="...")], tools=[tool])

    assert captured["body"]["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "alb_logcat",
                "description": "collect logcat",
                "parameters": {
                    "type": "object",
                    "properties": {"duration": {"type": "integer"}},
                },
            },
        }
    ]


@pytest.mark.asyncio
async def test_chat_parses_tool_calls() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "qwen2.5:3b",
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "alb_logcat",
                                "arguments": {"duration": 30, "filter": "*:E"},
                            }
                        }
                    ],
                },
                "done": True,
                "done_reason": "stop",
            },
        )

    b = OllamaBackend(transport=_mock_transport(handler))
    resp = await b.chat([Message(role="user", content="抓30秒")])

    assert resp.finish_reason == "tool_calls"
    assert len(resp.tool_calls) == 1
    tc = resp.tool_calls[0]
    assert tc.name == "alb_logcat"
    assert tc.arguments == {"duration": 30, "filter": "*:E"}
    assert tc.id.startswith("tc_")  # synthesised ID


@pytest.mark.asyncio
async def test_chat_tool_call_arguments_string_parsed_as_json() -> None:
    """Some non-Ollama backends return arguments as a JSON string."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "qwen2.5:3b",
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "alb_reboot", "arguments": '{"mode":"normal"}'}}
                    ],
                },
                "done": True,
            },
        )

    b = OllamaBackend(transport=_mock_transport(handler))
    resp = await b.chat([Message(role="user", content="reboot")])

    assert resp.tool_calls[0].arguments == {"mode": "normal"}


@pytest.mark.asyncio
async def test_chat_tool_call_arguments_invalid_json_falls_back() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "qwen2.5:3b",
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"function": {"name": "x", "arguments": "not-json{"}}],
                },
                "done": True,
            },
        )

    b = OllamaBackend(transport=_mock_transport(handler))
    resp = await b.chat([])
    assert resp.tool_calls[0].arguments == {"__raw__": "not-json{"}


@pytest.mark.asyncio
async def test_chat_serialises_assistant_tool_calls_in_history() -> None:
    """If a prior assistant turn had tool_calls, backend must forward them."""
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.content)
        return httpx.Response(
            200,
            json={"model": "q", "message": {"role": "assistant", "content": "ok"}, "done": True},
        )

    from alb.agent.backend import ToolCall

    b = OllamaBackend(transport=_mock_transport(handler))
    history = [
        Message(role="user", content="抓日志"),
        Message(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="tc_1", name="alb_logcat", arguments={"duration": 30})],
        ),
        Message(
            role="tool",
            content='{"ok": true, "lines": 42}',
            tool_call_id="tc_1",
            name="alb_logcat",
        ),
    ]
    await b.chat(history)
    wire = captured["body"]["messages"]
    assert wire[1]["tool_calls"] == [
        {"function": {"name": "alb_logcat", "arguments": {"duration": 30}}}
    ]
    assert wire[2]["role"] == "tool"
    assert wire[2]["name"] == "alb_logcat"


# ─── Error paths ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_connect_error_maps_to_backend_unreachable() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=req)

    b = OllamaBackend(transport=_mock_transport(handler))
    with pytest.raises(BackendError) as ei:
        await b.chat([Message(role="user", content="hi")])
    assert ei.value.code == "BACKEND_UNREACHABLE"
    assert "ollama" in str(ei.value).lower()


@pytest.mark.asyncio
async def test_chat_timeout_maps_to_backend_timeout() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=req)

    b = OllamaBackend(transport=_mock_transport(handler), timeout=1.0)
    with pytest.raises(BackendError) as ei:
        await b.chat([Message(role="user", content="hi")])
    assert ei.value.code == "BACKEND_TIMEOUT"


@pytest.mark.asyncio
async def test_chat_4xx_maps_to_http_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="model not found")

    b = OllamaBackend(transport=_mock_transport(handler))
    with pytest.raises(BackendError) as ei:
        await b.chat([Message(role="user", content="hi")])
    assert ei.value.code == "BACKEND_HTTP_ERROR"
    assert "404" in str(ei.value)


# ─── health() ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_reachable_with_model() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/tags"
        return httpx.Response(
            200,
            json={
                "models": [
                    {"name": "qwen2.5:3b"},
                    {"name": "llama3.2:3b"},
                ]
            },
        )

    b = OllamaBackend(model="qwen2.5:3b", transport=_mock_transport(handler))
    snap = await b.health()
    assert snap.reachable is True
    assert snap.model_present is True
    assert snap.model == "qwen2.5:3b"


@pytest.mark.asyncio
async def test_health_reachable_missing_model() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "llama3.2:3b"}]})

    b = OllamaBackend(model="qwen2.5:3b", transport=_mock_transport(handler))
    snap = await b.health()
    assert snap.reachable is True
    assert snap.model_present is False


@pytest.mark.asyncio
async def test_health_unreachable() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope", request=req)

    b = OllamaBackend(transport=_mock_transport(handler))
    snap = await b.health()
    assert snap.reachable is False
    assert snap.error is not None


# ─── Streaming ───────────────────────────────────────────────────────


def _ndjson(*chunks: dict[str, Any]) -> bytes:
    return ("\n".join(json.dumps(c) for c in chunks) + "\n").encode("utf-8")


@pytest.mark.asyncio
async def test_stream_plain_tokens() -> None:
    """Plain chat: tokens arrive as {type:token,delta:...} + final {type:done}."""

    def handler(req: httpx.Request) -> httpx.Response:
        assert json.loads(req.content)["stream"] is True
        body = _ndjson(
            {"model": "qwen2.5:3b", "message": {"content": "he"}, "done": False},
            {"model": "qwen2.5:3b", "message": {"content": "llo"}, "done": False},
            {
                "model": "qwen2.5:3b",
                "message": {"content": ""},
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 5,
                "eval_count": 2,
            },
        )
        return httpx.Response(200, content=body)

    b = OllamaBackend(model="qwen2.5:3b", transport=_mock_transport(handler))
    events: list[dict[str, Any]] = []
    async for ev in b.stream([Message(role="user", content="hi")]):
        events.append(ev)

    token_events = [e for e in events if e["type"] == "token"]
    done_events = [e for e in events if e["type"] == "done"]
    assert [e["delta"] for e in token_events] == ["he", "llo"]
    assert len(done_events) == 1
    done = done_events[0]
    assert done["content"] == "hello"
    assert done["finish_reason"] == "stop"
    assert done["tool_calls"] == []
    assert done["usage"]["output_tokens"] == 2


@pytest.mark.asyncio
async def test_stream_tool_call_buffered_until_done() -> None:
    """Tool-call turn emits no tokens but surfaces tool_calls in done event."""

    def handler(req: httpx.Request) -> httpx.Response:
        body = _ndjson(
            {"model": "qwen2.5:3b", "message": {"content": ""}, "done": False},
            {
                "model": "qwen2.5:3b",
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {"name": "alb_logcat", "arguments": {"lines": 50}}
                        }
                    ],
                },
                "done": True,
                "done_reason": "stop",
                "eval_count": 7,
            },
        )
        return httpx.Response(200, content=body)

    b = OllamaBackend(transport=_mock_transport(handler))
    events = []
    async for ev in b.stream(
        [Message(role="user", content="grab log")],
        tools=[ToolSpec(name="alb_logcat", description="x", parameters={"type": "object"})],
    ):
        events.append(ev)

    assert [e["type"] for e in events] == ["done"]  # no token events
    done = events[0]
    assert done["finish_reason"] == "tool_calls"
    assert len(done["tool_calls"]) == 1
    tc = done["tool_calls"][0]
    assert tc["name"] == "alb_logcat"
    assert tc["arguments"] == {"lines": 50}


@pytest.mark.asyncio
async def test_stream_http_error_raises_backend_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b'{"error":"boom"}')

    b = OllamaBackend(transport=_mock_transport(handler))
    with pytest.raises(BackendError) as ei:
        async for _ in b.stream([Message(role="user", content="hi")]):
            pass
    assert ei.value.code == "BACKEND_HTTP_ERROR"


@pytest.mark.asyncio
async def test_stream_thinking_promoted_when_content_empty() -> None:
    """Reasoning-model edge case: content empty, thinking has text."""

    def handler(req: httpx.Request) -> httpx.Response:
        body = _ndjson(
            {"message": {"content": "", "thinking": "I think "}, "done": False},
            {"message": {"content": "", "thinking": "PONG"}, "done": False},
            {
                "message": {"content": ""},
                "done": True,
                "done_reason": "stop",
                "eval_count": 3,
            },
        )
        return httpx.Response(200, content=body)

    b = OllamaBackend(transport=_mock_transport(handler))
    events = []
    async for ev in b.stream([Message(role="user", content="say pong")]):
        events.append(ev)
    done = [e for e in events if e["type"] == "done"][0]
    # No content tokens at all, but done.content should be promoted from thinking
    assert done["content"] == "I think PONG"
    assert done["thinking"] == "I think PONG"
