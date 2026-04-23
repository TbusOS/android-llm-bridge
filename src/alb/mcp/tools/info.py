"""MCP tools: alb_info (unified panel query)."""

from __future__ import annotations

from typing import Any

from alb.capabilities.info import _PANELS, all_info, panel_names
from alb.mcp.transport_factory import build_transport


def register(mcp) -> None:  # noqa: ANN001
    @mcp.tool()
    async def alb_info(
        panel: str = "all",
        device: str | None = None,
    ) -> dict[str, Any]:
        """Structured device software / hardware info.

        When to use:
            - Baseline the device before deeper analysis
            - Diagnose thermal throttle (panel="cpu" → thermal_zones)
            - Understand partition layout (panel="storage")
            - Check battery health (panel="battery")

        Args:
            panel: one of "all", "system", "cpu", "memory", "storage",
                   "network", "battery". Default "all" runs them all in
                   parallel.
            device: device serial (optional).

        Returns:
            For a single panel: the Result dict {ok, data, error, ...}.
            For "all": {panel_name: Result_dict_for_that_panel}.
        """
        t = build_transport(device_serial=device)

        if panel == "all":
            results = await all_info(t, device=device)
            return {k: v.to_dict() for k, v in results.items()}

        func = _PANELS.get(panel)
        if func is None:
            return {
                "ok": False,
                "data": None,
                "error": {
                    "code": "UNKNOWN_PANEL",
                    "message": f"Unknown panel '{panel}'. Choices: {panel_names()}",
                    "suggestion": "",
                    "category": "input",
                    "details": {},
                },
                "artifacts": [],
                "timing_ms": 0,
            }
        r = await func(t, device=device)
        return r.to_dict()
