"""alb_describe_errors: exact code, a search word, or the whole catalog.

The search word exists for the web assistant (2026-11-05): the whole catalog
is ~23 KB, the model's copy of a tool result is cut to keep a CPU-hosted model
fast, and from the cut copy it answered with 3 of the 6 serial codes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from alb.infra.errors import ERROR_CODES
from alb.mcp.tools import devices

Tool = Callable[..., Awaitable[dict[str, Any]]]


class _Collect:
    """Stands in for FastMCP: keeps the functions register() defines."""

    def __init__(self) -> None:
        self.fns: dict[str, Tool] = {}

    def tool(self) -> Callable[[Tool], Tool]:
        def keep(fn: Tool) -> Tool:
            self.fns[fn.__name__] = fn
            return fn

        return keep


def _describe_errors() -> Tool:
    mcp = _Collect()
    devices.register(mcp)
    return mcp.fns["alb_describe_errors"]


async def test_search_returns_every_matching_code_and_only_those() -> None:
    serial = sorted(c for c in ERROR_CODES if "serial" in c.lower())
    assert len(serial) >= 2, "the catalog changed; pick another word"
    r = await _describe_errors()(search="Serial")
    assert r["ok"] is True
    got = sorted(c["code"] for c in r["codes"])
    assert all(
        "serial" in c["code"].lower() or "serial" in c["category"].lower() for c in r["codes"]
    )
    assert set(serial) <= set(got)
    assert len(got) < len(ERROR_CODES)


async def test_search_also_matches_the_category() -> None:
    category = next(iter(ERROR_CODES.values())).category
    r = await _describe_errors()(search=category.upper())
    want = {s.code for s in ERROR_CODES.values() if category in s.category.lower() or category in s.code.lower()}
    assert {c["code"] for c in r["codes"]} == want


async def test_search_with_no_match_says_how_to_get_everything() -> None:
    r = await _describe_errors()(search="no-such-word-zz")
    assert r["ok"] is True and r["codes"] == []
    assert "omit search" in r["note"]


async def test_no_arguments_still_returns_the_whole_catalog() -> None:
    r = await _describe_errors()()
    assert len(r["codes"]) == len(ERROR_CODES)
    r = await _describe_errors()(search="  ")
    assert len(r["codes"]) == len(ERROR_CODES)


async def test_exact_code_wins_over_search() -> None:
    code = next(iter(ERROR_CODES))
    r = await _describe_errors()(code=code, search="serial")
    assert r["code"] == code
