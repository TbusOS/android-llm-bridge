"""MCP server entry point (`alb-mcp` command).

Starts a FastMCP server over stdio exposing all alb capabilities as tools.

The mcp package is imported lazily so the CLI / tests don't pay the cost
if the server never runs.
"""

from __future__ import annotations

import sys


def create_server():  # type: ignore[no-untyped-def]
    """Create and return a configured FastMCP instance.

    Kept as a free function so tests can introspect the registered tool
    list without going through stdio.
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as e:
        raise SystemExit(
            f"[alb-mcp] The `mcp` package is not installed: {e}\n"
            "Run: uv sync   (or: pip install mcp)"
        ) from None

    mcp = FastMCP(
        "alb",
        instructions=(
            "android-llm-bridge — Unified Android debugging bridge for LLM agents.\n"
            "Call alb_status / alb_describe first to discover the environment.\n"
            "All tools return structured { ok, data, error, artifacts } results."
        ),
    )
    from alb.mcp.tools import register_all

    register_all(mcp)
    return mcp


def main() -> None:
    """Entry point referenced by pyproject.toml `[project.scripts]`."""
    try:
        mcp = create_server()
    except SystemExit:
        raise
    except Exception as e:
        print(f"[alb-mcp] Failed to start: {e}", file=sys.stderr)
        sys.exit(1)

    # FastMCP.run() defaults to stdio transport — correct for MCP clients
    # launching us as a subprocess (Claude Code, Cursor, Codex).
    mcp.run()


if __name__ == "__main__":
    main()
