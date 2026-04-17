"""Bridge from MCP tool registry to AgentLoop's (ToolSpec, ToolExecutor) pair.

The `alb chat` CLI and FastAPI `POST /chat` both run a local `AgentLoop`
powered by `OllamaBackend` (or any other `LLMBackend`). The loop needs:

    1. A list of `ToolSpec` — JSON-schema descriptions handed to the LLM
       so it knows what tools exist and how to call them.
    2. A `ToolExecutor` — an async callable `(name, args) -> dict` that
       actually runs a tool and returns a serialisable result.

Rather than maintain a second tool definition, we reuse the MCP server's
registered tools (same 28 tools that external MCP clients like Claude Code
see).  This file is that bridge.

Design:
    - FastMCP is imported lazily (matches `alb.mcp.server`), so `alb chat`
      doesn't pay the cost when run with a non-MCP backend.
    - The server is built once per chat session and kept alive for reuse
      across turns — avoiding the ~100ms tool registration cost per turn.
    - FastMCP 1.x `call_tool()` returns `(list[Content], structured_dict)`;
      we take the structured dict directly (already shaped as our standard
      `{ok, data, error, artifacts, timing_ms}` envelope).
"""

from __future__ import annotations

from typing import Any

from alb.agent.backend import ToolSpec
from alb.agent.loop import ToolExecutor


async def make_agent_tools() -> tuple[list[ToolSpec], ToolExecutor]:
    """Build (tool_specs, executor) from the live MCP tool registry.

    Returns:
        A pair the caller passes straight to `AgentLoop(..., tools=..., tool_executor=...)`.
    """
    from alb.mcp.server import create_server  # lazy: avoid mcp import until needed

    mcp = create_server()
    raw_tools = await mcp.list_tools()

    specs: list[ToolSpec] = []
    for t in raw_tools:
        schema = t.inputSchema or {"type": "object", "properties": {}}
        specs.append(
            ToolSpec(
                name=t.name,
                description=(t.description or "").strip(),
                parameters=_sanitize_schema(schema),
            )
        )

    async def executor(name: str, args: dict[str, Any]) -> dict[str, Any]:
        try:
            _, structured = await mcp.call_tool(name, args)
        except Exception as e:  # noqa: BLE001 — executor must never crash AgentLoop
            return {
                "ok": False,
                "error": {
                    "code": "TOOL_CALL_FAILED",
                    "message": f"{type(e).__name__}: {e}",
                    "suggestion": "check tool args match JSON schema, or inspect alb logs",
                },
            }
        # FastMCP serialises dict return values directly; `structured` is the payload.
        if isinstance(structured, dict):
            return structured
        # Fallback: unexpected shape — wrap so AgentLoop still gets a dict
        return {"ok": True, "data": structured}

    return specs, executor


def _sanitize_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Strip FastMCP's `title` key — Ollama dislikes extra keys in tool schemas."""
    clean = {k: v for k, v in schema.items() if k != "title"}
    # recurse into properties for nested title removal
    props = clean.get("properties")
    if isinstance(props, dict):
        clean["properties"] = {
            k: {kk: vv for kk, vv in v.items() if kk != "title"} if isinstance(v, dict) else v
            for k, v in props.items()
        }
    return clean
