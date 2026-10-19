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


def test_a_wrong_mcp_major_version_is_not_reported_as_missing(monkeypatch) -> None:
    """mcp 2.x moved FastMCP, so the import fails as if mcp were missing.
    The 10-19 bench: "not installed, run uv sync" while 2.1.1 was installed
    in a `uv tool` environment that uv sync never touches."""
    import importlib.metadata

    from alb.mcp import server

    monkeypatch.setattr(importlib.metadata, "version", lambda name: "2.1.1")
    msg = server._import_failure(ImportError("No module named 'mcp.server.fastmcp'"))
    assert "mcp 2.1.1 is installed, but alb needs mcp 1.x" in msg
    assert "not installed" not in msg


def test_a_missing_mcp_is_reported_as_missing(monkeypatch) -> None:
    import importlib.metadata

    from alb.mcp import server

    def _missing(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", _missing)
    msg = server._import_failure(ImportError("No module named 'mcp'"))
    assert "is not installed" in msg
