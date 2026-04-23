"""Tests for /playground/* HTTP + WS endpoints."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from alb.agent.backend import (
    BackendError,
    ChatResponse,
    LLMBackend,
    Message,
    ToolSpec,
)
from alb.api.server import create_app


class _FakeBackend(LLMBackend):
    name = "ollama"  # masquerade as ollama so registry checks pass
    model = "fake-model"
    supports_tool_calls = False
    supports_streaming = True

    def __init__(self, *, reply: str = "hello", **_: Any) -> None:
        self._reply = reply

    async def chat(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        return ChatResponse(
            content=self._reply, finish_reason="stop", model=self.model,
            usage={
                "input_tokens": 4, "output_tokens": 5, "total_tokens": 9,
                "eval_duration_ms": 250, "prompt_eval_duration_ms": 50,
                "total_duration_ms": 300,
            },
            thinking="",
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
        for c in self._reply:
            yield {"type": "token", "delta": c}
        yield {
            "type": "done",
            "content": self._reply,
            "thinking": "",
            "finish_reason": "stop",
            "model": self.model,
            "usage": {
                "input_tokens": 2, "output_tokens": len(self._reply),
                "total_tokens": 2 + len(self._reply),
                "eval_duration_ms": 100, "prompt_eval_duration_ms": 20,
                "total_duration_ms": 120,
            },
        }

    async def list_models(self) -> list[dict[str, Any]]:
        return [{"name": "qwen2.5:3b", "size": 1_500_000_000, "modified_at": "2026-04-23T10:00:00"}]


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "alb.api.playground_route.get_backend",
        lambda name, **kw: _FakeBackend(**kw),
    )
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_list_backends(client) -> None:
    r = client.get("/playground/backends")
    assert r.status_code == 200
    body = r.json()
    names = [b["name"] for b in body["backends"]]
    assert "ollama" in names


def test_list_models_happy_path(client) -> None:
    r = client.get("/playground/backends/ollama/models")
    assert r.status_code == 200
    body = r.json()
    assert body["backend"] == "ollama"
    assert body["models"][0]["name"] == "qwen2.5:3b"


def test_list_models_unknown_backend(client) -> None:
    r = client.get("/playground/backends/no-such-backend/models")
    assert r.status_code == 404


def test_chat_post_happy_path(client) -> None:
    r = client.post("/playground/chat", json={
        "backend": "ollama",
        "messages": [{"role": "user", "content": "hi"}],
        "temperature": 0.5,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["content"] == "hello"
    assert body["metrics"]["tokens_per_second"] == 20.0  # 5 / 0.25s


def test_chat_post_unknown_backend(client) -> None:
    r = client.post("/playground/chat", json={
        "backend": "no-such-backend",
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "UNKNOWN_BACKEND"


def test_chat_post_validation_error(client) -> None:
    # Missing required `messages`
    r = client.post("/playground/chat", json={"backend": "ollama"})
    assert r.status_code == 422


def test_chat_ws_happy_path(client) -> None:
    with client.websocket_connect("/playground/chat/ws") as ws:
        ws.send_json({
            "backend": "ollama",
            "messages": [{"role": "user", "content": "hi"}],
            "temperature": 0.7,
        })
        events: list[dict[str, Any]] = []
        for _ in range(20):
            ev = ws.receive_json()
            events.append(ev)
            if ev.get("type") == "done":
                break
        assert events[-1]["type"] == "done"
        assert events[-1]["ok"] is True
        token_deltas = [e["delta"] for e in events if e["type"] == "token"]
        # _FakeBackend streams char-by-char so we should see "h","e","l","l","o"
        assert "".join(token_deltas) == "hello"
        assert events[-1]["metrics"]["output_tokens"] == 5


def test_chat_ws_invalid_request(client) -> None:
    with client.websocket_connect("/playground/chat/ws") as ws:
        ws.send_json({"backend": "ollama"})  # missing messages
        ev = ws.receive_json()
        assert ev["type"] == "done"
        assert ev["ok"] is False
        assert ev["error"]["code"] == "INVALID_REQUEST"
