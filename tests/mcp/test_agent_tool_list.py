"""The tool list the agent loop hands the model (2026-11-03).

It goes out with every request; on a CPU model the 36-tool list was ~6.4k
tokens and 45 s to read cold. The copy for the model is compacted; MCP
clients keep the original descriptions and schemas.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from alb.mcp.executor import _slim_property, make_agent_tools
from alb.mcp.server import create_server as _create_server

# create_server is untyped; called through Any so mypy accepts it in typed tests
create_server: Any = _create_server


@pytest.mark.asyncio
async def test_playground_chat_is_not_offered_to_the_model() -> None:
    specs, _ = await make_agent_tools()
    names = {s.name for s in specs}
    assert "alb_playground_chat" not in names
    assert {"alb_shell", "alb_logcat", "alb_status"} <= names
    # MCP clients still have it
    assert "alb_playground_chat" in {t.name for t in await create_server().list_tools()}


@pytest.mark.asyncio
async def test_descriptions_are_dedented_and_keep_their_sections() -> None:
    specs, _ = await make_agent_tools()
    logcat = next(s for s in specs if s.name == "alb_logcat")
    assert "\nWhen to use:\n" in logcat.description
    assert "\nWhen NOT to use:\n" in logcat.description
    assert "\nArgs:\n" in logcat.description
    for s in specs:
        assert not s.description.startswith(" "), s.name
        for header in ("When to use:", "When NOT to use:", "LLM notes:", "Args:"):
            if header in s.description:
                # the docstring's own indentation is gone: headers start a line
                assert f"\n{header}" in s.description, (s.name, header)


@pytest.mark.asyncio
async def test_the_model_copy_is_much_smaller_than_the_mcp_one() -> None:
    specs, _ = await make_agent_tools()
    raw = {t.name: t for t in await create_server().list_tools()}

    def size(name: str, desc: str, params: dict) -> int:
        return len(json.dumps({"name": name, "description": desc, "parameters": params}, ensure_ascii=False))

    ours = sum(size(s.name, s.description, s.parameters) for s in specs)
    theirs = sum(size(n, t.description or "", t.inputSchema or {}) for n, t in raw.items() if n != "alb_playground_chat")
    assert ours < theirs * 0.85, (ours, theirs)


def test_optional_parameter_is_written_plainly() -> None:
    assert _slim_property({"anyOf": [{"type": "string"}, {"type": "null"}], "default": None, "title": "Device"}) == {"type": "string"}
    # a real default stays
    assert _slim_property({"default": 30, "type": "integer", "title": "Timeout"}) == {"default": 30, "type": "integer"}
    # a nullable list keeps its item type
    assert _slim_property({"anyOf": [{"type": "array", "items": {"type": "string"}}, {"type": "null"}], "default": None}) == {
        "type": "array",
        "items": {"type": "string"},
    }
    # two real alternatives are left as they are
    both = {"anyOf": [{"type": "string"}, {"type": "integer"}]}
    assert _slim_property(both) == both


@pytest.mark.asyncio
async def test_validation_still_uses_the_original_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    """The slim copy is only what the model reads: a null device still passes
    FastMCP validation, and a number is still turned into a string."""
    import mcp.server.fastmcp as fastmcp

    seen: list[dict[str, Any]] = []

    async def fake_call_tool(self: Any, name: str, arguments: dict[str, Any]) -> Any:
        seen.append(arguments)
        return [], {"ok": True}

    monkeypatch.setattr(fastmcp.FastMCP, "call_tool", fake_call_tool)
    _, execute = await make_agent_tools()
    await execute("alb_shell", {"cmd": "id", "device": 11223344})
    assert seen == [{"cmd": "id", "device": "11223344"}]
