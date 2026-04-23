"""MCP tool: alb_playground_chat — direct LLM chat with full sampling control."""

from __future__ import annotations

from typing import Any

from alb.agent.backends import get_backend
from alb.agent.playground import PlaygroundParams, playground_chat


def register(mcp) -> None:  # noqa: ANN001
    @mcp.tool()
    async def alb_playground_chat(
        message: str,
        backend: str = "ollama",
        model: str | None = None,
        base_url: str | None = None,
        system: str | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        repeat_penalty: float | None = None,
        seed: int | None = None,
        num_ctx: int | None = None,
        num_predict: int | None = None,
        think: bool | None = None,
    ) -> dict[str, Any]:
        """Send a single prompt to a local model with full sampling control.

        Bypasses the agent loop — no tool calls, no auto-retry. Useful when
        you want to test how a different model / temperature / system
        prompt would respond, without running real device tools.

        Returns:
            { ok, content, thinking, finish_reason, model, backend,
              metrics: {tokens_per_second, eval_duration_ms, ...},
              error: {code, message, suggestion} | null }
        """
        backend_kwargs: dict[str, Any] = {}
        if model:
            backend_kwargs["model"] = model
        if base_url:
            backend_kwargs["base_url"] = base_url
        try:
            b = get_backend(backend, **backend_kwargs)
        except (ValueError, ImportError) as e:
            return {
                "ok": False,
                "error": {
                    "code": "BACKEND_NOT_IMPLEMENTED",
                    "message": str(e),
                    "suggestion": "use backend='ollama' (the only implemented one)",
                },
            }
        params = PlaygroundParams(
            temperature=temperature, top_p=top_p, top_k=top_k,
            repeat_penalty=repeat_penalty, seed=seed,
            num_ctx=num_ctx, num_predict=num_predict, think=think,
        )
        result = await playground_chat(
            b, [{"role": "user", "content": message}],
            params=params, system=system,
        )
        return result.to_dict()
