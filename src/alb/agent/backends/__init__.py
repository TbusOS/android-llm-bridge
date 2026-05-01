"""Concrete `LLMBackend` implementations.

Each backend lives in its own module and is imported lazily — pulling in
`anthropic` or `llama-cpp-python` just to run the CLI would be wasteful
for users who never touch chat features.

Usage::

    from alb.agent.backends import get_backend
    backend = get_backend("ollama", model="qwen2.5:3b")

Registered backends (check `alb.infra.registry.BACKENDS` for the canonical
list + status):

    ollama          — local HTTP (recommended for CPU-only)          (M2 beta)
    openai-compat   — any OpenAI-compatible /v1/chat/completions     (M2 beta)
    llama-cpp       — embedded llama.cpp                             (M3 planned)
    anthropic       — Claude API                                      (M3 planned)
"""

from __future__ import annotations

import os
from typing import Any

from alb.agent.backend import LLMBackend

__all__ = ["close_probe_cache", "get_backend"]


# Process-lifetime cache for the no-kwargs construction path used by
# `GET /playground/backends/{name}/health` — that endpoint hits us
# every 15 s per backend per dashboard tab, and a fresh instance per
# probe means re-running the (small) constructor + creating fresh
# httpx clients underneath. We only cache the no-kwargs case so chat
# call sites that pass per-call overrides (model / base_url) still get
# a fresh instance with their settings.
_PROBE_CACHE: dict[str, LLMBackend] = {}


def get_backend(name: str, **kwargs: Any) -> LLMBackend:
    """Lazy-import and construct a concrete backend by name.

    Raises `ValueError` on unknown name, `ImportError` on missing
    optional dependency.
    """
    if not kwargs and name in _PROBE_CACHE:
        return _PROBE_CACHE[name]

    backend = _construct(name, **kwargs)

    if not kwargs:
        _PROBE_CACHE[name] = backend
    return backend


async def close_probe_cache() -> None:
    """Close every cached probe-path backend's shared httpx client.

    Called from alb-api FastAPI shutdown lifespan event so the
    DEBT-019 reused clients release their TCP/TLS pools cleanly on
    server stop. Per-CLI invocations (`alb chat ...`) instantiate
    fresh backends with kwargs (no cache hit), so they're unaffected
    — they exit when the process exits.
    """
    for backend in list(_PROBE_CACHE.values()):
        aclose = getattr(backend, "aclose", None)
        if aclose is None:
            continue
        try:
            await aclose()
        except Exception:  # noqa: BLE001 — best-effort shutdown
            # Already-closed / network-failed close shouldn't block
            # the rest of the shutdown sequence.
            pass
    _PROBE_CACHE.clear()


def _construct(name: str, **kwargs: Any) -> LLMBackend:
    if name == "ollama":
        from alb.agent.backends.ollama import OllamaBackend

        # DEBT-020 close: probe-path constructions (no kwargs) honour
        # ALB_OLLAMA_URL / ALB_OLLAMA_MODEL env so dashboard health
        # reflects the actually-configured Ollama, not the manifest
        # default. Same precedence as chat_route.py:
        #   caller kwargs > env > library default.
        env_base = os.environ.get("ALB_OLLAMA_URL")
        env_model = os.environ.get("ALB_OLLAMA_MODEL")
        if env_base and "base_url" not in kwargs:
            kwargs["base_url"] = env_base
        if env_model and "model" not in kwargs:
            kwargs["model"] = env_model
        return OllamaBackend(**kwargs)
    if name == "openai-compat":
        from alb.agent.backends.openai_compat import OpenAICompatBackend

        return OpenAICompatBackend(**kwargs)
    if name == "llama-cpp":
        raise ValueError("llama-cpp backend not yet implemented (M3)")
    if name == "anthropic":
        raise ValueError("anthropic backend not yet implemented (M3)")
    raise ValueError(
        f"unknown backend: {name!r}; "
        "known: ollama | openai-compat | llama-cpp | anthropic"
    )
