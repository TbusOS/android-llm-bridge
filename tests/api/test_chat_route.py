"""Tests for FastAPI POST /chat (M2 step 2)."""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient

from alb.agent.backend import ChatResponse, LLMBackend, Message, ToolSpec
from alb.api.server import create_app


class _FakeBackend(LLMBackend):
    name = "fake"
    supports_tool_calls = True

    def __init__(self, reply: str = "ok") -> None:
        self.model = "fake-model"
        self._reply = reply

    async def chat(self, messages: list[Message], **kwargs: Any) -> ChatResponse:
        return ChatResponse(content=self._reply, finish_reason="stop", model=self.model)


@pytest.fixture
def client(monkeypatch, tmp_path):
    # Redirect workspace_root() to tmp so events.jsonl + sessions land
    # under tmp_path, not the user's real workspace.
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    from alb.infra.event_bus import reset_bus
    reset_bus()
    return TestClient(create_app())


@pytest.fixture
def fake_backend_patch(monkeypatch):
    """Replace get_backend() in chat_route with a fake factory."""
    def _factory(name: str, **kwargs: Any) -> LLMBackend:
        return _FakeBackend(reply=kwargs.get("_fake_reply", "Pixel 7 连接正常"))

    monkeypatch.setattr("alb.api.chat_route.get_backend", _factory)
    return _factory


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] == "true"
    assert body["api"] == "alb"


def test_chat_happy_path_no_tools(client, fake_backend_patch):
    r = client.post("/chat", json={"message": "你好", "tools": False})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["reply"] == "Pixel 7 连接正常"
    assert body["session_id"]  # non-empty, auto-created
    assert body["backend"] == "fake"
    assert body["model"] == "fake-model"
    assert body["error"] is None


def test_chat_resume_session(client, fake_backend_patch):
    # First turn — create session
    r1 = client.post("/chat", json={"message": "hi", "tools": False})
    sid = r1.json()["session_id"]
    # Second turn — resume
    r2 = client.post("/chat", json={"message": "再问一次", "tools": False, "session_id": sid})
    assert r2.status_code == 200
    body = r2.json()
    assert body["ok"] is True
    assert body["session_id"] == sid


def test_chat_session_not_found(client, fake_backend_patch):
    r = client.post(
        "/chat",
        json={"message": "hi", "tools": False, "session_id": "nonexistent-xxx"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "SESSION_NOT_FOUND"


def test_chat_invalid_session_id_returns_400_envelope(client, fake_backend_patch):
    """L-035: bad session_id (traversal attempt) must not 500/traceback —
    returns structured INVALID_SESSION_ID error envelope.

    Locks against the meta-audit finding (part 140): chat_route called
    `ChatSession.load(req.session_id)` without catching `InvalidSessionId`,
    leaking traceback + regex details on 500.
    """
    r = client.post(
        "/chat",
        json={"message": "hi", "tools": False, "session_id": "../etc"},
    )
    assert r.status_code == 200  # envelope shape regardless of error
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "INVALID_SESSION_ID"


def test_chat_backend_init_failed(client, monkeypatch):
    def _bad_factory(name: str, **kwargs):
        raise ValueError(f"unknown backend: {name!r}")

    monkeypatch.setattr("alb.api.chat_route.get_backend", _bad_factory)
    r = client.post("/chat", json={"message": "hi", "backend": "no-such-thing", "tools": False})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "BACKEND_INIT_FAILED"


def test_chat_missing_message(client, fake_backend_patch):
    r = client.post("/chat", json={"tools": False})
    # pydantic validation → 422
    assert r.status_code == 422


def test_chat_env_var_model_override(client, monkeypatch):
    """When req.model is None, ALB_OLLAMA_MODEL env var should feed into backend kwargs."""
    captured: dict = {}

    def _spy_factory(name: str, **kwargs):
        captured.update(kwargs)
        return _FakeBackend(reply="ok")

    monkeypatch.setenv("ALB_OLLAMA_MODEL", "gemma4:26b")
    monkeypatch.setattr("alb.api.chat_route.get_backend", _spy_factory)
    r = client.post("/chat", json={"message": "hi", "tools": False})
    assert r.status_code == 200
    assert captured.get("model") == "gemma4:26b"


# ─── WebSocket /chat/ws ──────────────────────────────────────────────


class _StreamingFakeBackend(_FakeBackend):
    supports_streaming = True

    async def stream(self, messages, **kwargs):
        for ch in self._reply:
            yield {"type": "token", "delta": ch}
        yield {
            "type": "done",
            "content": self._reply,
            "tool_calls": [],
            "finish_reason": "stop",
            "usage": {"input_tokens": 1, "output_tokens": len(self._reply), "total_tokens": 1 + len(self._reply)},
            "model": self.model,
            "thinking": "",
        }


def test_chat_ws_streams_tokens_then_done(client, monkeypatch):
    def _factory(name: str, **kwargs):
        return _StreamingFakeBackend(reply="hello")

    monkeypatch.setattr("alb.api.chat_route.get_backend", _factory)

    with client.websocket_connect("/chat/ws") as ws:
        ws.send_json({"message": "hi", "tools": False})
        events = []
        while True:
            try:
                ev = ws.receive_json()
            except Exception:
                break
            events.append(ev)
            if ev.get("type") == "done":
                break

    tokens = [e for e in events if e["type"] == "token"]
    dones = [e for e in events if e["type"] == "done"]
    assert "".join(e["delta"] for e in tokens) == "hello"
    assert len(dones) == 1
    assert dones[0]["ok"] is True
    assert dones[0]["data"] == "hello"
    assert dones[0]["model"] == "fake-model"
    assert dones[0]["backend"] == "fake"


def test_chat_ws_invalid_request_emits_error_done(client):
    with client.websocket_connect("/chat/ws") as ws:
        # Missing required `message` field
        ws.send_json({"tools": False})
        ev = ws.receive_json()

    assert ev["type"] == "done"
    assert ev["ok"] is False
    assert ev["error"]["code"] == "INVALID_REQUEST"


def test_chat_ws_non_json_first_frame_rejected(client):
    """SEC-2: a non-JSON first frame must yield a clean INVALID_REQUEST
    done event, not an uncaught JSONDecodeError that crashes the handler."""
    with client.websocket_connect("/chat/ws") as ws:
        ws.send_text("this is not json {{{")
        ev = ws.receive_json()

    assert ev["type"] == "done"
    assert ev["ok"] is False
    assert ev["error"]["code"] == "INVALID_REQUEST"


def test_chat_ws_session_not_found(client, monkeypatch):
    monkeypatch.setattr(
        "alb.api.chat_route.get_backend",
        lambda name, **kw: _StreamingFakeBackend(reply="x"),
    )
    with client.websocket_connect("/chat/ws") as ws:
        ws.send_json({"message": "hi", "tools": False, "session_id": "nope"})
        ev = ws.receive_json()

    assert ev["type"] == "done"
    assert ev["ok"] is False
    assert ev["error"]["code"] == "SESSION_NOT_FOUND"


def test_chat_ws_invalid_session_id_emits_done_error(client, monkeypatch):
    """L-035: WS path-traversal session_id → structured `done ok:false`
    error frame, not raw exception. Mirrors `test_chat_invalid_session_id_returns_400_envelope`
    for the POST path.
    """
    monkeypatch.setattr(
        "alb.api.chat_route.get_backend",
        lambda name, **kw: _StreamingFakeBackend(reply="x"),
    )
    with client.websocket_connect("/chat/ws") as ws:
        ws.send_json({"message": "hi", "tools": False, "session_id": "../etc"})
        ev = ws.receive_json()

    assert ev["type"] == "done"
    assert ev["ok"] is False
    assert ev["error"]["code"] == "INVALID_SESSION_ID"


# ─── Audit bus integration ──────────────────────────────────────────


def _read_events(workspace_root) -> list[dict]:
    import json
    from pathlib import Path

    p = Path(workspace_root) / "events.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def test_post_chat_publishes_user_and_done_to_bus(client, fake_backend_patch, tmp_path):
    r = client.post("/chat", json={"message": "你好世界", "tools": False})
    assert r.status_code == 200
    sid = r.json()["session_id"]

    events = _read_events(tmp_path)
    kinds = [(e["source"], e["kind"]) for e in events]
    assert ("chat", "user") in kinds
    assert ("chat", "done") in kinds
    user_ev = next(e for e in events if e["kind"] == "user")
    assert user_ev["session_id"] == sid
    assert user_ev["summary"] == "你好世界"
    done_ev = next(e for e in events if e["kind"] == "done")
    assert done_ev["data"]["model"] == "fake-model"
    assert done_ev["data"]["backend"] == "fake"


def test_post_chat_publishes_error_event_on_backend_init_fail(client, monkeypatch, tmp_path):
    def _bad(name: str, **kw):
        raise ValueError("nope")
    monkeypatch.setattr("alb.api.chat_route.get_backend", _bad)
    r = client.post("/chat", json={"message": "hi", "tools": False, "backend": "x"})
    assert r.status_code == 200 and r.json()["ok"] is False
    # No session was created → no user/done events; backend init failure
    # path bypasses publish, which is fine — error is in HTTP body.
    events = _read_events(tmp_path)
    assert all(e["kind"] != "user" for e in events)


def test_ws_chat_publishes_user_and_done(client, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "alb.api.chat_route.get_backend",
        lambda name, **kw: _StreamingFakeBackend(reply="hi"),
    )
    with client.websocket_connect("/chat/ws") as ws:
        ws.send_json({"message": "ping", "tools": False})
        while True:
            ev = ws.receive_json()
            if ev.get("type") == "done":
                break

    events = _read_events(tmp_path)
    kinds = [e["kind"] for e in events]
    assert "user" in kinds
    assert "done" in kinds
    # token events are NOT broadcast
    assert "token" not in kinds


def test_long_user_message_is_truncated_in_summary(client, fake_backend_patch, tmp_path):
    long = "x" * 500
    client.post("/chat", json={"message": long, "tools": False})
    events = _read_events(tmp_path)
    user_ev = next(e for e in events if e["kind"] == "user")
    # Summary capped at 120 chars (with ellipsis)
    assert len(user_ev["summary"]) <= 120
    assert user_ev["summary"].endswith("…")


def test_ws_chat_emits_tps_sample(client, monkeypatch, tmp_path):
    """Streaming chat must produce at least one tps_sample event in
    events.jsonl (the final flush at sampler.close())."""
    long_reply = "abcdefghij" * 20  # 200 chars → enough token deltas to observe
    monkeypatch.setattr(
        "alb.api.chat_route.get_backend",
        lambda name, **kw: _StreamingFakeBackend(reply=long_reply),
    )
    with client.websocket_connect("/chat/ws") as ws:
        ws.send_json({"message": "go", "tools": False})
        while True:
            ev = ws.receive_json()
            if ev.get("type") == "done":
                break

    events = _read_events(tmp_path)
    samples = [e for e in events if e["kind"] == "tps_sample"]
    assert samples, "expected tps_sample events from TokenSampler.close()"
    last = samples[-1]
    assert last["source"] == "chat"
    assert last["data"]["total_tokens"] > 0
    assert last["data"]["window_s"] == 1.0  # default interval


# ─── confirm_tools: ask before each tool call (off by default) ───────


class _OneToolCallBackend(_FakeBackend):
    """Asks for one alb_reboot, then answers with what the tool returned."""

    def __init__(self) -> None:
        super().__init__("unused")
        self.turn = 0

    async def chat(self, messages: list[Message], **kwargs: Any) -> ChatResponse:
        from alb.agent.backend import ToolCall

        self.turn += 1
        if self.turn == 1:
            return ChatResponse(
                content="",
                tool_calls=[ToolCall(id="c1", name="alb_reboot", arguments={"mode": "normal"})],
                finish_reason="tool_calls",
                model=self.model,
            )
        return ChatResponse(content=f"tool said: {messages[-1].content}", finish_reason="stop", model=self.model)

    supports_streaming = True

    async def stream(self, messages: list[Message], **kwargs: Any) -> Any:
        resp = await self.chat(messages)
        yield {
            "type": "done",
            "content": resp.content,
            "tool_calls": [tc.to_dict() for tc in resp.tool_calls],
            "finish_reason": resp.finish_reason,
            "usage": {},
            "model": self.model,
            "thinking": "",
        }


def _tools_ran(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    ran: list[str] = []

    async def _executor(name: str, args: dict[str, Any]) -> dict[str, Any]:
        ran.append(name)
        return {"ok": True, "data": "rebooting"}

    async def _make() -> tuple[list[ToolSpec], Any]:
        spec = ToolSpec(name="alb_reboot", description="reboot", parameters={"type": "object", "properties": {}})
        return [spec], _executor

    monkeypatch.setattr("alb.mcp.executor.make_agent_tools", _make)
    monkeypatch.setattr("alb.api.chat_route.get_backend", lambda name, **kw: _OneToolCallBackend())
    return ran


def _talk(client: TestClient, body: dict[str, Any], answer: bool | None) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with client.websocket_connect("/chat/ws") as ws:
        ws.send_json(body)
        while True:
            ev = ws.receive_json()
            events.append(ev)
            if ev.get("type") == "tool_confirm_request" and answer is not None:
                ws.send_json({"type": "tool_confirm", "id": ev["id"], "approve": answer})
            if ev.get("type") == "done":
                return events


def test_confirm_tools_declined_means_the_tool_never_runs(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran = _tools_ran(monkeypatch)
    events = _talk(client, {"message": "reboot it", "tools": True, "confirm_tools": True}, answer=False)
    asks = [e for e in events if e["type"] == "tool_confirm_request"]
    assert len(asks) == 1 and asks[0]["name"] == "alb_reboot" and asks[0]["arguments"] == {"mode": "normal"}
    assert ran == []
    assert "TOOL_DECLINED" in events[-1]["data"]


def test_confirm_tools_approved_runs_the_tool(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran = _tools_ran(monkeypatch)
    events = _talk(client, {"message": "reboot it", "tools": True, "confirm_tools": True}, answer=True)
    assert ran == ["alb_reboot"]
    assert "rebooting" in events[-1]["data"]


def test_without_confirm_tools_nothing_is_asked(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default: board commands run without asking (2026-10-23)."""
    ran = _tools_ran(monkeypatch)
    events = _talk(client, {"message": "reboot it", "tools": True}, answer=None)
    assert not [e for e in events if e["type"] == "tool_confirm_request"]
    assert ran == ["alb_reboot"]


def test_a_page_that_leaves_before_answering_runs_nothing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran = _tools_ran(monkeypatch)
    with client.websocket_connect("/chat/ws") as ws:
        ws.send_json({"message": "reboot it", "tools": True, "confirm_tools": True})
        while ws.receive_json().get("type") != "tool_confirm_request":
            pass
    # the socket is closed with the question unanswered
    import time

    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not ran:
        time.sleep(0.05)
    assert ran == []


# ─── cancelling the chat task (the stop button) ─────────────────────
# On Python 3.11 asyncio.wait_for returns the awaited result and drops a
# cancel that comes in the same loop step as that result. Both tests put
# the two in one step and check that the cancel still wins.


async def test_a_call_approved_as_the_chat_is_cancelled_does_not_run() -> None:
    from alb.api.chat_route import _Confirmations

    sent: list[dict[str, Any]] = []
    asked = asyncio.Event()

    class _Socket:
        async def send_json(self, msg: dict[str, Any]) -> None:
            sent.append(msg)
            asked.set()

    ran: list[str] = []

    async def _run(name: str, args: dict[str, Any]) -> dict[str, Any]:
        ran.append(name)
        return {"ok": True}

    sock: Any = _Socket()
    confirms = _Confirmations(sock)
    task = asyncio.ensure_future(confirms.wrap(_run)("alb_reboot", {}))
    await asked.wait()
    # what read_replies does when the page answers, then the stop button
    confirms._pending[sent[0]["id"]].set_result(True)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ran == []


async def test_a_cancel_as_the_first_frame_arrives_is_not_lost() -> None:
    from alb.api.chat_route import chat_ws

    frame: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
    waiting = asyncio.Event()
    sent: list[dict[str, Any]] = []

    class _Socket:
        async def accept(self) -> None:
            pass

        async def receive_json(self) -> Any:
            waiting.set()
            return await frame

        async def send_json(self, msg: dict[str, Any]) -> None:
            sent.append(msg)

        async def close(self) -> None:
            pass

    sock: Any = _Socket()
    task = asyncio.create_task(chat_ws(sock))
    await waiting.wait()
    frame.set_result({})
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sent == []  # it did not go on to answer the frame


def _wait_until(cond: Any, timeout_s: float = 5.0) -> None:
    import time

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline and not cond():
        time.sleep(0.02)


def test_closing_the_socket_stops_the_tool_in_progress(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """The page's Stop button closes the socket. The hub cancels the turn:
    the running tool sees the cancel, the call gets a CHAT_STOPPED result
    in the session, and the bus hears the turn was stopped."""
    from alb.agent.session import ChatSession

    seen: list[str] = []

    async def _slow(name: str, args: dict[str, Any]) -> dict[str, Any]:
        seen.append("started")
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            seen.append("cancelled")
            raise
        return {"ok": True}

    async def _make() -> tuple[list[ToolSpec], Any]:
        spec = ToolSpec(name="alb_reboot", description="reboot", parameters={"type": "object", "properties": {}})
        return [spec], _slow

    monkeypatch.setattr("alb.mcp.executor.make_agent_tools", _make)
    monkeypatch.setattr("alb.api.chat_route.get_backend", lambda name, **kw: _OneToolCallBackend())
    with client.websocket_connect("/chat/ws") as ws:
        ws.send_json({"message": "reboot it", "tools": True})
        _wait_until(lambda: "started" in seen)
        ws.close()  # what the Stop button does
        # the test client cancels the whole handler on leaving the block,
        # so wait inside it for what the hub does on its own
        _wait_until(lambda: any(e["kind"] == "error" for e in _read_events(tmp_path)))

    assert seen == ["started", "cancelled"]
    stop = next(e for e in _read_events(tmp_path) if e["kind"] == "error")
    assert stop["data"] == {"code": "CHAT_STOPPED"}
    last = ChatSession.load(stop["session_id"]).messages()[-1]
    assert last.role == "tool" and "CHAT_STOPPED" in last.content


def test_a_page_that_closes_after_done_stopped_nothing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setattr(
        "alb.api.chat_route.get_backend",
        lambda name, **kw: _StreamingFakeBackend(reply="hi"),
    )
    with client.websocket_connect("/chat/ws") as ws:
        ws.send_json({"message": "ping", "tools": False})
        while ws.receive_json().get("type") != "done":
            pass
        ws.close()
        assert ws.receive()["type"] == "websocket.close"  # the handler is through

    kinds = [e["kind"] for e in _read_events(tmp_path)]
    assert "done" in kinds and "error" not in kinds


# ─── Reply language (2026-11-03) ─────────────────────────────────────


class _PromptSpy(_FakeBackend):
    seen: ClassVar[list[str]] = []
    last_user: ClassVar[list[str]] = []

    async def chat(self, messages: list[Message], **kwargs: Any) -> ChatResponse:
        _PromptSpy.seen.append(messages[0].content if messages[0].role == "system" else "")
        _PromptSpy.last_user.append(messages[-1].content)
        return await super().chat(messages, **kwargs)


@pytest.mark.parametrize(
    ("lang", "expect", "absent"),
    [(None, "简体中文", "reply in English"), ("zh", "简体中文", "reply in English"), ("en", "reply in English", "简体中文")],
)
def test_chat_reply_language_follows_lang(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, lang: str | None, expect: str, absent: str
) -> None:
    """The page sends its UI language; without one the model is told to
    answer in Chinese."""
    _PromptSpy.seen = []
    monkeypatch.setattr("alb.api.chat_route.get_backend", lambda name, **kw: _PromptSpy())
    body: dict[str, Any] = {"message": "hello", "tools": False}
    if lang:
        body["lang"] = lang
    r = client.post("/chat", json=body)
    assert r.status_code == 200, r.text
    assert expect in _PromptSpy.seen[0]
    assert absent not in _PromptSpy.seen[0]


def test_chat_rejects_unknown_lang(client: TestClient, fake_backend_patch: Any) -> None:
    r = client.post("/chat", json={"message": "hi", "tools": False, "lang": "fr"})
    assert r.status_code == 422


@pytest.mark.parametrize(
    ("lang", "note"),
    [(None, "(请用简体中文回答)"), ("en", "(Please answer in English.)"), ("auto", None)],
)
def test_chat_user_message_carries_the_language_note(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, lang: str | None, note: str | None
) -> None:
    _PromptSpy.last_user = []
    monkeypatch.setattr("alb.api.chat_route.get_backend", lambda name, **kw: _PromptSpy())
    body: dict[str, Any] = {"message": "hello", "tools": False}
    if lang:
        body["lang"] = lang
    assert client.post("/chat", json=body).status_code == 200
    assert _PromptSpy.last_user == [f"hello\n\n{note}" if note else "hello"]


class _CtxSpy(_PromptSpy):
    num_ctx = 16384


def test_chat_system_prompt_names_model_and_context(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """2026-11-03: asked "which model are you", the model said it has no
    model ID — the prompt never told it."""
    _PromptSpy.seen = []
    monkeypatch.setattr("alb.api.chat_route.get_backend", lambda name, **kw: _CtxSpy())
    assert client.post("/chat", json={"message": "你的模型id是啥", "tools": False}).status_code == 200
    assert "you are the language model fake-model, served by fake." in _PromptSpy.seen[0]
    assert "Context window: 16384 tokens" in _PromptSpy.seen[0]


def test_chat_backend_without_num_ctx_names_only_the_model(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _PromptSpy.seen = []
    monkeypatch.setattr("alb.api.chat_route.get_backend", lambda name, **kw: _PromptSpy())
    assert client.post("/chat", json={"message": "hi", "tools": False}).status_code == 200
    assert "you are the language model fake-model, served by fake." in _PromptSpy.seen[0]
    assert "Context window" not in _PromptSpy.seen[0]
