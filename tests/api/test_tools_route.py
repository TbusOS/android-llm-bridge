"""Tests for GET /tools."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from alb.api.server import create_app


@pytest.fixture
def client():
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_returns_ok_and_count(client) -> None:
    r = client.get("/tools")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert isinstance(body["count"], int)
    # 33 currently shipped (2026-04-28); use a tighter lower bound to
    # catch accidental module deletion regressions
    assert body["count"] >= 30
    assert len(body["tools"]) == body["count"]


def test_eleven_categories_present(client) -> None:
    """Lock down every expected MCP tool module shows up. If a module
    file is renamed/deleted, this catches it."""
    body = client.get("/tools").json()
    cat_names = {c["name"] for c in body["categories"]}
    expected = {
        "devices", "shell", "logging", "filesync", "diagnose",
        "power", "app", "ui", "info", "metrics", "playground",
    }
    assert expected <= cat_names, f"missing categories: {expected - cat_names}"


def test_well_known_tools_present(client) -> None:
    """A handful of canonical alb tools should always appear."""
    body = client.get("/tools").json()
    names = {t["name"] for t in body["tools"]}
    for canonical in (
        "alb_shell", "alb_logcat", "alb_devices", "alb_status",
        "alb_describe", "alb_ui_screenshot", "alb_app_list", "alb_reboot",
    ):
        assert canonical in names, f"missing canonical tool {canonical}"


def test_tools_sorted_by_name(client) -> None:
    body = client.get("/tools").json()
    names = [t["name"] for t in body["tools"]]
    assert names == sorted(names)


def test_each_tool_has_category(client) -> None:
    body = client.get("/tools").json()
    for t in body["tools"]:
        assert t["category"], f"{t['name']} missing category"
        assert isinstance(t["category"], str)


def test_categories_summary_consistent(client) -> None:
    body = client.get("/tools").json()
    # categories sums up to count
    total = sum(c["count"] for c in body["categories"])
    assert total == body["count"]
    # categories sorted by name
    cat_names = [c["name"] for c in body["categories"]]
    assert cat_names == sorted(cat_names)


def test_descriptions_are_first_docstring_line(client) -> None:
    """Description should be a non-empty short line for most tools."""
    body = client.get("/tools").json()
    # alb_shell has a multi-paragraph docstring; description should be
    # only the first line
    shell = next(t for t in body["tools"] if t["name"] == "alb_shell")
    assert shell["description"]
    assert "\n" not in shell["description"]
    # Soft invariant: ≥ 80% of tools have a non-empty description.
    # If a future PR makes someone's docstring start with a blank line
    # (common with `\"\"\"\n    Foo bar\n    \"\"\"` style), this catches it.
    non_empty = sum(1 for t in body["tools"] if t["description"])
    assert non_empty >= int(0.8 * len(body["tools"])), (
        f"only {non_empty}/{len(body['tools'])} tools have a non-empty "
        f"description; check for docstrings starting with blank line"
    )
    # All descriptions are str (None would be a bug)
    for t in body["tools"]:
        assert isinstance(t["description"], str)


def test_endpoint_listed_in_schema(client) -> None:
    paths = [(e["method"], e["path"]) for e in client.get("/api/version").json()["rest"]]
    assert ("GET", "/tools") in paths


def test_no_side_effect_on_repeated_calls(client) -> None:
    """Two calls return identical content (collector is hermetic — no
    register_all() side effects bleed across requests)."""
    a = client.get("/tools").json()
    b = client.get("/tools").json()
    assert a == b


def test_collector_tolerates_kwargs_tool_decorator() -> None:
    """FastMCP also supports `@mcp.tool(name="x", description="y")`.
    Collector must honour explicit name/description without crashing."""
    from alb.api.tools_route import _ToolCollector

    coll = _ToolCollector()

    @coll.tool(name="custom_x", description="custom desc")
    async def some_fn() -> int:
        """ignored docstring"""
        return 1

    assert len(coll.tools) == 1
    assert coll.tools[0]["name"] == "custom_x"
    assert coll.tools[0]["description"] == "custom desc"


def test_collector_tolerates_unknown_surface() -> None:
    """If register_all someday calls @mcp.resource() / @mcp.prompt() /
    mcp.add_tool(...), collector must NOT crash — it should return a
    no-op decorator/method and let known @mcp.tool() registrations
    still flow through."""
    from alb.api.tools_route import _ToolCollector

    coll = _ToolCollector()

    # Decorator-form unknown surface
    @coll.resource()
    async def some_resource() -> int:
        return 1

    # Direct-call-form unknown surface
    coll.add_tool("foo", lambda: None)

    # Known surface still works
    @coll.tool()
    async def real_tool() -> int:
        """real tool"""
        return 2

    assert len(coll.tools) == 1
    assert coll.tools[0]["name"] == "real_tool"
