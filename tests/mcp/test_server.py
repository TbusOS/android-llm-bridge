"""Tests for MCP server wiring.

These tests import lazily so absence of the `mcp` package results in SKIP,
not FAIL — the rest of the test suite still runs.
"""

from __future__ import annotations

import pytest

mcp_pkg = pytest.importorskip("mcp", reason="mcp SDK not installed")


def test_create_server_registers_tools() -> None:
    from alb.mcp.server import create_server

    server = create_server()
    assert server is not None
    # FastMCP stores tools on an internal structure — we just smoke-check that
    # calling create_server() doesn't explode and returns a server.
    #
    # When the mcp SDK exposes a stable introspection API we can assert the
    # expected tool names (alb_shell, alb_logcat, alb_devices, ...).
    assert hasattr(server, "run")
