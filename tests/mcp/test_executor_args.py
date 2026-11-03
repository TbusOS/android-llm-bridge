"""Numbers the model sends where a tool takes a string (2026-11-03)."""

from __future__ import annotations

from typing import Any

import pytest

from alb.mcp.executor import coerce_string_args, make_agent_tools

_SHELL = {
    "properties": {
        "cmd": {"type": "string"},
        "timeout": {"default": 30, "type": "integer"},
        "device": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
        "allow_dangerous": {"default": False, "type": "boolean"},
        "either": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
    }
}


def test_an_all_digit_serial_sent_as_a_number_becomes_a_string() -> None:
    assert coerce_string_args(_SHELL, {"cmd": "getprop", "device": 11223344}) == {
        "cmd": "getprop",
        "device": "11223344",
    }


def test_only_string_parameters_change() -> None:
    args = {"cmd": 5, "timeout": 10, "allow_dangerous": True, "either": 3, "unknown": 7}
    assert coerce_string_args(_SHELL, args) == {
        "cmd": "5",  # string only
        "timeout": 10,  # integer
        "allow_dangerous": True,  # bool is an int in Python; left alone
        "either": 3,  # string or integer: the number is fine
        "unknown": 7,  # not in the schema
    }


def test_no_schema_passes_args_through() -> None:
    args = {"device": 1}
    assert coerce_string_args({}, args) is args


@pytest.mark.asyncio
async def test_the_executor_hands_the_tool_a_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """Through the real tool registry: alb_shell gets device as a string."""
    import mcp.server.fastmcp as fastmcp

    seen: list[dict[str, Any]] = []

    async def fake_call_tool(self: Any, name: str, arguments: dict[str, Any]) -> Any:
        seen.append(arguments)
        return [], {"ok": True}

    monkeypatch.setattr(fastmcp.FastMCP, "call_tool", fake_call_tool)
    _, execute = await make_agent_tools()
    assert await execute("alb_shell", {"cmd": "getprop", "device": 11223344}) == {"ok": True}
    assert seen == [{"cmd": "getprop", "device": "11223344"}]
