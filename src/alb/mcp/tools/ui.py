"""MCP tools: alb_ui_screenshot, alb_ui_dump.

Both are read-only diagnostics — alb does not tap / swipe / type. See
docs/capabilities/ui.md for the rationale.
"""

from __future__ import annotations

from typing import Any

from alb.capabilities.ui import screenshot, ui_dump
from alb.mcp.transport_factory import build_transport


def register(mcp) -> None:  # noqa: ANN001
    @mcp.tool()
    async def alb_ui_screenshot(
        device: str | None = None,
        output: str | None = None,
        include_thumbnail: bool = False,
    ) -> dict[str, Any]:
        """Capture a PNG screenshot of the device.

        When to use:
            - Sanity-check what the user is seeing before further analysis
            - Attach visual context to a bug report

        Args:
            device: device serial (for multi-device setups).
            output: local path override. Default: workspace/devices/<s>/screenshots/.
            include_thumbnail: if True, also return a base64 PNG thumbnail
                (max 256 px). Defaults to False to save tokens; set True
                only when you actually need to look at the pixels.

        Returns:
            { path, device_path, width, height, size_bytes, thumbnail_base64 }.
        """
        t = build_transport(device_serial=device)
        r = await screenshot(
            t,
            device=device,
            output=output,
            include_thumbnail=include_thumbnail,
        )
        return r.to_dict()

    @mcp.tool()
    async def alb_ui_dump(
        device: str | None = None,
        output: str | None = None,
    ) -> dict[str, Any]:
        """Dump the current Android view hierarchy as structured JSON.

        When to use:
            - Find a specific control by text / resource-id / class
            - Understand how the current screen is laid out
            - Combine with alb_ui_screenshot for visual + structural view

        Returns the full view tree (UINode with class / resource-id / text /
        bounds / clickable / enabled / focused / selected / package / children),
        plus top_activity + package_name for context.

        Note: alb is diagnostic-only — to tap or swipe, use a different tool.
        """
        t = build_transport(device_serial=device)
        r = await ui_dump(t, device=device, output=output)
        return r.to_dict()
