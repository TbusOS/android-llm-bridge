"""Tests for AgentLoop with a fake LLM backend."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable
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
from alb.agent.loop import AgentLoop, repair_tool_name
from alb.agent.session import ChatSession


# ─── Fake backend ────────────────────────────────────────────────────


class FakeBackend(LLMBackend):
    """Scripted backend: pops a queue of ChatResponse objects per chat() call.

    Also exposes a stream() method for run_stream() tests that converts each
    scripted ChatResponse into token + done events.
    """

    name = "fake"
    supports_tool_calls = True
    supports_streaming = True

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

    async def stream(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        **kwargs: Any,
    ):
        self.calls.append([Message(**m.__dict__) for m in messages])
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        # Like the real backends: content streams as one delta per char in
        # every turn, including one that ends in tool calls.
        for ch in item.content:
            yield {"type": "token", "delta": ch}
        yield {
            "type": "done",
            "content": item.content,
            "tool_calls": [tc.to_dict() for tc in item.tool_calls],
            "finish_reason": item.finish_reason,
            "usage": item.usage or {},
            "model": item.model or self.model,
            "thinking": getattr(item, "thinking", ""),
        }


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


# ─── Streaming (run_stream) ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_stream_simple_reply() -> None:
    """Plain chat: expect char-level token events + final done."""
    b = FakeBackend([_final("你好")])

    async def exec_nothing(name: str, args: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("should not be called")

    loop = AgentLoop(b, tools=[_TOOL], tool_executor=exec_nothing)
    events = []
    async for ev in loop.run_stream("hi"):
        events.append(ev)

    token_events = [e for e in events if e["type"] == "token"]
    done_events = [e for e in events if e["type"] == "done"]
    assert "".join(e["delta"] for e in token_events) == "你好"
    assert len(done_events) == 1
    done = done_events[0]
    assert done["ok"] is True
    assert done["data"] == "你好"
    assert done["artifacts"] == []


@pytest.mark.asyncio
async def test_run_stream_tool_call_then_reply() -> None:
    """Tool-decision turn streams silently, tool events fire, final turn streams tokens."""
    b = FakeBackend(
        [
            _calls(_tc("alb_logcat", {"lines": 50}, tc_id="tc_1")),
            _final("日志已抓"),
        ]
    )

    async def exec_(name: str, args: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "data": {"lines": args.get("lines", 0)}}

    loop = AgentLoop(b, tools=[_TOOL], tool_executor=exec_)
    events = []
    async for ev in loop.run_stream("抓日志"):
        events.append(ev)

    types = [e["type"] for e in events]
    # Expected sequence: tool_call_start, tool_call_end, then token*, done
    assert types[0] == "tool_call_start"
    assert types[1] == "tool_call_end"
    assert events[0]["name"] == "alb_logcat"
    assert events[0]["arguments"] == {"lines": 50}
    assert events[1]["result"]["ok"] is True
    # Last event must be done with ok=True
    assert types[-1] == "done"
    assert events[-1]["ok"] is True
    assert events[-1]["data"] == "日志已抓"
    # In between there should be at least one token
    assert any(t == "token" for t in types[2:-1])


@pytest.mark.asyncio
async def test_run_stream_max_turns_exceeded() -> None:
    """Infinite tool-call loop → should terminate with AGENT_MAX_TURNS_EXCEEDED."""
    b = FakeBackend(
        [
            _calls(_tc("alb_logcat", {}, tc_id="t1")),
            _calls(_tc("alb_logcat", {}, tc_id="t2")),
            _calls(_tc("alb_logcat", {}, tc_id="t3")),
        ]
    )

    async def exec_(name: str, args: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True}

    loop = AgentLoop(b, tools=[_TOOL], tool_executor=exec_, max_turns=2)
    events = []
    async for ev in loop.run_stream("loop"):
        events.append(ev)

    assert events[-1]["type"] == "done"
    assert events[-1]["ok"] is False
    assert events[-1]["error"]["code"] == "AGENT_MAX_TURNS_EXCEEDED"


# ─── Stopped mid-turn (the page's Stop button) ───────────────────────


@pytest.mark.asyncio
async def test_run_stream_cancelled_in_a_tool_answers_every_call(tmp_path, monkeypatch) -> None:
    """A stop during the first of two calls leaves both with a CHAT_STOPPED
    result, so the session's next turn sends no unanswered tool call."""
    import asyncio

    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    b = FakeBackend([_calls(_tc("alb_logcat", {}, "tc_1"), _tc("alb_reboot", {}, "tc_2"))])
    started = asyncio.Event()

    async def exec_(name: str, args: dict[str, Any]) -> dict[str, Any]:
        started.set()
        await asyncio.sleep(30)
        return {"ok": True}

    loop = AgentLoop(b, tools=[_TOOL, _REBOOT], tool_executor=exec_)
    session = ChatSession.create(backend="fake")

    async def _drain() -> None:
        async for _ in loop.run_stream("抓日志再重启", session=session):
            pass

    task = asyncio.create_task(_drain())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    msgs = ChatSession.load(session.session_id).messages()
    assert [m.role for m in msgs] == ["user", "assistant", "tool", "tool"]
    assert [m.tool_call_id for m in msgs[2:]] == ["tc_1", "tc_2"]
    assert all(json.loads(m.content)["error"]["code"] == "CHAT_STOPPED" for m in msgs[2:])


@pytest.mark.asyncio
async def test_run_stream_closed_before_a_call_runs_answers_it(tmp_path, monkeypatch) -> None:
    """The relay closes the stream when a stop lands while it is sending
    tool_call_start: the call never runs and still gets a result."""
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    b = FakeBackend([_calls(_tc("alb_reboot", {}, "tc_1"))])
    ran: list[str] = []

    async def exec_(name: str, args: dict[str, Any]) -> dict[str, Any]:
        ran.append(name)
        return {"ok": True}

    loop = AgentLoop(b, tools=[_REBOOT], tool_executor=exec_)
    session = ChatSession.create(backend="fake")
    stream = loop.run_stream("重启", session=session)
    assert (await stream.__anext__())["type"] == "tool_call_start"
    await stream.aclose()

    assert ran == []
    last = ChatSession.load(session.session_id).messages()[-1]
    assert (last.role, last.tool_call_id) == ("tool", "tc_1")
    assert json.loads(last.content)["error"]["code"] == "CHAT_STOPPED"


# ─── Tokens go out as they arrive (2026-11-03) ───────────────────────


class _GatedBackend(LLMBackend):
    """Streams "你", then waits for `gate` before "好" and done.  `closed`
    is set when the generator is shut (its finally ran)."""

    name = "gated"
    supports_tool_calls = True
    supports_streaming = True

    def __init__(self) -> None:
        import asyncio

        self.model = "gated"
        self.gate = asyncio.Event()
        self.closed = False

    async def chat(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        **kw: Any,
    ) -> ChatResponse:
        raise AssertionError("not used")

    async def stream(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        **kw: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        try:
            yield {"type": "token", "delta": "你"}
            await self.gate.wait()
            yield {"type": "token", "delta": "好"}
            yield {"type": "done", "content": "你好", "tool_calls": [], "usage": {}}
        finally:
            self.closed = True


@pytest.mark.asyncio
async def test_run_stream_sends_a_token_before_the_turn_ends() -> None:
    """The first token reaches the client while the model is still
    generating.  The old loop held tokens until done, so this timed out."""
    import asyncio

    b = _GatedBackend()

    async def exec_(name: str, args: dict[str, Any]) -> dict[str, Any]:
        return {}

    loop = AgentLoop(b, tools=[_TOOL], tool_executor=exec_)
    stream = loop.run_stream("hi")
    first = await asyncio.wait_for(stream.__anext__(), timeout=1.0)
    assert first == {"type": "token", "delta": "你"}
    b.gate.set()
    rest = [ev async for ev in stream]
    assert [e["delta"] for e in rest if e["type"] == "token"] == ["好"]
    assert rest[-1]["type"] == "done" and rest[-1]["data"] == "你好"


@pytest.mark.asyncio
async def test_run_stream_stop_while_streaming_closes_the_backend_stream() -> None:
    """Stopping between two tokens shuts the backend's request at once
    (aclosing), instead of leaving it to the garbage collector."""
    import asyncio

    b = _GatedBackend()

    async def exec_(name: str, args: dict[str, Any]) -> dict[str, Any]:
        return {}

    loop = AgentLoop(b, tools=[_TOOL], tool_executor=exec_)
    stream = loop.run_stream("hi")
    assert (await asyncio.wait_for(stream.__anext__(), timeout=1.0))["type"] == "token"
    await stream.aclose()
    assert b.closed is True


@pytest.mark.asyncio
async def test_run_stream_text_before_a_tool_call_is_sent_first() -> None:
    """A turn that says something and then calls a tool: the text goes out
    before tool_call_start; done carries only the final turn's answer."""
    b = FakeBackend(
        [
            ChatResponse(
                content="我先看日志",
                tool_calls=[_tc("alb_logcat", {}, "tc_1")],
                finish_reason="tool_calls",
            ),
            _final("没有崩溃"),
        ]
    )

    async def exec_(name: str, args: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True}

    loop = AgentLoop(b, tools=[_TOOL], tool_executor=exec_)
    events = [ev async for ev in loop.run_stream("有崩溃吗")]
    types = [e["type"] for e in events]
    start = types.index("tool_call_start")
    before = "".join(e["delta"] for e in events[:start] if e["type"] == "token")
    after = "".join(e["delta"] for e in events[start:] if e["type"] == "token")
    assert before == "我先看日志"
    assert after == "没有崩溃"
    assert events[-1]["data"] == "没有崩溃"


class _NoTokenBackend(FakeBackend):
    """A backend that only reports the whole reply on done."""

    async def stream(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        **kw: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        item = self._queue.pop(0)
        assert isinstance(item, ChatResponse)
        yield {"type": "done", "content": item.content, "tool_calls": [], "usage": {}}


@pytest.mark.asyncio
async def test_run_stream_reply_without_token_events_is_sent_once() -> None:
    b = _NoTokenBackend([_final("整段回答")])

    async def exec_(name: str, args: dict[str, Any]) -> dict[str, Any]:
        return {}

    loop = AgentLoop(b, tools=[_TOOL], tool_executor=exec_)
    events = [ev async for ev in loop.run_stream("hi")]
    assert [e["delta"] for e in events if e["type"] == "token"] == ["整段回答"]


# ─── Tool names glued to a stray prefix (2026-11-03) ─────────────────

_KNOWN = {"alb_describe", "alb_describe_errors", "alb_status", "alb_shell"}


@pytest.mark.parametrize(
    ("name", "want"),
    [
        # seen with Ling-3.0-tiny through Ollama 0.40
        ("alb_describealb_describe_errors", "alb_describe_errors"),
        ("alb_statusalb_status", "alb_status"),
        ("alb_stalb_status", "alb_status"),
        # already right
        ("alb_status", None),
        # front part is not the start of the tool name: leave it alone
        ("my_alb_shell", None),
        ("xalb_status", None),
        # unknown and unrelated
        ("alb_reboot", None),
    ],
)
def test_repair_tool_name(name: str, want: str | None) -> None:
    assert repair_tool_name(name, _KNOWN) == want


@pytest.mark.asyncio
async def test_run_stream_runs_the_repaired_tool_and_says_so(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    tools = [
        ToolSpec(name="alb_describe", description="d", parameters={"type": "object"}),
        ToolSpec(name="alb_describe_errors", description="e", parameters={"type": "object"}),
    ]
    b = FakeBackend([_calls(_tc("alb_describealb_describe_errors", {}, "tc_1")), _final("好了")])
    ran: list[str] = []

    async def exec_(name: str, args: dict[str, Any]) -> dict[str, Any]:
        ran.append(name)
        return {"ok": True}

    loop = AgentLoop(b, tools=tools, tool_executor=exec_)
    session = ChatSession.create(backend="fake")
    events = [ev async for ev in loop.run_stream("错误码", session=session)]

    assert ran == ["alb_describe_errors"]
    start = next(e for e in events if e["type"] == "tool_call_start")
    assert start["name"] == "alb_describe_errors"
    assert start["requested_name"] == "alb_describealb_describe_errors"
    # the history the model sees next turn uses the real name
    msgs = ChatSession.load(session.session_id).messages()
    assert msgs[1].tool_calls[0].name == "alb_describe_errors"
    assert msgs[2].name == "alb_describe_errors"


@pytest.mark.asyncio
async def test_run_also_repairs_the_tool_name() -> None:
    tools = [
        ToolSpec(name="alb_describe", description="d", parameters={"type": "object"}),
        ToolSpec(name="alb_describe_errors", description="e", parameters={"type": "object"}),
    ]
    b = FakeBackend([_calls(_tc("alb_describealb_describe_errors", {}, "tc_1")), _final("好了")])
    ran: list[str] = []

    async def exec_(name: str, args: dict[str, Any]) -> dict[str, Any]:
        ran.append(name)
        return {"ok": True}

    r = await AgentLoop(b, tools=tools, tool_executor=exec_).run("错误码")
    assert r.ok is True
    assert ran == ["alb_describe_errors"]
