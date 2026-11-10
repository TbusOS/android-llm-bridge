"""docs/llm-integration.md must name exactly the MCP tools alb-mcp registers.

The tool table in that doc drifted from the code once already: it listed 22
of 36 tools and two names that never existed (``alb_sleep_wake``,
``alb_tombstone``). An LLM or a person copying a name from the doc gets
"unknown tool", and nothing in the build noticed. This test checks both
directions against the live registration, so adding, renaming or removing a
tool without touching the doc fails here.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

pytest.importorskip("mcp", reason="mcp SDK not installed")

DOC = Path(__file__).resolve().parent.parent / "docs" / "llm-integration.md"

# Anything in the doc shaped like a tool name is checked against the server.
_TOOL_LIKE = re.compile(r"\balb_[a-z_]+\b")

# Doc words that look like tool names but are not tools. Each entry needs a
# reason; keep it empty unless the doc really has to mention such a word.
_NOT_TOOLS: dict[str, str] = {}


def _registered_tools() -> set[str]:
    from alb.mcp.server import create_server

    server = create_server()
    return {t.name for t in asyncio.run(server.list_tools())}


def _doc_names() -> set[str]:
    return set(_TOOL_LIKE.findall(DOC.read_text(encoding="utf-8")))


def test_server_registers_tools() -> None:
    # Guards the two checks below: an empty set would make both pass.
    assert len(_registered_tools()) > 10


def test_every_registered_tool_is_in_the_doc() -> None:
    missing = sorted(_registered_tools() - _doc_names())
    assert not missing, f"docs/llm-integration.md does not mention these MCP tools: {missing}"


def test_every_tool_name_in_the_doc_exists() -> None:
    unknown = sorted(_doc_names() - _registered_tools() - set(_NOT_TOOLS))
    assert not unknown, (
        f"docs/llm-integration.md names tools alb-mcp does not register: {unknown} "
        "(fix the doc, or add a non-tool word to _NOT_TOOLS with a reason)"
    )
