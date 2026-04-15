"""MCP tools: alb_logcat, alb_dmesg, alb_log_search, alb_log_tail."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from alb.capabilities.logging import (
    capture_uart,
    collect_dmesg,
    collect_logcat,
    search_logs,
    tail_log,
)
from alb.mcp.transport_factory import build_transport


def register(mcp) -> None:  # noqa: ANN001
    @mcp.tool()
    async def alb_logcat(
        duration: int = 60,
        filter: str | None = None,  # noqa: A002
        tags: list[str] | None = None,
        clear_before: bool = False,
        device: str | None = None,
    ) -> dict[str, Any]:
        """Collect Android logcat to workspace for `duration` seconds.

        When to use:
            - Investigating app crashes, ANRs, system errors
            - Reproducing a bug — capture 30-60s around the repro window

        When NOT to use:
            - Continuous / background monitoring > 300s (M2 will add watch mode)
            - Device is only reachable via UART → use alb_uart_capture instead

        LLM notes:
            - Returns only a summary (lines/errors/warnings/top_tags).
            - Full log is in result.artifacts[0]. Use alb_log_search or
              alb_log_tail to read specific parts.
            - filter syntax: "*:E" (all tags, error only),
              "Tag:I *:S" (only Tag at Info, silence rest)

        Args:
            duration: 1-3600 seconds
            filter: logcat filter spec
            tags: shortcut — auto-builds "<tag>:V *:S" style filter
            clear_before: run `logcat -c` before collecting
            device: optional device serial
        """
        t = build_transport(device_serial=device)
        r = await collect_logcat(
            t,
            duration=duration,
            filter=filter,
            tags=tags,
            clear_before=clear_before,
            device=device,
        )
        return r.to_dict()

    @mcp.tool()
    async def alb_dmesg(
        duration: int = 10,
        device: str | None = None,
    ) -> dict[str, Any]:
        """Collect kernel dmesg for `duration` seconds.

        When to use:
            - Kernel-level issues (driver errors, OOM, low-level panics)
            - Complementing logcat for boot / suspend/resume bugs
        """
        t = build_transport(device_serial=device)
        r = await collect_dmesg(t, duration=duration, device=device)
        return r.to_dict()

    @mcp.tool()
    async def alb_uart_capture(
        duration: int = 30,
        device: str | None = None,
    ) -> dict[str, Any]:
        """Capture raw UART output to workspace for `duration` seconds.

        REQUIRES serial transport (method G). Call alb_setup or set
        ALB_TRANSPORT=serial first.

        When to use (UART's unique value — no other transport can do these):
            - Device is bricked / hung / black-screen (adb/ssh are dead)
            - Debugging boot stage: u-boot / kernel init / early userspace
            - Capturing kernel panic stack traces
            - Observing watchdog reset reasons
            - Root-cause analysis for why adbd / sshd failed to start

        LLM notes:
            - Returns a summary with error-keyword count; full log is at
              result.artifacts[0].
            - Use alb_log_search(pattern="panic|oops|BUG|fail") to find
              interesting sections in the full log.
        """
        t = build_transport(override="serial", device_serial=device)
        r = await capture_uart(t, duration=duration, device=device)
        return r.to_dict()

    @mcp.tool()
    async def alb_log_search(
        pattern: str,
        path: str | None = None,
        device: str | None = None,
        max_matches: int = 200,
    ) -> dict[str, Any]:
        """Regex-search across workspace-collected logs.

        When to use:
            - After alb_logcat/alb_dmesg/alb_uart_capture, to find specific
              events (e.g. pattern="FATAL|ANR|panic|oops") without reading
              the whole file
            - To correlate events across multiple collections

        Args:
            pattern: Python regex
            path: optional single file or directory (default: all workspace logs)
            device: limit to one device's logs
            max_matches: cap at 200 by default
        """
        p = Path(path).resolve() if path else None
        r = await search_logs(
            pattern, path=p, device=device, max_matches=max_matches
        )
        return r.to_dict()

    @mcp.tool()
    async def alb_log_tail(
        path: str,
        lines: int = 50,
        from_line: int | None = None,
        to_line: int | None = None,
    ) -> dict[str, Any]:
        """Read a section of a log file. Only workspace paths are allowed.

        When to use:
            - After alb_log_search, read context around a match
            - When you need the last N lines of a long collection

        Args:
            path: workspace log file path (returned in artifacts from other tools)
            lines: tail length (ignored if from_line/to_line given)
            from_line / to_line: 1-based inclusive range
        """
        r = await tail_log(
            Path(path), lines=lines, from_line=from_line, to_line=to_line
        )
        return r.to_dict()
