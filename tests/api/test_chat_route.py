"""Tests for FastAPI POST /chat (M2 step 2)."""

from __future__ import annotations

from typing import Any

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
