"""MCP tool registration package.

Each submodule exposes a `register(mcp)` function which attaches tools
for one capability area. The server module calls register() for all.
"""

from alb.mcp.tools import app as app_tools
from alb.mcp.tools import devices as devices_tools
from alb.mcp.tools import diagnose as diagnose_tools
from alb.mcp.tools import filesync as filesync_tools
from alb.mcp.tools import logging as logging_tools
from alb.mcp.tools import power as power_tools
from alb.mcp.tools import shell as shell_tools
from alb.mcp.tools import ui as ui_tools


def register_all(mcp) -> None:  # noqa: ANN001 — FastMCP import lazy in server
    devices_tools.register(mcp)
    shell_tools.register(mcp)
    logging_tools.register(mcp)
    filesync_tools.register(mcp)
    diagnose_tools.register(mcp)
    power_tools.register(mcp)
    app_tools.register(mcp)
    ui_tools.register(mcp)
