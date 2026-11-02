"""A chat turn must stop when the page's Stop button cancels it.

On Python 3.11 asyncio.wait_for returns the awaited result and drops a
cancel that arrives in the same loop step as that result, so a tool could
finish, or go on to its next step, after the user pressed Stop. The code a
chat turn runs through uses ``async with asyncio.timeout(...)`` instead;
this test keeps it that way.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src" / "alb"

# What a chat turn runs through: the agent loop, the MCP tool bridge, the
# capabilities and transports the tools call, the subprocess runner and the
# chat route itself.
_TOOL_PATH = [
    _SRC / "agent",
    _SRC / "mcp",
    _SRC / "capabilities",
    _SRC / "transport",
    _SRC / "infra" / "process.py",
    _SRC / "api" / "chat_route.py",
]


def _wait_for_uses(path: Path) -> list[str]:
    """`asyncio.wait_for(...)` calls and `from asyncio import wait_for`."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "wait_for"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "asyncio"
        ) or (
            isinstance(node, ast.ImportFrom)
            and node.module == "asyncio"
            and any(a.name == "wait_for" for a in node.names)
        ):
            hits.append(f"{path}:{node.lineno}")
    return hits


def _tool_path_files() -> list[Path]:
    return [f for p in _TOOL_PATH for f in ([p] if p.is_file() else sorted(p.rglob("*.py")))]


def test_no_asyncio_wait_for_on_the_tool_path() -> None:
    files = _tool_path_files()
    assert len(files) >= 50, f"only {len(files)} files scanned: is _SRC right?"
    found = [hit for f in files for hit in _wait_for_uses(f)]
    assert found == [], "use `async with asyncio.timeout(...)` instead:\n" + "\n".join(found)


def test_the_scan_sees_both_spellings(tmp_path: Path) -> None:
    sample = tmp_path / "sample.py"
    sample.write_text(
        "import asyncio\n"
        "from asyncio import wait_for\n"
        "# asyncio.wait_for in a comment does not count\n"
        "async def f(c):\n"
        "    return await asyncio.wait_for(c, 1)\n"
    )
    assert sorted(_wait_for_uses(sample)) == [f"{sample}:2", f"{sample}:5"]
