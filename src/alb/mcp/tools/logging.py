"""MCP tools: alb_logcat, alb_dmesg, alb_log_search, alb_log_tail."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from alb.capabilities.logging import (
    capture_uart,
    collect_dmesg,
    collect_logcat,
    search_logs,
    send_uart,
    tail_log,
    watch_uart_panic,
)
from alb.capabilities.shell import execute as shell_execute
from alb.mcp.transport_factory import build_transport


def register(mcp) -> None:
    @mcp.tool()
    async def alb_logcat(
        duration: int = 60,
        filter: str | None = None,
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
    async def alb_uart_send(
        text: str,
        append_newline: bool = True,
        device: str | None = None,
    ) -> dict[str, Any]:
        """Send raw text to the UART console (fire-and-forget, no read).

        REQUIRES serial transport (method G).

        When to use:
            - Interrupt u-boot autoboot: text="\\x03" (Ctrl-C), append_newline=False
            - Type a u-boot / bootloader command, then read with alb_uart_shell
              or alb_uart_capture
            - Send a single keypress / control sequence to a hung console

        LLM notes:
            - Does NOT return the device's response — follow with
              alb_uart_shell (waits for prompt) or alb_uart_capture (time window).
            - append_newline=True appends "\\n"; set False for raw control bytes.
        """
        t = build_transport(override="serial", device_serial=device)
        r = await send_uart(t, text, append_newline=append_newline)
        return r.to_dict()

    @mcp.tool()
    async def alb_uart_shell(
        cmd: str,
        timeout: int = 30,
        device: str | None = None,
    ) -> dict[str, Any]:
        """Run a command on the UART console and return its output.

        REQUIRES serial transport (method G). Unlike alb_uart_send, this waits
        for the console prompt and strips it, returning the command output. Works
        on a Linux shell / getty AND on a u-boot prompt (the state machine
        classifies the console first).

        When to use:
            - The device is only reachable over UART (adb/ssh dead) but a shell
              or u-boot prompt is up
            - Inspect / set u-boot env, run a bootloader command, run a shell
              command during early boot

        LLM notes:
            - Maps console state to clear errors: BOARD_PANICKED (with the panic
              tail in stdout), BOARD_UNREACHABLE, SERIAL_BAUD_MISMATCH,
              BOARD_BOOTING, BOARD_NEEDS_LOGIN.
            - For interrupt sequences / raw bytes use alb_uart_send instead.
        """
        t = build_transport(override="serial", device_serial=device)
        r = await shell_execute(t, cmd, timeout=timeout)
        return r.to_dict()

    @mcp.tool()
    async def alb_uart_watch_panic(
        duration: int = 60,
        device: str | None = None,
    ) -> dict[str, Any]:
        """Capture UART for `duration`s and report whether a kernel panic / Oops
        appeared, with the crash tail.

        REQUIRES serial transport (method G).

        When to use:
            - Reproduce a crash / watchdog reset and confirm + grab the stack
            - Reboot a flaky board and watch the boot for a panic

        LLM notes:
            - Returns {panic_detected, marker, tail, lines}; full log at
              result.artifacts[0]. Size `duration` to cover the boot / repro.
            - Captures the whole window then scans (no early return).
        """
        t = build_transport(override="serial", device_serial=device)
        r = await watch_uart_panic(t, duration=duration, device=device)
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
        r = await search_logs(pattern, path=p, device=device, max_matches=max_matches)
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
        r = await tail_log(Path(path), lines=lines, from_line=from_line, to_line=to_line)
        return r.to_dict()
