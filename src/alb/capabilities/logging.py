"""logging capability — collect logcat / dmesg / uart and search / tail.

See docs/capabilities/logging.md for the full spec.
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any

from alb.infra.events import bus
from alb.infra.result import Result, fail, ok
from alb.infra.workspace import (
    InvalidDeviceSerial,
    is_safe_device,
    iso_timestamp,
    resolve_capture_path,
    workspace_root,
)
from alb.transport.base import Transport
from alb.transport.serial_state import DEFAULT_PATTERNS


# ─── Models ────────────────────────────────────────────────────────
@dataclass(frozen=True)
class LogcatSummary:
    lines: int
    errors: int
    warnings: int
    top_tags: list[tuple[str, int]] = field(default_factory=list)
    first_line_ts: str = ""
    last_line_ts: str = ""
    duration_captured_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "lines": self.lines,
            "errors": self.errors,
            "warnings": self.warnings,
            "top_tags": [{"tag": t, "count": c} for t, c in self.top_tags],
            "first_line_ts": self.first_line_ts,
            "last_line_ts": self.last_line_ts,
            "duration_captured_ms": self.duration_captured_ms,
        }


@dataclass(frozen=True)
class DmesgSummary:
    lines: int
    errors: int
    duration_captured_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "lines": self.lines,
            "errors": self.errors,
            "duration_captured_ms": self.duration_captured_ms,
        }


@dataclass(frozen=True)
class SearchMatch:
    path: str
    line_number: int
    content: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "line_number": self.line_number,
            "content": self.content,
        }


@dataclass(frozen=True)
class SearchResults:
    pattern: str
    matches: list[SearchMatch]
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern": self.pattern,
            "matches": [m.to_dict() for m in self.matches],
            "truncated": self.truncated,
            "match_count": len(self.matches),
        }


# ─── logcat ────────────────────────────────────────────────────────
_LOGCAT_THREADTIME_RE = re.compile(
    r"^(?P<date>\d{2}-\d{2})\s+"
    r"(?P<time>\d{2}:\d{2}:\d{2}\.\d+)\s+"
    r"(?P<pid>\d+)\s+(?P<tid>\d+)\s+"
    r"(?P<level>[VDIWEF])\s+"
    r"(?P<tag>[^:]+?):\s"
)


async def collect_logcat(
    transport: Transport,
    *,
    duration: int = 60,
    filter: str | None = None,
    tags: list[str] | None = None,
    clear_before: bool = False,
    device: str | None = None,
    output: Path | str | None = None,
) -> Result[LogcatSummary]:
    """Collect logcat for N seconds into a workspace file.

    LLM: returns a summary (lines/errors/warnings). Full log is in
    `result.artifacts[0]`; use `search_logs` or `tail_log` to read it.

    Args:
        output: Optional override for the artifact path (same rules as
            `capture_uart` / `_resolve_capture_path`):
            - None → workspace/.../logs/<ts>-logcat.txt
            - Existing dir or trailing "/" → that dir + auto file name
            - Anything else → exact file path
    """
    if duration < 1 or duration > 3600:
        return fail(
            code="INVALID_DURATION",
            message=f"duration must be 1..3600, got {duration}",
            suggestion="Use a value between 1 and 3600 seconds",
            category="input",
        )

    perm = await transport.check_permissions(
        "logging.logcat",
        {"duration": duration, "filter": filter},
    )
    if perm.behavior == "deny":
        return fail(
            code="PERMISSION_DENIED",
            message=perm.reason or "logcat blocked",
            suggestion=perm.suggestion or "",
            category="permission",
        )

    filt = filter
    if tags and not filt:
        filt = " ".join(f"{t}:V" for t in tags) + " *:S"

    artifact = _resolve_capture_path(
        output,
        f"{iso_timestamp()}-logcat.txt",
        device=device,
    )

    start = perf_counter()
    stats = _LineStats()
    try:
        async with asyncio.timeout(duration + 5):
            await _drain_stream(
                transport.stream_read("logcat", filter=filt, clear=clear_before),
                artifact,
                stats,
                max_seconds=duration,
                line_parser=_parse_logcat_line,
                topic="logcat.line",
            )
    except TimeoutError:
        pass

    duration_ms = int((perf_counter() - start) * 1000)

    summary = LogcatSummary(
        lines=stats.lines,
        errors=stats.errors,
        warnings=stats.warnings,
        top_tags=stats.top_tags(limit=10),
        first_line_ts=stats.first_ts,
        last_line_ts=stats.last_ts,
        duration_captured_ms=duration_ms,
    )
    return ok(data=summary, artifacts=[artifact], timing_ms=duration_ms)


# ─── dmesg ─────────────────────────────────────────────────────────
async def capture_uart(
    transport: Transport,
    *,
    duration: int = 30,
    device: str | None = None,
    output: Path | str | None = None,
) -> Result[DmesgSummary]:
    """Capture raw UART output for `duration` seconds. Requires SerialTransport.

    LLM notes:
        - UART bytes are written verbatim to the artifact file, incrementally —
          you can tail / grep the artifact WHILE the capture is still running
          instead of waiting out the full duration.
        - Use for: boot log, u-boot stage, kernel panic rescue.
        - Returns DmesgSummary-shaped summary (lines + error-keyword count).
        - Safe to run alongside `alb_uart_shell` / `alb_uart_send` / the web UART
          console: readers share one link. To capture a full boot, start the
          capture FIRST, then reboot the board from another call.

    Args:
        output: Optional override for the artifact path.
            - None (default) → workspace/.../logs/<ts>-uart.log
            - An existing directory or a path ending with "/" → that dir +
              "<ts>-uart.log" (directory is created if missing)
            - Anything else → treated as the exact file path
    """
    if transport.name != "serial":
        return fail(
            code="TRANSPORT_NOT_SUPPORTED",
            message=f"capture_uart requires serial transport, got {transport.name}",
            suggestion="Run: alb setup serial (method G)",
            category="transport",
        )
    if duration < 1 or duration > 3600:
        return fail(
            code="INVALID_DURATION",
            message=f"duration must be 1..3600, got {duration}",
            suggestion="Use a value between 1 and 3600 seconds",
            category="input",
        )

    artifact = _resolve_capture_path(
        output,
        f"{iso_timestamp()}-uart.log",
        device=device,
    )

    start = perf_counter()
    deadline = start + duration
    stats = _LineStats()
    try:
        async with asyncio.timeout(duration + 5):
            await _drain_stream(
                _reconnecting_serial_stream(
                    transport,
                    "uart",
                    deadline_perf=deadline,
                ),
                artifact,
                stats,
                max_seconds=duration,
                line_parser=_parse_dmesg_line,
                topic="uart.line",
            )
    except TimeoutError:
        pass

    duration_ms = int((perf_counter() - start) * 1000)
    return ok(
        data=DmesgSummary(
            lines=stats.lines,
            errors=stats.errors,
            duration_captured_ms=duration_ms,
        ),
        artifacts=[artifact],
        timing_ms=duration_ms,
    )


async def send_uart(
    transport: Transport,
    text: str,
    *,
    append_newline: bool = True,
) -> Result[dict[str, Any]]:
    """Fire-and-forget write to the UART console (no prompt wait).

    Use for u-boot interrupt sequences (e.g. text="\\x03" to send Ctrl-C and
    stop autoboot), sending a single keypress, or injecting a command where
    there is no shell prompt to wait on. For send-and-read-the-response, use the
    shell path (`alb_uart_shell`) — its state machine waits for the prompt.

    LLM notes:
        - `append_newline=True` (default) appends "\\n" so "printenv" runs.
          Set False for raw control bytes / interrupt chars.
        - Returns immediately; it does NOT read the response. Follow with
          `alb_uart_capture` or `alb_uart_shell` to see output.
        - ok=True means the bytes reached the UART, NOT that the board acted on
          them — a console that is not at a prompt silently discards input. When
          you need proof a command ran, use `alb_uart_shell` (it waits for the
          prompt and returns a real exit code).
    """
    if transport.name != "serial":
        return fail(
            code="TRANSPORT_NOT_SUPPORTED",
            message=f"send_uart requires serial transport, got {transport.name}",
            suggestion="Run: alb setup serial (method G)",
            category="transport",
        )
    payload = (text + "\n") if append_newline else text
    raw = payload.encode("utf-8", errors="replace")
    r = await transport.send_raw(raw)
    if not r.ok:
        return fail(
            code=r.error_code or "UART_SEND_FAILED",
            message=r.stderr or "UART write failed",
            suggestion="Check the serial link (alb doctor)",
            category="transport",
        )
    return ok(data={"sent_bytes": len(raw), "appended_newline": append_newline})


async def watch_uart_panic(
    transport: Transport,
    *,
    duration: int = 60,
    device: str | None = None,
    output: Path | str | None = None,
) -> Result[dict[str, Any]]:
    """Capture UART for up to `duration`s and report whether a kernel panic /
    fatal Oops appeared, with the crash tail.

    Reuses the panic markers from the serial state machine (DEFAULT_PATTERNS),
    so detection stays in lockstep with `transport.shell`'s panic routing.

    LLM notes:
        - `panic_detected` is the headline; `tail` is the text from the marker
          onward (capped). Full log is at result.artifacts[0].
        - Captures the whole window then scans (it does not return early on the
          panic); size `duration` to cover the boot / repro you expect.
    """
    cap = await capture_uart(transport, duration=duration, device=device, output=output)
    if not cap.ok:
        err = cap.error
        return fail(
            code=err.code if err else "UART_CAPTURE_FAILED",
            message=err.message if err else "UART capture failed",
            suggestion=err.suggestion if err else "",
            category=err.category if err else "transport",
        )

    artifact = cap.artifacts[0] if cap.artifacts else None
    panic_detected = False
    marker: str | None = None
    tail = ""
    if artifact is not None:
        data = await asyncio.to_thread(artifact.read_bytes)
        m = re.compile(DEFAULT_PATTERNS["panic"]).search(data)
        if m is not None:
            panic_detected = True
            marker = m.group(0).decode("utf-8", errors="replace")
            tail = data[m.start() : m.start() + 8192].decode("utf-8", errors="replace")

    summary = cap.data
    return ok(
        data={
            "panic_detected": panic_detected,
            "marker": marker,
            "tail": tail,
            "lines": summary.lines if summary else 0,
        },
        artifacts=cap.artifacts,
        timing_ms=cap.timing_ms,
    )


async def collect_dmesg(
    transport: Transport,
    *,
    duration: int = 10,
    device: str | None = None,
    output: Path | str | None = None,
) -> Result[DmesgSummary]:
    """Collect kernel dmesg into a workspace file.

    Args:
        output: Optional override for the artifact path (same rules as
            `capture_uart` / `collect_logcat` / `_resolve_capture_path`).
    """
    if duration < 1 or duration > 3600:
        return fail(
            code="INVALID_DURATION",
            message=f"duration must be 1..3600, got {duration}",
            suggestion="Use a value between 1 and 3600 seconds",
            category="input",
        )

    artifact = _resolve_capture_path(
        output,
        f"{iso_timestamp()}-dmesg.txt",
        device=device,
    )

    start = perf_counter()
    stats = _LineStats()
    try:
        async with asyncio.timeout(duration + 5):
            await _drain_stream(
                transport.stream_read("dmesg"),
                artifact,
                stats,
                max_seconds=duration,
                line_parser=_parse_dmesg_line,
                topic="dmesg.line",
            )
    except TimeoutError:
        pass

    duration_ms = int((perf_counter() - start) * 1000)
    return ok(
        data=DmesgSummary(
            lines=stats.lines,
            errors=stats.errors,
            duration_captured_ms=duration_ms,
        ),
        artifacts=[artifact],
        timing_ms=duration_ms,
    )


# ─── Search / tail ─────────────────────────────────────────────────
# Hard cap on the wall-clock time `search_logs` can spend in the scan
# loop.  ``re.search`` against an attacker-controlled pattern is a known
# ReDoS vector (e.g. ``(a+)+$``); we can't reliably cancel a single
# `re.search` C-call mid-execution from Python, but we can:
#   - run the scan in a worker thread (event loop stays responsive)
#   - bound the async wait via `asyncio.wait_for`
#   - check a deadline between lines to short-circuit non-pathological
#     slow scans (millions of lines)
# Worst-case: a single ReDoS line leaks one thread per malicious request
# until the regex C-call finishes.  Acceptable for a dev tool; revisit
# if we ever expose this endpoint without auth on a public network.
_SEARCH_TIMEOUT_S = 2.0


def _scan_files_for_pattern(
    regex: re.Pattern[str],
    files: list[Path],
    max_matches: int,
    deadline: float,
) -> tuple[list[SearchMatch], bool, bool]:
    """Sync scan helper run via ``asyncio.to_thread``.

    Returns ``(matches, truncated, timed_out)``. The deadline check
    between lines short-circuits long scans; a single pathological
    ``regex.search()`` line is still bounded only by the outer
    ``asyncio.wait_for`` (thread may leak).
    """
    matches: list[SearchMatch] = []
    truncated = False
    for fp in files:
        if perf_counter() > deadline:
            return matches, truncated, True
        try:
            with fp.open("r", encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f, start=1):
                    if perf_counter() > deadline:
                        return matches, truncated, True
                    if regex.search(line):
                        matches.append(
                            SearchMatch(
                                path=str(fp),
                                line_number=i,
                                content=line.rstrip("\n"),
                            )
                        )
                        if len(matches) >= max_matches:
                            truncated = True
                            break
        except OSError:
            continue
        if truncated:
            break
    return matches, truncated, False


async def search_logs(
    pattern: str,
    *,
    path: Path | None = None,
    device: str | None = None,
    max_matches: int = 200,
) -> Result[SearchResults]:
    """Grep-style search across workspace logs.

    If `path` is None, searches all files under workspace/devices/<serial>/logs/
    (or all devices if `device` is None).

    ReDoS guard: the actual scan runs in a worker thread with a hard
    ``_SEARCH_TIMEOUT_S`` cap via :func:`asyncio.wait_for`.  Pathological
    patterns ``(a+)+$`` against a 1 KB line still bound the request to
    ``_SEARCH_TIMEOUT_S`` from the caller's perspective.
    """
    try:
        regex = re.compile(pattern)
    except re.error as e:
        return fail(
            code="INVALID_FILTER",
            message=f"Invalid regex: {e}",
            suggestion="Check pattern syntax; escape special chars",
            category="input",
        )

    try:
        files = _resolve_search_targets(path, device)
    except InvalidDeviceSerial as e:
        return fail(
            code="INVALID_DEVICE",
            message=str(e),
            suggestion="Use a valid device serial (alnum + . : - _; <= 64 chars)",
            category="input",
        )

    deadline = perf_counter() + _SEARCH_TIMEOUT_S
    try:
        matches, truncated, timed_out = await asyncio.wait_for(
            asyncio.to_thread(_scan_files_for_pattern, regex, files, max_matches, deadline),
            timeout=_SEARCH_TIMEOUT_S + 0.5,  # +0.5s slack for thread join
        )
    except TimeoutError:
        return fail(
            code="PATTERN_TIMEOUT",
            message=(
                f"regex search exceeded {_SEARCH_TIMEOUT_S}s — pattern may be "
                f"pathological (ReDoS) or workspace is too large"
            ),
            suggestion="Simplify the regex or narrow with --device / --path",
            category="timeout",
        )

    if timed_out:
        return fail(
            code="PATTERN_TIMEOUT",
            message=(f"regex search exceeded {_SEARCH_TIMEOUT_S}s during scan"),
            suggestion="Simplify the regex or narrow with --device / --path",
            category="timeout",
        )

    return ok(data=SearchResults(pattern=pattern, matches=matches, truncated=truncated))


async def tail_log(
    path: Path,
    *,
    lines: int = 50,
    from_line: int | None = None,
    to_line: int | None = None,
) -> Result[str]:
    """Read lines from a log file. By default the last `lines` lines.

    For ranged reads pass from_line/to_line (1-based inclusive).
    Path must be inside workspace (prevents traversal).
    """
    path = path.resolve()
    root = workspace_root().resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return fail(
            code="REMOTE_PATH_INVALID",
            message=f"Path outside workspace: {path}",
            suggestion=f"Logs must be under {root}",
            category="io",
        )

    if not path.exists():
        return fail(
            code="FILE_NOT_FOUND",
            message=f"Log file not found: {path}",
            suggestion="Run: alb log list",
            category="io",
        )

    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            content = f.readlines()
    except OSError as e:
        return fail(
            code="FILE_NOT_READABLE",
            message=str(e),
            suggestion="Check file permissions",
            category="io",
        )

    if from_line is not None or to_line is not None:
        lo = max(0, (from_line or 1) - 1)
        hi = to_line if to_line is not None else len(content)
        selected = content[lo:hi]
    else:
        selected = content[-lines:]

    return ok(data="".join(selected))


# ─── Internal helpers ──────────────────────────────────────────────
@dataclass
class _LineStats:
    lines: int = 0
    errors: int = 0
    warnings: int = 0
    tag_counts: Counter[str] = field(default_factory=Counter)
    first_ts: str = ""
    last_ts: str = ""

    def update_logcat(self, parsed: dict[str, str]) -> None:
        self.lines += 1
        level = parsed.get("level", "")
        if level in ("E", "F"):
            self.errors += 1
        elif level == "W":
            self.warnings += 1
        tag = parsed.get("tag", "").strip()
        if tag:
            self.tag_counts[tag] += 1
        ts = parsed.get("date", "") + "T" + parsed.get("time", "")
        if not self.first_ts and ts.strip("T"):
            self.first_ts = ts
        if ts.strip("T"):
            self.last_ts = ts

    def update_dmesg(self, parsed: dict[str, str]) -> None:
        self.lines += 1
        if parsed.get("is_error"):
            self.errors += 1

    def top_tags(self, *, limit: int = 10) -> list[tuple[str, int]]:
        return self.tag_counts.most_common(limit)


def _parse_logcat_line(line: bytes) -> dict[str, str]:
    text = line.decode("utf-8", errors="replace")
    m = _LOGCAT_THREADTIME_RE.match(text)
    if not m:
        return {}
    return m.groupdict()


def _parse_dmesg_line(line: bytes) -> dict[str, str]:
    text = line.decode("utf-8", errors="replace").lower()
    is_error = any(kw in text for kw in ("error", "panic", "oops", "bug:", "fail", "warn"))
    return {"is_error": "1" if is_error else ""}


async def _drain_stream(
    stream_iter: Any,
    out_file: Path,
    stats: _LineStats,
    *,
    max_seconds: int,
    line_parser: Any,
    topic: str,
) -> None:
    """Write the stream to `out_file` while updating `stats` until timeout."""
    start = perf_counter()
    event_bus = bus()
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with out_file.open("wb") as f:
        async for chunk in stream_iter:
            f.write(chunk)
            parsed = line_parser(chunk)
            if parsed:
                if topic == "logcat.line":
                    stats.update_logcat(parsed)
                elif topic in ("dmesg.line", "uart.line"):
                    stats.update_dmesg(parsed)
            # Fan-out to any subscribers (CLI printer, Web UI, etc.)
            await event_bus.publish(topic, chunk)
            if perf_counter() - start >= max_seconds:
                break


_RECONNECT_BACKOFF_S = 0.5


async def _reconnecting_serial_stream(
    transport: Transport,
    source: str,
    *,
    deadline_perf: float | None = None,
    backoff_s: float = _RECONNECT_BACKOFF_S,
    **kwargs: Any,
) -> AsyncIterator[bytes]:
    """Iterate ``transport.stream_read(source)``, reconnecting on early EOF.

    ``deadline_perf`` (a ``perf_counter()`` value) is an optional soft cap —
    when reached, the iterator returns. Pass ``None`` for open-ended use
    (e.g. the WebSocket live stream, which runs until the client closes).

    Why: TCP UART bridges (ser2net / windows_serial_bridge / socat) commonly
    close the client connection when the COM port has no data — making a
    naive ``async for chunk in transport.stream_read("uart")`` return in
    ~100 ms even when the caller wants a long-running capture or a live
    console. The dominant CLI workflow is "start capture, *then* reboot
    the board"; the dominant Web workflow is "open the UART tab, *then*
    reboot the board". Both need the read side to keep retrying until
    real bytes arrive. See ``BUG_serial_capture_idle_auto_exit.md`` for
    the field report.

    Backoff: a constant ``backoff_s`` sleep between reconnect attempts (no
    exponential growth — we want responsiveness when the bridge starts
    flowing data again; outer cancellation / timeout is the hard cap).
    """

    def _expired() -> bool:
        return deadline_perf is not None and perf_counter() >= deadline_perf

    while not _expired():
        try:
            async for chunk in transport.stream_read(source, **kwargs):
                yield chunk
                if _expired():
                    return
        except (asyncio.CancelledError, GeneratorExit):
            raise
        except Exception:
            # Outer caller (asyncio.timeout / WS cancel) is the hard bound;
            # here we just want to keep trying until the soft deadline.
            pass
        # Inner iterator exhausted or raised. Short backoff before next
        # attempt — without it we'd spin the CPU when the bridge EOF-loops.
        if deadline_perf is not None:
            remaining = deadline_perf - perf_counter()
            if remaining <= 0:
                return
            await asyncio.sleep(min(backoff_s, remaining))
        else:
            await asyncio.sleep(backoff_s)


# NB: `_resolve_capture_path` was promoted to `infra/workspace.resolve_capture_path`
# (N=4 callers across logging + diagnose). Keep the private alias for any
# external code that imported the underscore name historically.
_resolve_capture_path = resolve_capture_path


def _resolve_search_targets(path: Path | None, device: str | None) -> list[Path]:
    """Build the list of `*.txt` files to search.

    L-035: `device` is a user-input vector (MCP `alb_log_search` accepts
    arbitrary serial; CLI `--device` flag); reject `..` / absolute paths
    / etc at root layer rather than letting `(root / "devices" / device
    / "logs").rglob(...)` escape via `..`. base.resolve() flatten gotcha
    applies here too — the rglob walk would happily enumerate files in
    the escaped target.
    """
    if path is not None:
        return [path] if path.is_file() else sorted(path.rglob("*.txt"))
    root = workspace_root()
    if device:
        if not is_safe_device(device):
            raise InvalidDeviceSerial(
                f"invalid device {device!r}: must match [A-Za-z0-9._:-]{{1,64}} "
                f"with alnum leading char (rejected to prevent path traversal)"
            )
        return sorted((root / "devices" / device / "logs").rglob("*.txt"))
    return sorted((root / "devices").rglob("*.txt"))
