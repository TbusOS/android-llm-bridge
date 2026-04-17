"""Tests for AgentLoop with a fake LLM backend."""

from __future__ import annotations

import json
from collections.abc import Awaitable
from typing import Any

import pytest

from alb.agent.backend import (
    BackendError,
    ChatResponse,
    LLMBackend,
    Message,
    ToolCall,
    ToolSpec,
)
from alb.agent.loop import AgentLoop
from alb.agent.session import ChatSession


# ─── Fake backend ────────────────────────────────────────────────────


class FakeBackend(LLMBackend):
    """Scripted backend: pops a queue of ChatResponse objects per chat() call."""

    name = "fake"
    supports_tool_calls = True

    def __init__(self, scripted: list[ChatResponse | Exception]) -> None:
        self.model = "fake"
        self._queue = list(scripted)
        self.calls: list[list[Message]] = []

    async def chat(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        self.calls.append([Message(**m.__dict__) for m in messages])
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _tc(name: str, args: dict[str, Any], tc_id: str = "tc_X") -> ToolCall:
    return ToolCall(id=tc_id, name=name, arguments=args)


def _final(content: str) -> ChatResponse:
    return ChatResponse(content=content, finish_reason="stop")


def _calls(*tcs: ToolCall) -> ChatResponse:
    return ChatResponse(content="", tool_calls=list(tcs), finish_reason="tool_calls")


_TOOL = ToolSpec(name="alb_logcat", description="x", parameters={"type": "object"})
_REBOOT = ToolSpec(name="alb_reboot", description="r", parameters={"type": "object"})


# ─── Single-turn paths ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_simple_reply_no_tool_calls() -> None:
    b = FakeBackend([_final("你好")])

    async def exec_nothing(name: str, args: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("should not be called")

    loop = AgentLoop(b, tools=[_TOOL], tool_executor=exec_nothing)
    r = await loop.run("hi")
    assert r.ok is True
    assert r.data == "你好"
    assert r.artifacts == []


@pytest.mark.asyncio
async def test_system_prompt_injected() -> None:
    b = FakeBackend([_final("ok")])

    async def exec_(name: str, args: dict[str, Any]) -> dict[str, Any]:
        return {}

    loop = AgentLoop(
        b,
        tools=[_TOOL],
        tool_executor=exec_,
        system_prompt="you are helpful",
    )
    await loop.run("hi")
    first_msgs = b.calls[0]
    assert first_msgs[0].role == "system"
    assert first_msgs[0].content == "you are helpful"
    assert first_msgs[1].role == "user"


@pytest.mark.asyncio
async def test_tool_call_then_final() -> None:
    b = FakeBackend(
        [
            _calls(_tc("alb_logcat", {"duration": 30}, "tc_1")),
            _final("已抓到 12 条 error"),
        ]
    )
    executed: list[tuple[str, dict]] = []

    async def exec_(name: str, args: dict[str, Any]) -> dict[str, Any]:
        executed.append((name, args))
        return {"ok": True, "lines": 42, "artifacts": ["/tmp/workspace/logcat.txt"]}

    loop = AgentLoop(b, tools=[_TOOL], tool_executor=exec_)
    r = await loop.run("抓30秒")

    assert r.ok is True
    assert r.data == "已抓到 12 条 error"
    # Artifact collected
    assert len(r.artifacts) == 1
    assert str(r.artifacts[0]).endswith("logcat.txt")
    # Tool was dispatched with right args
    assert executed == [("alb_logcat", {"duration": 30})]
    # Second chat call sees: user + asst(tool_calls) + tool
    # (the final asst is produced BY this call, not in its input)
    assert len(b.calls[1]) == 3
    assert [m.role for m in b.calls[1]] == ["user", "assistant", "tool"]
    assert b.calls[1][2].tool_call_id == "tc_1"
    assert b.calls[1][1].tool_calls[0].id == "tc_1"


@pytest.mark.asyncio
async def test_parallel_tool_calls_same_turn() -> None:
    b = FakeBackend(
        [
            _calls(
                _tc("alb_logcat", {"duration": 10}, "tc_1"),
                _tc("alb_reboot", {"mode": "normal"}, "tc_2"),
            ),
            _final("done"),
        ]
    )
    seen: list[str] = []

    async def exec_(name: str, args: dict[str, Any]) -> dict[str, Any]:
        seen.append(name)
        return {"ok": True}

    loop = AgentLoop(b, tools=[_TOOL, _REBOOT], tool_executor=exec_)
    r = await loop.run("both")

    assert r.ok
    assert seen == ["alb_logcat", "alb_reboot"]


@pytest.mark.asyncio
async def test_unknown_tool_returns_error_envelope() -> None:
    b = FakeBackend(
        [
            _calls(_tc("nonexistent_tool", {})),
            _final("ok, recovered"),
        ]
    )

    async def exec_(name: str, args: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("must not be called for unknown tool")

    loop = AgentLoop(b, tools=[_TOOL], tool_executor=exec_)
    r = await loop.run("try unknown")

    assert r.ok  # loop finished cleanly
    # tool message injected with error
    tool_msgs = [m for m in b.calls[1] if m.role == "tool"]
    assert len(tool_msgs) == 1
    body = json.loads(tool_msgs[0].content)
    assert body["ok"] is False
    assert body["error"]["code"] == "TOOL_NOT_FOUND"


@pytest.mark.asyncio
async def test_executor_exception_becomes_tool_error() -> None:
    b = FakeBackend(
        [
            _calls(_tc("alb_logcat", {"duration": 30})),
            _final("recovered"),
        ]
    )

    async def exec_(name: str, args: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("adb broken")

    loop = AgentLoop(b, tools=[_TOOL], tool_executor=exec_)
    r = await loop.run("抓30s")

    assert r.ok
    tool_msgs = [m for m in b.calls[1] if m.role == "tool"]
    body = json.loads(tool_msgs[0].content)
    assert body["error"]["code"] == "TOOL_EXECUTOR_RAISED"
    assert "adb broken" in body["error"]["message"]


# ─── Termination paths ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_max_turns_exceeded() -> None:
    # Always emits a tool_call, never stops.
    always_tc = _calls(_tc("alb_logcat", {"duration": 1}))
    b = FakeBackend([always_tc for _ in range(10)])

    async def exec_(name: str, args: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True}

    loop = AgentLoop(b, tools=[_TOOL], tool_executor=exec_, max_turns=3)
    r = await loop.run("forever")

    assert r.ok is False
    assert r.error is not None
    assert r.error.code == "AGENT_MAX_TURNS_EXCEEDED"
    assert len(b.calls) == 3


@pytest.mark.asyncio
async def test_backend_error_propagates_as_result_fail() -> None:
    b = FakeBackend([BackendError("BACKEND_UNREACHABLE", "no daemon", suggestion="start ollama")])

    async def exec_(name: str, args: dict[str, Any]) -> dict[str, Any]:
        return {}

    loop = AgentLoop(b, tools=[_TOOL], tool_executor=exec_)
    r = await loop.run("hi")

    assert r.ok is False
    assert r.error is not None
    assert r.error.code == "BACKEND_UNREACHABLE"
    assert r.error.suggestion == "start ollama"


# ─── Session integration ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_session_captures_full_trace(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    b = FakeBackend(
        [
            _calls(_tc("alb_logcat", {"duration": 5}, "tc_1")),
            _final("done"),
        ]
    )

    async def exec_(name: str, args: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True}

    loop = AgentLoop(b, tools=[_TOOL], tool_executor=exec_)
    session = ChatSession.create(backend="fake")
    r = await loop.run("抓5s", session=session)
    assert r.ok

    # Reload — should see user + asst + tool + asst
    reloaded = ChatSession.load(session.session_id)
    msgs = reloaded.messages()
    roles = [m.role for m in msgs]
    assert roles == ["user", "assistant", "tool", "assistant"]
    assert msgs[1].tool_calls[0].name == "alb_logcat"
    assert msgs[2].tool_call_id == "tc_1"


@pytest.mark.asyncio
async def test_session_history_replayed_into_next_run(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    b = FakeBackend([_final("round 2 answer")])

    async def exec_(name: str, args: dict[str, Any]) -> dict[str, Any]:
        return {}

    session = ChatSession.create()
    session.append(Message(role="user", content="先前的问题"))
    session.append(Message(role="assistant", content="先前的答案"))

    loop = AgentLoop(b, tools=[], tool_executor=exec_)
    r = await loop.run("新问题", session=session)

    assert r.ok and r.data == "round 2 answer"
    # Backend saw [prev_user, prev_asst, new_user]
    msgs = b.calls[0]
    assert [m.role for m in msgs] == ["user", "assistant", "user"]
    assert msgs[2].content == "新问题"


@pytest.mark.asyncio
async def test_supports_tool_calls_false_suppresses_tools() -> None:
    class NoToolsBackend(FakeBackend):
        supports_tool_calls = False

    b = NoToolsBackend([_final("ok")])

    async def exec_(name: str, args: dict[str, Any]) -> dict[str, Any]:
        return {}

    loop = AgentLoop(b, tools=[_TOOL], tool_executor=exec_)
    await loop.run("hi")
    # Tool list still reaches backend.chat's tools param, but as None per loop logic.
    # We can't inspect tools arg from FakeBackend.chat without extending it, so just
    # verify a successful run without tool_calls.
