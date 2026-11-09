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

import inspect
from typing import Any

from alb.agent.backend import ToolSpec
from alb.agent.loop import ToolExecutor

# Registered for MCP clients but not handed to the agent loop: asking the
# model to call another model (raw playground chat) is never what a person
# in the web assistant wants, and its description alone was 1.6k characters
# of every request (2026-11-03).
_AGENT_SKIP = frozenset({"alb_playground_chat"})


async def make_agent_tools() -> tuple[list[ToolSpec], ToolExecutor]:
    """Build (tool_specs, executor) from the live MCP tool registry.

    Returns:
        A pair the caller passes straight to `AgentLoop(..., tools=..., tool_executor=...)`.
    """
    from alb.mcp.server import create_server  # lazy: avoid mcp import until needed

    mcp = create_server()
    raw_tools = await mcp.list_tools()

    # The tool list goes out with every request: 36 tools were ~6.4k tokens,
    # 45 s for a CPU model to read before the first word. The copy for the
    # model is compacted without dropping meaning (dedented docstrings,
    # "string or null" written as "string"); MCP clients keep the originals.
    specs: list[ToolSpec] = []
    schemas: dict[str, dict[str, Any]] = {}
    for t in raw_tools:
        schema = t.inputSchema or {"type": "object", "properties": {}}
        schemas[t.name] = schema
        if t.name in _AGENT_SKIP:
            continue
        specs.append(
            ToolSpec(
                name=t.name,
                description=inspect.cleandoc(t.description or ""),
                parameters=_sanitize_schema(schema),
            )
        )

    async def executor(name: str, args: dict[str, Any]) -> dict[str, Any]:
        args = drop_blank_device(coerce_string_args(schemas.get(name, {}), args))
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


def drop_blank_device(args: dict[str, Any]) -> dict[str, Any]:
    """``device=""`` counts as no device given: the tool runs on the default
    board, exactly as when the model leaves ``device`` out.

    Seen 2026-11-05: asked for the battery level, Ling-3.0-tiny called
    ``alb_battery(device="")``. An empty string is not a serial, so it failed
    the serial check (TOOL_CALL_FAILED) and the model spent two more calls
    finding the serial. Only a blank string is dropped, so a tool still sees
    ``device`` as missing (None) and keeps what it does then — alb_reboot
    retries over the serial console, alb_log_search searches every board.
    With two boards and no ``device``, adb refuses ("more than one
    device/emulator") rather than picking one.
    """
    value = args.get("device")
    if isinstance(value, str) and not value.strip():
        return {k: v for k, v in args.items() if k != "device"}
    return args


def coerce_string_args(schema: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    """Turn a number into a string where the tool only takes a string.

    Seen 2026-11-03: the bench board's serial is all digits (an 8-digit
    number), and Ling-3.0-tiny wrote ``"device": <that number>`` as a JSON
    number.  Tool argument validation does not turn int into str, so every
    alb_shell call failed and the model retried until AGENT_MAX_TURNS_EXCEEDED.
    Only plain numbers are changed, and only for a parameter whose type
    allows a string but no number; everything else is passed through.
    """
    props = schema.get("properties")
    if not isinstance(props, dict):
        return args
    out = dict(args)
    for key, value in args.items():
        if isinstance(value, bool) or not isinstance(value, int | float):
            continue
        prop = props.get(key)
        if isinstance(prop, dict) and _takes_string_not_number(prop):
            out[key] = str(value)
    return out


def _takes_string_not_number(prop: dict[str, Any]) -> bool:
    types: set[Any] = {prop.get("type")}
    for alt in prop.get("anyOf") or []:
        if isinstance(alt, dict):
            types.add(alt.get("type"))
    return "string" in types and not types & {"integer", "number"}


def _sanitize_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """The schema the model sees: no FastMCP `title` keys (Ollama dislikes
    extra keys), and an optional parameter written plainly — FastMCP spells
    `x: str | None = None` as anyOf [string, null] + default null; for the
    model "type: string, not required" says the same in a third of the text.
    Validation still runs against the original schema (FastMCP)."""
    clean = {k: v for k, v in schema.items() if k != "title"}
    props = clean.get("properties")
    if isinstance(props, dict):
        clean["properties"] = {k: _slim_property(v) if isinstance(v, dict) else v for k, v in props.items()}
    return clean


def _slim_property(prop: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in prop.items() if k != "title"}
    alts = out.get("anyOf")
    if isinstance(alts, list) and len(alts) == 2:
        non_null = [a for a in alts if isinstance(a, dict) and a.get("type") != "null"]
        if len(non_null) == 1 and len(alts) - len(non_null) == 1:
            del out["anyOf"]
            for k, v in non_null[0].items():
                if k != "title":
                    out.setdefault(k, v)
    if "default" in out and out["default"] is None:
        del out["default"]
    return out
