"""MCP tool: alb_shell."""

from __future__ import annotations

from typing import Any

from alb.capabilities.shell import execute as shell_execute
from alb.mcp.transport_factory import build_transport


def register(mcp) -> None:  # noqa: ANN001
    @mcp.tool()
    async def alb_shell(
        cmd: str,
        timeout: int = 30,
        device: str | None = None,
        allow_dangerous: bool = False,
    ) -> dict[str, Any]:
        """Execute a shell command on the connected Android device.

        When to use:
            - Querying state (getprop, dumpsys, ps, ls, cat, ...)
            - Invoking Android tools (pm, am, service, input)
            - One-off commands

        When NOT to use:
            - Long output (> hundreds of lines): prefer a capability that
              routes to workspace (alb_logcat, alb_bugreport)
            - Destructive / state-changing operations without clear intent
              — the permission system will block most of them anyway

        Safety:
            - Dangerous patterns (rm -rf /, reboot bootloader, setprop
              persist.*, dd to /dev/block) are denied by default.
            - ASK-level commands (mount remount,rw) need allow_dangerous=True.
            - DENY is never bypassable via allow_dangerous.

        Args:
            cmd: command line to run on device
            timeout: seconds, default 30
            device: optional device serial (overrides profile default)
            allow_dangerous: if True, ASK commands auto-proceed. DENY unaffected.

        Returns:
            Standard Result {ok, data:{stdout, stderr, exit_code, duration_ms},
            error, artifacts, timing_ms}.
        """
        transport = build_transport(device_serial=device)
        r = await shell_execute(
            transport, cmd, timeout=timeout, allow_dangerous=allow_dangerous
        )
        return r.to_dict()
