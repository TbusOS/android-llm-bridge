"""Tests for /playground/* HTTP + WS endpoints."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from alb.agent.backend import (
    BackendError,
    ChatResponse,
    HealthResult,
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


# ─── /playground/backends/{name}/health (DEBT-017) ─────────────────


def test_health_unknown_backend(client) -> None:
    r = client.get("/playground/backends/no-such-backend/health")
    assert r.status_code == 404


def test_health_planned_backend(client) -> None:
    """`status="planned"` backends short-circuit before construction —
    no daemon ping, just a clear 'not implemented' signal so the UI
    can grey out the card without surfacing an error toast."""
    r = client.get("/playground/backends/anthropic/health")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "anthropic"
    assert body["reachable"] is False
    assert body["reason"] == "not_implemented"
    assert body["latency_ms"] is None
    assert body["model"] is None


def test_health_no_probe_wired(client) -> None:
    """Backends that didn't opt into a real probe (has_health_probe=
    False, the ABC default) short-circuit before health() runs. The
    endpoint surfaces 'no_probe' so the UI can distinguish
    'registered but unprobed' from 'probe says down'."""
    r = client.get("/playground/backends/ollama/health")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "ollama"
    # _FakeBackend doesn't set has_health_probe → endpoint folds it
    # into reason='no_probe' without invoking health().
    assert body["reachable"] is False
    assert body["reason"] == "no_probe"
    assert body["latency_ms"] is None
    assert body["model"] == "fake-model"


def test_health_with_method(client, monkeypatch) -> None:
    """Backend with has_health_probe=True returns a HealthResult; the
    endpoint times the call and forwards reachable / model /
    model_present onto the response."""

    class _BackendWithHealth(_FakeBackend):
        has_health_probe = True

        async def health(self) -> HealthResult:  # type: ignore[override]
            return HealthResult(
                reachable=True,
                model="qwen2.5:3b",
                model_present=True,
            )

    monkeypatch.setattr(
        "alb.api.playground_route.get_backend",
        lambda name, **kw: _BackendWithHealth(**kw),
    )
    r = client.get("/playground/backends/ollama/health")
    assert r.status_code == 200
    body = r.json()
    assert body["reachable"] is True
    assert body["reason"] is None
    assert body["model"] == "qwen2.5:3b"
    assert body["model_present"] is True
    assert isinstance(body["latency_ms"], int) and body["latency_ms"] >= 0


def test_health_probe_failure(client, monkeypatch) -> None:
    """A health() that raises is treated as a probe failure — caller
    sees reachable=false + reason=probe_failed + the error message,
    so the card can render a clear 'down · network' state."""

    class _BackendThatExplodes(_FakeBackend):
        has_health_probe = True

        async def health(self) -> HealthResult:  # type: ignore[override]
            raise RuntimeError("connection refused")

    monkeypatch.setattr(
        "alb.api.playground_route.get_backend",
        lambda name, **kw: _BackendThatExplodes(**kw),
    )
    r = client.get("/playground/backends/ollama/health")
    assert r.status_code == 200
    body = r.json()
    assert body["reachable"] is False
    assert body["reason"] == "probe_failed"
    assert "connection refused" in body["error"]


def test_health_probe_timeout(client, monkeypatch) -> None:
    """A health() that exceeds the endpoint deadline is reported as
    probe_timeout so the UI can show a clear stall reason instead of
    waiting alongside the upstream socket."""
    import asyncio

    class _BackendThatStalls(_FakeBackend):
        has_health_probe = True

        async def health(self) -> HealthResult:  # type: ignore[override]
            await asyncio.sleep(60)  # never returns within deadline
            return HealthResult(reachable=True)

    monkeypatch.setattr(
        "alb.api.playground_route.get_backend",
        lambda name, **kw: _BackendThatStalls(**kw),
    )
    # Shorten the deadline to keep the test fast — the endpoint reads
    # this module-level constant, so monkeypatch is enough.
    monkeypatch.setattr(
        "alb.api.playground_route._HEALTH_PROBE_DEADLINE_S", 0.05
    )
    r = client.get("/playground/backends/ollama/health")
    assert r.status_code == 200
    body = r.json()
    assert body["reachable"] is False
    assert body["reason"] == "probe_timeout"


def test_health_abc_default_raises() -> None:
    """The ABC default health() must raise NotImplementedError — the
    'forgot to wire up the probe' error path is intentionally loud
    so the gate (has_health_probe) stays the canonical capability
    advertise. Direct unit test (no HTTP)."""
    import asyncio

    class _NoProbeBackend(LLMBackend):
        name = "test"
        model = "fake"
        supports_tool_calls = False

        async def chat(self, messages, **kwargs):  # type: ignore[override]
            return ChatResponse(content="ok", model=self.model)

    b = _NoProbeBackend()
    with pytest.raises(NotImplementedError):
        asyncio.get_event_loop().run_until_complete(b.health())


def test_health_init_failure(client, monkeypatch) -> None:
    """If construction itself raises (missing dep / bad config) we
    surface reason='init_failed' with the error so the user sees why
    the backend was registered but unreachable."""

    def _bad_factory(name: str, **kw: Any) -> LLMBackend:
        raise ImportError("anthropic SDK not installed")

    monkeypatch.setattr("alb.api.playground_route.get_backend", _bad_factory)
    r = client.get("/playground/backends/ollama/health")
    assert r.status_code == 200
    body = r.json()
    assert body["reachable"] is False
    assert body["reason"] == "init_failed"
    assert "anthropic SDK not installed" in body["error"]


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
