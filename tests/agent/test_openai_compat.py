"""Tests for OpenAICompatBackend (HTTP mocked via httpx.MockTransport)."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

import httpx
import pytest

from alb.agent.backend import BackendError, Message, ToolSpec
from alb.agent.backends.openai_compat import OpenAICompatBackend


def _mock_transport(handler: callable) -> httpx.MockTransport:  # type: ignore[valid-type]
    return httpx.MockTransport(handler)


def _sse(events: Iterable[dict[str, Any] | str]) -> bytes:
    """Build a chunked SSE payload — each event becomes `data: ...\\n\\n`.
    Strings are written verbatim (so `[DONE]` works); dicts get json.dumps."""
    out = []
    for e in events:
        if isinstance(e, str):
            out.append(f"data: {e}\n\n")
        else:
            out.append(f"data: {json.dumps(e)}\n\n")
    return "".join(out).encode("utf-8")


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
                "id": "chatcmpl-1",
                "model": "Qwen/Qwen2.5-7B-Instruct",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "hi there"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 7,
                    "completion_tokens": 5,
                    "total_tokens": 12,
                },
            },
        )

    b = OpenAICompatBackend(
        model="Qwen/Qwen2.5-7B-Instruct",
        transport=_mock_transport(handler),
    )
    resp = await b.chat([Message(role="user", content="hi")])

    assert resp.content == "hi there"
    assert resp.finish_reason == "stop"
    assert resp.tool_calls == []
    assert resp.usage["input_tokens"] == 7
    assert resp.usage["output_tokens"] == 5
    assert resp.usage["total_tokens"] == 12
    assert resp.model == "Qwen/Qwen2.5-7B-Instruct"

    # request shape
    assert captured["url"].endswith("/chat/completions")
    assert captured["body"]["model"] == "Qwen/Qwen2.5-7B-Instruct"
    assert captured["body"]["stream"] is False
    assert captured["body"]["messages"] == [{"role": "user", "content": "hi"}]
    assert captured["body"]["temperature"] == 0.2
    # No api_key → no Authorization header
    assert "authorization" not in {k.lower() for k in captured["headers"]}
    assert "tools" not in captured["body"]


@pytest.mark.asyncio
async def test_chat_with_api_key_sets_bearer() -> None:
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(req.headers)
        return httpx.Response(
            200,
            json={
                "model": "gpt-4o-mini",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    b = OpenAICompatBackend(
        model="gpt-4o-mini",
        api_key="sk-test-1234",
        transport=_mock_transport(handler),
    )
    await b.chat([Message(role="user", content="...")])

    auth = captured["headers"].get("authorization") or captured["headers"].get(
        "Authorization"
    )
    assert auth == "Bearer sk-test-1234"


@pytest.mark.asyncio
async def test_chat_injects_tools() -> None:
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "model": "x",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": ""},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
            },
        )

    b = OpenAICompatBackend(model="x", transport=_mock_transport(handler))
    tool = ToolSpec(
        name="alb_logcat",
        description="collect logcat",
        parameters={
            "type": "object",
            "properties": {"duration": {"type": "integer"}},
        },
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
async def test_chat_parses_tool_calls_with_string_arguments() -> None:
    """OpenAI servers serialise function.arguments as a JSON STRING; we
    must parse it (the agent loop expects dict args)."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "x",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_abc",
                                    "type": "function",
                                    "function": {
                                        "name": "alb_logcat",
                                        "arguments": '{"duration": 5}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 4, "completion_tokens": 8, "total_tokens": 12},
            },
        )

    b = OpenAICompatBackend(model="x", transport=_mock_transport(handler))
    resp = await b.chat([Message(role="user", content="...")])

    assert resp.finish_reason == "tool_calls"
    assert len(resp.tool_calls) == 1
    tc = resp.tool_calls[0]
    assert tc.id == "call_abc"
    assert tc.name == "alb_logcat"
    assert tc.arguments == {"duration": 5}


@pytest.mark.asyncio
async def test_chat_tool_calls_garbled_json_falls_back() -> None:
    """Bad arguments JSON shouldn't crash the agent loop — wrap raw."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "x",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_x",
                                    "type": "function",
                                    "function": {
                                        "name": "alb_logcat",
                                        "arguments": "{not json",
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    b = OpenAICompatBackend(model="x", transport=_mock_transport(handler))
    resp = await b.chat([Message(role="user", content="...")])
    assert resp.tool_calls[0].arguments == {"__raw__": "{not json"}


@pytest.mark.asyncio
async def test_chat_serialises_assistant_tool_calls() -> None:
    """When sending a prior assistant turn that contained tool_calls,
    function.arguments must be a JSON STRING per OpenAI spec (opposite
    of Ollama which keeps it as a dict)."""
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "model": "x",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "done"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    b = OpenAICompatBackend(model="x", transport=_mock_transport(handler))
    from alb.agent.backend import ToolCall

    msgs = [
        Message(role="user", content="..."),
        Message(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(id="t1", name="alb_logcat", arguments={"duration": 5})
            ],
        ),
        Message(role="tool", content='{"lines": []}', tool_call_id="t1"),
    ]
    await b.chat(msgs)

    assistant_msg = captured["body"]["messages"][1]
    assert assistant_msg["tool_calls"][0]["function"]["arguments"] == '{"duration": 5}'
    tool_msg = captured["body"]["messages"][2]
    assert tool_msg == {
        "role": "tool",
        "tool_call_id": "t1",
        "content": '{"lines": []}',
    }


@pytest.mark.asyncio
async def test_chat_raises_on_missing_model() -> None:
    """Default model is empty; chat() must raise BACKEND_MISCONFIGURED
    with a clear suggestion (health() can still run, but inference can't)."""
    b = OpenAICompatBackend(transport=_mock_transport(lambda r: httpx.Response(200)))
    with pytest.raises(BackendError) as exc:
        await b.chat([Message(role="user", content="hi")])
    assert exc.value.code == "BACKEND_MISCONFIGURED"
    assert "model" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_chat_http_error_wrapped() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid api key"})

    b = OpenAICompatBackend(model="x", transport=_mock_transport(handler))
    with pytest.raises(BackendError) as exc:
        await b.chat([Message(role="user", content="hi")])
    assert exc.value.code == "BACKEND_HTTP_ERROR"
    assert "401" in str(exc.value)


# ─── health() ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_reachable_with_model() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/v1/models"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "Qwen/Qwen2.5-7B-Instruct", "object": "model"},
                    {"id": "gpt-4o-mini", "object": "model"},
                ]
            },
        )

    b = OpenAICompatBackend(
        model="Qwen/Qwen2.5-7B-Instruct",
        transport=_mock_transport(handler),
    )
    snap = await b.health()
    assert snap.reachable is True
    assert snap.model == "Qwen/Qwen2.5-7B-Instruct"
    assert snap.model_present is True


@pytest.mark.asyncio
async def test_health_reachable_missing_model() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "different-model"}]})

    b = OpenAICompatBackend(
        model="Qwen/Qwen2.5-7B-Instruct",
        transport=_mock_transport(handler),
    )
    snap = await b.health()
    assert snap.reachable is True
    assert snap.model_present is False


@pytest.mark.asyncio
async def test_health_no_model_configured_returns_none_for_present() -> None:
    """Health probe runs even when model isn't configured (e.g. dashboard
    probe of registered backend before user picks a model). model_present
    is None ('we couldn't tell') rather than False ('we checked, it's
    missing')."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "any"}]})

    b = OpenAICompatBackend(transport=_mock_transport(handler))
    snap = await b.health()
    assert snap.reachable is True
    assert snap.model is None
    assert snap.model_present is None


@pytest.mark.asyncio
async def test_health_unreachable() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope", request=req)

    b = OpenAICompatBackend(transport=_mock_transport(handler))
    snap = await b.health()
    assert snap.reachable is False
    assert snap.error is not None


# ─── stream() ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stream_content_deltas_and_done() -> None:
    """Plain content stream — three deltas + finish_reason + final
    usage event. We expect three token events and one done event."""

    payload = _sse(
        [
            {
                "choices": [{"index": 0, "delta": {"content": "hi"}}],
                "model": "x",
            },
            {
                "choices": [{"index": 0, "delta": {"content": " there"}}],
            },
            {
                "choices": [{"index": 0, "delta": {"content": "!"}}],
            },
            {
                "choices": [
                    {"index": 0, "delta": {}, "finish_reason": "stop"}
                ],
            },
            {
                "choices": [],
                "usage": {
                    "prompt_tokens": 4,
                    "completion_tokens": 3,
                    "total_tokens": 7,
                },
            },
            "[DONE]",
        ]
    )

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=payload,
            headers={"content-type": "text/event-stream"},
        )

    b = OpenAICompatBackend(model="x", transport=_mock_transport(handler))
    events = [
        ev async for ev in b.stream([Message(role="user", content="hi")])
    ]

    tokens = [e for e in events if e["type"] == "token"]
    assert [t["delta"] for t in tokens] == ["hi", " there", "!"]
    assert all(t["tokens"] == 1 for t in tokens)

    done = [e for e in events if e["type"] == "done"]
    assert len(done) == 1
    assert done[0]["content"] == "hi there!"
    assert done[0]["finish_reason"] == "stop"
    assert done[0]["usage"]["total_tokens"] == 7
    assert done[0]["tool_calls"] == []


@pytest.mark.asyncio
async def test_stream_accumulates_tool_call_arguments() -> None:
    """Tool-call arguments arrive fragmented across N deltas — must
    accumulate by index and parse the resulting JSON."""

    payload = _sse(
        [
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_abc",
                                    "function": {
                                        "name": "alb_logcat",
                                        "arguments": "",
                                    },
                                }
                            ]
                        },
                    }
                ],
            },
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "function": {"arguments": '{"dura'},
                                }
                            ]
                        },
                    }
                ],
            },
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "function": {"arguments": 'tion": 5}'},
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            },
            "[DONE]",
        ]
    )

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=payload,
            headers={"content-type": "text/event-stream"},
        )

    b = OpenAICompatBackend(model="x", transport=_mock_transport(handler))
    events = [
        ev async for ev in b.stream([Message(role="user", content="hi")])
    ]

    done = [e for e in events if e["type"] == "done"][0]
    assert done["finish_reason"] == "tool_calls"
    assert done["tool_calls"] == [
        {
            "id": "call_abc",
            "name": "alb_logcat",
            "arguments": {"duration": 5},
        }
    ]


@pytest.mark.asyncio
async def test_stream_http_error_wrapped() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"upstream busted")

    b = OpenAICompatBackend(model="x", transport=_mock_transport(handler))
    with pytest.raises(BackendError) as exc:
        async for _ in b.stream([Message(role="user", content="hi")]):
            pass
    assert exc.value.code == "BACKEND_HTTP_ERROR"


@pytest.mark.asyncio
async def test_stream_401_wrapped() -> None:
    """API-key failure is more common than 500; cover it explicitly so
    a future status_code threshold tweak (e.g. only catching >=500)
    doesn't silently break this path."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, content=b'{"error":"invalid_api_key"}')

    b = OpenAICompatBackend(
        model="x",
        api_key="sk-bogus",
        transport=_mock_transport(handler),
    )
    with pytest.raises(BackendError) as exc:
        async for _ in b.stream([Message(role="user", content="hi")]):
            pass
    assert exc.value.code == "BACKEND_HTTP_ERROR"
    assert "401" in str(exc.value)


@pytest.mark.asyncio
async def test_stream_tool_call_with_interleaved_content() -> None:
    """Some servers (vLLM tool-calling builds) interleave content
    deltas BEFORE finalising tool_calls. Buffer must keep both apart
    — content accumulates into content_parts, tool_calls accumulate
    via per-index buffers, no cross-contamination."""

    payload = _sse(
        [
            {
                "choices": [
                    {"index": 0, "delta": {"content": "Calling tool: "}},
                ],
            },
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_z",
                                    "function": {
                                        "name": "alb_logcat",
                                        "arguments": '{"duration": 3}',
                                    },
                                }
                            ]
                        },
                    }
                ],
            },
            {
                "choices": [
                    {"index": 0, "delta": {}, "finish_reason": "tool_calls"}
                ],
            },
            "[DONE]",
        ]
    )

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=payload,
            headers={"content-type": "text/event-stream"},
        )

    b = OpenAICompatBackend(model="x", transport=_mock_transport(handler))
    events = [
        ev async for ev in b.stream([Message(role="user", content="hi")])
    ]

    tokens = [e for e in events if e["type"] == "token"]
    assert [t["delta"] for t in tokens] == ["Calling tool: "]

    done = [e for e in events if e["type"] == "done"][0]
    assert done["content"] == "Calling tool: "
    assert done["finish_reason"] == "tool_calls"
    assert len(done["tool_calls"]) == 1
    assert done["tool_calls"][0]["arguments"] == {"duration": 3}


@pytest.mark.asyncio
async def test_chat_extra_kwargs_cannot_override_reserved_fields() -> None:
    """Caller passing kwargs that collide with structural body keys
    via **kwargs must NOT silently swap them out. (Python catches
    dup-named positional/keyword args at the call site, so the only
    realistic slip-throughs are `model` and `stream` — neither is in
    chat()'s explicit signature.)"""
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "model": "x",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    b = OpenAICompatBackend(model="x", transport=_mock_transport(handler))
    await b.chat(
        [Message(role="user", content="hi")],
        # Hostile / accidental kwargs that try to swap structural keys
        # via **kwargs — both must be filtered.
        model="evil-swap",
        stream=True,
        # legitimate passthrough that SHOULD land on the body
        top_p=0.5,
    )

    body = captured["body"]
    assert body["model"] == "x"  # not "evil-swap"
    assert body["stream"] is False  # not True (we set stream=False for chat())
    assert body["top_p"] == 0.5  # non-reserved kwarg passes through


@pytest.mark.asyncio
async def test_chat_default_options_cannot_override_reserved_fields() -> None:
    """Same guard at construction time — default_options containing
    reserved keys must be filtered, not swap structural body."""
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "model": "x",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    b = OpenAICompatBackend(
        model="x",
        default_options={"model": "swap", "messages": [], "top_k": 50},
        transport=_mock_transport(handler),
    )
    await b.chat([Message(role="user", content="hi")])

    body = captured["body"]
    assert body["model"] == "x"
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert body["top_k"] == 50  # non-reserved default passes through


# ─── list_models() ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_models_passthrough() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "model-a", "object": "model", "owned_by": "vllm"},
                    {"id": "model-b", "object": "model", "owned_by": "vllm"},
                ]
            },
        )

    b = OpenAICompatBackend(transport=_mock_transport(handler))
    models = await b.list_models()
    assert [m["id"] for m in models] == ["model-a", "model-b"]
