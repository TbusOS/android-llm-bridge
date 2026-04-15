"""MCP server entry point (`alb-mcp` command).

M0 skeleton — starts a placeholder server that advertises no tools yet.
M1 will register tools from capabilities/.
"""

from __future__ import annotations

import sys


def main() -> None:
    """Entry point referenced by pyproject.toml `[project.scripts]`.

    M0: print a notice and exit.
    M1: will actually start the MCP server over stdio.
    """
    print(
        "[alb-mcp] M0 skeleton — not implemented yet.\n"
        "M1 will start a real MCP server exposing alb capabilities to LLM clients.\n"
        "See docs/llm-integration.md and docs/project-plan.md.",
        file=sys.stderr,
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
