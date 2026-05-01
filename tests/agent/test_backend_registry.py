"""Tests for the agent backend registry helpers (`alb.agent.backends`)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from alb.agent.backend import ChatResponse, LLMBackend, Message
from alb.agent.backends import (
    _PROBE_CACHE,
    close_probe_cache,
    get_backend,
)


@pytest.fixture(autouse=True)
def _reset_cache():
    _PROBE_CACHE.clear()
    yield
    _PROBE_CACHE.clear()


def test_get_backend_caches_no_kwargs_path() -> None:
    """Calling get_backend('ollama') twice returns the same instance —
    the probe-cache hit avoids paying constructor cost on every
    /playground/backends/{name}/health request."""
    a = get_backend("ollama")
    b = get_backend("ollama")
    assert a is b
    assert _PROBE_CACHE["ollama"] is a


def test_get_backend_does_not_cache_when_kwargs_present() -> None:
    """CLI sessions pass --model / --base-url, which would pollute the
    probe cache if we cached them; verify those paths bypass cache."""
    a = get_backend("ollama")  # cached
    b = get_backend("ollama", model="qwen2.5:3b")  # not cached
    assert a is not b
    # The cached instance must be the no-kwargs one
    assert _PROBE_CACHE["ollama"] is a


def test_get_backend_dispatches_openai_compat() -> None:
    """M3 step 1 wired openai-compat — this used to ValueError."""
    b = get_backend("openai-compat")
    from alb.agent.backends.openai_compat import OpenAICompatBackend

    assert isinstance(b, OpenAICompatBackend)


def test_get_backend_ollama_reads_env_overrides(monkeypatch) -> None:
    """DEBT-020: probe-path construction must honour ALB_OLLAMA_URL /
    ALB_OLLAMA_MODEL so the dashboard health card reflects the actually
    -configured Ollama, not the manifest default."""
    monkeypatch.setenv("ALB_OLLAMA_URL", "http://example.test:11434")
    monkeypatch.setenv("ALB_OLLAMA_MODEL", "qwen2.5:7b")
    b = get_backend("ollama")
    assert b.base_url == "http://example.test:11434"
    assert b.model == "qwen2.5:7b"


def test_get_backend_ollama_caller_kwargs_beat_env(monkeypatch) -> None:
    """Caller-passed kwargs win over env (mirrors chat_route precedence)."""
    monkeypatch.setenv("ALB_OLLAMA_URL", "http://from-env:11434")
    monkeypatch.setenv("ALB_OLLAMA_MODEL", "from-env-model")
    b = get_backend("ollama", base_url="http://from-kwarg:11434", model="from-kwarg-model")
    assert b.base_url == "http://from-kwarg:11434"
    assert b.model == "from-kwarg-model"


def test_get_backend_ollama_no_env_uses_default(monkeypatch) -> None:
    """No env set → library defaults preserved (back-compat)."""
    monkeypatch.delenv("ALB_OLLAMA_URL", raising=False)
    monkeypatch.delenv("ALB_OLLAMA_MODEL", raising=False)
    b = get_backend("ollama")
    # OllamaBackend defaults are an implementation detail, but they
    # must NOT be 'http://from-env...' or any env-injected value.
    assert "from-env" not in b.base_url
    assert "from-env" not in b.model


@pytest.mark.asyncio
async def test_close_probe_cache_calls_aclose_and_clears() -> None:
    """The alb-api shutdown lifespan calls close_probe_cache —
    verify it (a) calls aclose() on each cached backend (b) clears
    the cache so the next request gets a fresh client."""
    aclose_count = {"n": 0}

    class _SpyBackend(LLMBackend):
        name = "ollama"
        model = "spy"
        supports_tool_calls = False

        async def chat(self, messages, **kwargs):  # type: ignore[override]
            return ChatResponse(content="ok", model=self.model)

        async def aclose(self) -> None:
            aclose_count["n"] += 1

    _PROBE_CACHE["ollama"] = _SpyBackend()
    _PROBE_CACHE["openai-compat"] = _SpyBackend()

    await close_probe_cache()

    assert aclose_count["n"] == 2
    assert _PROBE_CACHE == {}


@pytest.mark.asyncio
async def test_close_probe_cache_swallows_aclose_errors() -> None:
    """One backend's aclose() blowing up shouldn't block the rest of
    the shutdown sequence."""

    class _BadCloser(LLMBackend):
        name = "ollama"
        model = "x"
        supports_tool_calls = False

        async def chat(self, messages, **kwargs):  # type: ignore[override]
            return ChatResponse(content="ok", model=self.model)

        async def aclose(self) -> None:
            raise RuntimeError("close failed")

    closed = {"good": False}

    class _GoodCloser(LLMBackend):
        name = "openai-compat"
        model = "y"
        supports_tool_calls = False

        async def chat(self, messages, **kwargs):  # type: ignore[override]
            return ChatResponse(content="ok", model=self.model)

        async def aclose(self) -> None:
            closed["good"] = True

    _PROBE_CACHE["bad"] = _BadCloser()
    _PROBE_CACHE["good"] = _GoodCloser()

    # Must not raise even though _BadCloser.aclose() does.
    await close_probe_cache()
    assert closed["good"] is True
    assert _PROBE_CACHE == {}


@pytest.mark.asyncio
async def test_close_probe_cache_skips_backends_without_aclose() -> None:
    """Older backends (or test fixtures) without aclose() must be
    skipped silently — close_probe_cache should not crash on them."""

    class _NoCloseBackend(LLMBackend):
        name = "ollama"
        model = "x"
        supports_tool_calls = False

        async def chat(self, messages, **kwargs):  # type: ignore[override]
            return ChatResponse(content="ok", model=self.model)

    _PROBE_CACHE["ollama"] = _NoCloseBackend()
    await close_probe_cache()
    assert _PROBE_CACHE == {}
