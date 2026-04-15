"""logging capability — collect logcat / dmesg / uart and search / tail.

See docs/capabilities/logging.md for the full spec.
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any

from alb.infra.events import bus
from alb.infra.result import Result, fail, ok
from alb.infra.workspace import iso_timestamp, workspace_path, workspace_root
from alb.transport.base import Transport


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
    filter: str | None = None,  # noqa: A002 — mirrors `logcat -s ...` terminology
    tags: list[str] | None = None,
    clear_before: bool = False,
    device: str | None = None,
) -> Result[LogcatSummary]:
    """Collect logcat for N seconds into a workspace file.

    LLM: returns a summary (lines/errors/warnings). Full log is in
    `result.artifacts[0]`; use `search_logs` or `tail_log` to read it.
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

    artifact = workspace_path(
        "logs",
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
    except asyncio.TimeoutError:
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
async def collect_dmesg(
    transport: Transport,
    *,
    duration: int = 10,
    device: str | None = None,
) -> Result[DmesgSummary]:
    if duration < 1 or duration > 3600:
        return fail(
            code="INVALID_DURATION",
            message=f"duration must be 1..3600, got {duration}",
            suggestion="Use a value between 1 and 3600 seconds",
            category="input",
        )

    artifact = workspace_path(
        "logs",
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
    except asyncio.TimeoutError:
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

    files = _resolve_search_targets(path, device)
    matches: list[SearchMatch] = []
    truncated = False

    for fp in files:
        try:
            with fp.open("r", encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f, start=1):
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

    return ok(
        data=SearchResults(pattern=pattern, matches=matches, truncated=truncated)
    )


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
    is_error = any(
        kw in text for kw in ("error", "panic", "oops", "bug:", "fail", "warn")
    )
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
                elif topic == "dmesg.line":
                    stats.update_dmesg(parsed)
            # Fan-out to any subscribers (CLI printer, Web UI, etc.)
            await event_bus.publish(topic, chunk)
            if perf_counter() - start >= max_seconds:
                break


def _resolve_search_targets(path: Path | None, device: str | None) -> list[Path]:
    if path is not None:
        return [path] if path.is_file() else sorted(path.rglob("*.txt"))
    root = workspace_root()
    if device:
        return sorted((root / "devices" / device / "logs").rglob("*.txt"))
    return sorted((root / "devices").rglob("*.txt"))
