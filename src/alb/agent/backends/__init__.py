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
    openai-compat   — any OpenAI-compatible /v1/chat/completions    (M2 planned)
    llama-cpp       — embedded llama.cpp                             (M3 planned)
    anthropic       — Claude API                                      (M3 planned)
"""

from __future__ import annotations

from typing import Any

from alb.agent.backend import LLMBackend

__all__ = ["get_backend"]


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


def _construct(name: str, **kwargs: Any) -> LLMBackend:
    if name == "ollama":
        from alb.agent.backends.ollama import OllamaBackend

        return OllamaBackend(**kwargs)
    if name == "openai-compat":
        raise ValueError("openai-compat backend not yet implemented (M2)")
    if name == "llama-cpp":
        raise ValueError("llama-cpp backend not yet implemented (M3)")
    if name == "anthropic":
        raise ValueError("anthropic backend not yet implemented (M3)")
    raise ValueError(
        f"unknown backend: {name!r}; "
        "known: ollama | openai-compat | llama-cpp | anthropic"
    )
