"""What the model sends that a tool would refuse: numbers where a tool takes
a string (2026-11-03), and ``device=""`` (2026-11-05)."""

from __future__ import annotations

from typing import Any

import pytest

from alb.infra.result import ok
from alb.mcp.executor import coerce_string_args, drop_blank_device, make_agent_tools

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


# ─── device="" (2026-11-05: alb_battery(device="") failed, two more calls) ──


def test_a_blank_device_is_left_out() -> None:
    assert drop_blank_device({"cmd": "getprop", "device": ""}) == {"cmd": "getprop"}
    assert drop_blank_device({"device": "  "}) == {}


def test_a_named_or_missing_device_is_kept() -> None:
    named = {"cmd": "getprop", "device": "board-a"}
    assert drop_blank_device(named) is named
    assert drop_blank_device({"device": None}) == {"device": None}
    assert drop_blank_device({"cmd": "getprop"}) == {"cmd": "getprop"}


@pytest.mark.asyncio
async def test_a_blank_device_runs_the_tool_on_the_default_board(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Through the real tool registry: the tool sees no device, as when the
    model leaves it out, instead of failing the serial check."""
    import alb.mcp.tools.power as power

    seen: list[str | None] = []

    def fake_build_transport(*, override: str | None = None, device_serial: str | None = None) -> Any:
        seen.append(device_serial)
        return object()

    async def fake_battery(transport: Any) -> Any:
        return ok(data={"level": 80})

    monkeypatch.setattr(power, "build_transport", fake_build_transport)
    monkeypatch.setattr(power, "battery", fake_battery)
    _, execute = await make_agent_tools()
    result = await execute("alb_battery", {"device": ""})
    assert result["ok"] is True, result
    assert seen == [None]
