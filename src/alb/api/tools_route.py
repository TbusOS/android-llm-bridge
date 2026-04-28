"""GET /tools — discover MCP tools registered by alb.

Returns the canonical list of `@mcp.tool()` functions defined under
`src/alb/mcp/tools/`. Used by the Web UI Dashboard's "MCP tools" KPI
(DEBT-003 close) and as future input for the Inspect / Playground
modules to render a tool palette.

Implementation: instead of starting a real FastMCP server (heavy +
needs stdio), we feed `register_all()` a tiny `_ToolCollector` that
captures function metadata as the `@mcp.tool()` decorator runs.
This keeps GET /tools cheap and side-effect-free.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter

from alb.mcp.tools import register_all

router = APIRouter()


# Module suffix → human category label. Matches the directory structure
# under src/alb/mcp/tools/. Keys are the leaf module name (after the
# last "." in __module__).
_CATEGORY_LABELS = {
    "devices": "devices",
    "shell": "shell",
    "logging": "logging",
    "filesync": "filesync",
    "diagnose": "diagnose",
    "power": "power",
    "app": "app",
    "ui": "ui",
    "info": "info",
    "metrics": "metrics",
    "playground": "playground",
}


def _category_from_module(module_name: str) -> str:
    leaf = module_name.rsplit(".", 1)[-1]
    return _CATEGORY_LABELS.get(leaf, leaf)


def _first_doc_line(fn: Callable[..., Any]) -> str:
    doc = (fn.__doc__ or "").strip()
    if not doc:
        return ""
    return doc.splitlines()[0].strip()


class _ToolCollector:
    """Fake MCP that records `@tool()` calls as metadata.

    Doesn't actually wire anything; the captured callables are returned
    unchanged so register_all completes without side effects.

    Forward-compat: register_all() may someday call other FastMCP
    surfaces (`@mcp.resource()`, `@mcp.prompt()`, `mcp.add_tool(...)`,
    etc.). `__getattr__` returns a no-op for unknown attributes so the
    endpoint degrades gracefully (returning whatever `@tool()` we did
    capture) instead of crashing with AttributeError. We log once per
    unknown surface so the gap is visible in operator logs.
    """

    def __init__(self) -> None:
        self.tools: list[dict[str, Any]] = []
        self._warned_surfaces: set[str] = set()

    def tool(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        # FastMCP also accepts `@tool(name="x", description="y")`; honour
        # explicit name/description if present, otherwise fall back to
        # function-level introspection.
        explicit_name = kwargs.get("name")
        explicit_desc = kwargs.get("description")

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.tools.append({
                "name": str(explicit_name or fn.__name__),
                "description": str(explicit_desc or _first_doc_line(fn)),
                "category": _category_from_module(fn.__module__),
            })
            return fn

        return decorator

    def __getattr__(self, name: str) -> Callable[..., Any]:
        # Unknown surface — return a passthrough no-op decorator/method
        # that never crashes. We can't `import logging` at class level
        # in the noqa, but warning once per name is fine inline.
        if name not in self._warned_surfaces:
            self._warned_surfaces.add(name)
            import logging  # noqa: PLC0415 — defer until actually triggered
            logging.getLogger(__name__).warning(
                "GET /tools: unknown FastMCP surface %r — /tools may "
                "underreport tools registered via this surface.", name,
            )

        def _noop_decorator_or_call(*args: Any, **kwargs: Any) -> Any:
            # Behaves both as `@mcp.foo` (decorator factory) and
            # `mcp.foo(...)` (direct call). For decorator-of-fn shape
            # return the fn untouched.
            if args and callable(args[0]):
                return args[0]
            return _noop_decorator_or_call

        return _noop_decorator_or_call


def _collect_tools() -> list[dict[str, Any]]:
    """Walk register_all() with a fake MCP collector.

    Intentionally NOT cached: register_all is a pure dict-append walk
    over already-imported modules (~33 decorator calls, sub-millisecond).
    Caching adds invariant risk (stale results after a hot-reload in
    dev) and we have no measured pressure to justify it.
    """
    collector = _ToolCollector()
    register_all(collector)
    return collector.tools


@router.get("/tools")
async def list_tools() -> dict[str, Any]:
    """List all MCP tools registered by alb.

    Response shape::

        {
          "ok": true,
          "count": N,
          "categories": [{"name": "shell", "count": 1}, ...],   # sorted
          "tools": [{"name": "alb_shell",
                     "description": "<first docstring line>",
                     "category": "shell"}, ...]                  # sorted by name
        }

    The `description` field is the first non-empty line of each tool
    function's docstring. **Consumers MUST treat description as plain
    text** — first docstring line may legally contain HTML-like glyphs
    (`<...>` / `&` etc.); render with React's default escaping or
    explicit escape, never `dangerouslySetInnerHTML`.
    """
    tools = _collect_tools()
    tools.sort(key=lambda t: t["name"])

    counts: dict[str, int] = {}
    for t in tools:
        counts[t["category"]] = counts.get(t["category"], 0) + 1
    categories = [{"name": k, "count": counts[k]} for k in sorted(counts)]

    return {
        "ok": True,
        "count": len(tools),
        "categories": categories,
        "tools": tools,
    }
