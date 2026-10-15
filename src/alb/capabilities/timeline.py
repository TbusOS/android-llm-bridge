"""timeline capability — logcat + dmesg + UART merged on one clock.

Why this exists: when one problem is debugged over adb and UART at the same
time (a driver that hangs: logcat shows the app side, dmesg the kernel,
the console shows early boot and the moments adb is gone), today each
stream lands in its own file and nobody can line them up afterwards. Raw
UART bytes carry no timestamps at all, and the timestamps inside logcat
(wall clock) and dmesg (seconds since boot) are on different clocks. The
ordering information exists only at the moment the bytes arrive, so the
merge has to happen at capture time — exactly what `remote/flash_timeline`
already does for flash jobs (ADR-056 decision 4). This module generalises
that: every line from every source is stamped with the host's monotonic
clock the instant it arrives and appended to ONE `timeline.jsonl`.

Artifacts (one directory per capture):

    timeline.jsonl   {"t": 1.234567, "wall": "...", "src": "uart", "line": "..."}
    uart.log         raw bytes, untouched (line-splitting is lossy for a
                     bootloader; the JSONL is for reasoning, this is evidence)
    logcat.txt       raw bytes as received
    dmesg.txt        raw bytes as received
    summary.json     counts / notes / duration, written when the capture ends

The JSONL is flushed per received chunk, so a capture killed half-way still
leaves a usable file. Records are stamped at ARRIVAL, and a partial line
flushed on idle is written up to `IDLE_FLUSH_S` after its stamp, so the file
is "nearly sorted": `iter_timeline` re-sorts with a bounded window rather
than loading the file.
"""

from __future__ import annotations

import asyncio
import contextlib
import heapq
import inspect
import json
import time
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable, Collection, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any, BinaryIO, Final, TextIO

from alb.capabilities.logging import _reconnecting_serial_stream
from alb.infra.events import bus
from alb.infra.result import ErrorInfo, Result, fail, ok
from alb.infra.workspace import InvalidDeviceSerial, iso_timestamp, workspace_path
from alb.transport.base import Transport

ALL_SOURCES: Final[tuple[str, ...]] = ("logcat", "dmesg", "uart")

# How long an incomplete trailing fragment (no newline yet) waits before it
# is written out anyway. A bootloader prompt such as "=> " never gets its
# newline; holding it back would hide the single most informative line.
# 200 ms is longer than the gap between two bytes of one line at any sane
# baud, and shorter than a human notices.
IDLE_FLUSH_S: Final[float] = 0.2

# A "line" longer than this is not a line — it is a binary blob arriving on
# a console at the wrong baud. Cut it so one such run cannot grow without
# bound in memory; the raw file still holds every byte.
_MAX_LINE: Final[int] = 8192

# The synthetic marker `SerialTransport.stream_read` yields when the port /
# bridge cannot be opened. It is alb talking, not the board, so it is turned
# into one note instead of being written to uart.log as if the board said it.
_SERIAL_OPEN_FAILED: Final[bytes] = b"[alb serial open failed:"

# Records may trail their stamp by at most IDLE_FLUSH_S (see module doc).
# `iter_timeline` holds this much look-ahead before emitting; anything larger
# only costs memory, anything smaller than IDLE_FLUSH_S would misorder.
_REORDER_WINDOW_S: Final[float] = 2.0

_RAW_FILE: Final[dict[str, str]] = {
    "logcat": "logcat.txt",
    "dmesg": "dmesg.txt",
    "uart": "uart.log",
}

# Fan-out topics, the same ones `capabilities/logging._drain_stream` uses, so
# whoever already listens to a plain capture (web UI, CLI printer) sees this
# capture too without learning a new topic.
_TOPIC: Final[dict[str, str]] = {
    "logcat": "logcat.line",
    "dmesg": "dmesg.line",
    "uart": "uart.line",
}

# `Transport.stream_read` is declared as a coroutine that returns an iterator
# while every implementation is an async generator; accept both shapes so a
# factory can simply return `transport.stream_read(...)`.
StreamFactory = Callable[[], AsyncIterator[bytes] | Awaitable[AsyncIterator[bytes]]]


# ─── Models ────────────────────────────────────────────────────────
@dataclass(frozen=True)
class TimelineSummary:
    directory: Path
    sources: list[str]
    lines: dict[str, int]
    notes: list[str] = field(default_factory=list)
    duration_captured_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "directory": str(self.directory),
            "timeline": str(self.directory / "timeline.jsonl"),
            "sources": list(self.sources),
            "lines": dict(self.lines),
            "notes": list(self.notes),
            "duration_captured_ms": self.duration_captured_ms,
        }


# ─── Writer: one clock, one file ───────────────────────────────────
class _TimelineWriter:
    """Owns `timeline.jsonl`. Every stamp comes from one monotonic clock.

    `wall` is derived from the start wall time plus the monotonic offset,
    not read live per line: if the host clock is stepped mid-capture, `t`
    and `wall` would otherwise disagree about the order of two lines.
    """

    def __init__(self, path: Path) -> None:
        self._f: TextIO = path.open("w", encoding="utf-8")
        self._t0_ns = time.monotonic_ns()
        self._wall0 = datetime.now(UTC)
        self.lines: Counter[str] = Counter()
        self.notes: list[str] = []
        # Sources that could not be read when last heard of — what tells
        # "nothing could be captured" apart from "the board was quiet".
        self.missing: set[str] = set()
        self.closed = False

    def now_ns(self) -> int:
        return time.monotonic_ns() - self._t0_ns

    def _stamp(self, at_ns: int) -> tuple[float, str]:
        wall = self._wall0 + timedelta(microseconds=at_ns // 1000)
        return round(at_ns / 1e9, 6), wall.isoformat(timespec="milliseconds")

    def _write(self, obj: dict[str, Any]) -> None:
        if self.closed:
            return  # an idle timer firing after the capture ended
        self._f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def flush(self) -> None:
        if not self.closed:
            self._f.flush()

    def meta(self, ev: str, **extra: Any) -> None:
        t, wall = self._stamp(self.now_ns())
        self._write({"t": t, "wall": wall, "src": "meta", "ev": ev, **extra})
        self.flush()

    def line(
        self,
        src: str,
        text: str,
        *,
        at_ns: int,
        partial: bool = False,
        truncated: bool = False,
    ) -> None:
        t, wall = self._stamp(at_ns)
        rec: dict[str, Any] = {"t": t, "wall": wall, "src": src, "line": text}
        if partial:
            rec["partial"] = True  # no newline yet — likely a prompt
        if truncated:
            rec["truncated"] = True
        self._write(rec)
        self.lines[src] += 1

    def note(self, src: str, text: str) -> None:
        """alb's own commentary about `src`, in the same stream so a reader
        sees WHEN a source went missing relative to the others."""
        t, wall = self._stamp(self.now_ns())
        self._write({"t": t, "wall": wall, "src": src, "line": text, "note": True})
        self.notes.append(text)
        self.flush()

    def unavailable(self, src: str, reason: str) -> None:
        self.missing.add(src)
        self.note(src, f"[alb] {src} unavailable: {reason}")

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        with contextlib.suppress(Exception):
            self._f.close()


# ─── Per-source line assembly ──────────────────────────────────────
class _LineAssembler:
    """Turns a byte stream into stamped lines for one source.

    Chunks are not lines: adb yields one line per chunk, a serial link
    yields whatever bytes arrived (several lines, or half of one). Complete
    lines are emitted at once; the trailing fragment waits for more bytes
    or for `idle_flush_s` of silence.
    """

    def __init__(
        self,
        src: str,
        writer: _TimelineWriter,
        raw: BinaryIO | None,
        *,
        idle_flush_s: float,
    ) -> None:
        self.src = src
        self._writer = writer
        self._raw = raw
        self._idle_flush_s = idle_flush_s
        self._buf = bytearray()
        self._last_ns = 0
        self._seq = 0
        self._timer: asyncio.TimerHandle | None = None
        self.lines = 0

    def feed(self, chunk: bytes) -> None:
        at_ns = self._writer.now_ns()
        if self._raw is not None:
            # Raw first, verbatim: evidence before interpretation. Flushed per
            # chunk so `tail -f` on the raw file keeps up with the board.
            self._raw.write(chunk)
            self._raw.flush()
        self._buf.extend(chunk)
        self._last_ns = at_ns
        self._seq += 1
        while True:
            idx = self._buf.find(b"\n")
            if idx < 0:
                if len(self._buf) > _MAX_LINE:
                    self._emit(bytes(self._buf[:_MAX_LINE]), at_ns, truncated=True)
                    del self._buf[:_MAX_LINE]
                    continue
                break
            line = bytes(self._buf[:idx])
            del self._buf[: idx + 1]
            self._emit(line, at_ns)
        self._writer.flush()  # per chunk, not per line: one batch, one flush
        self._arm_idle_timer()

    def _arm_idle_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if not self._buf:
            return
        # `call_later` rather than wrapping the source's `__anext__` in
        # `wait_for`: a timeout there would cancel the async generator and
        # kill the stream, which is the opposite of what an idle flush wants.
        loop = asyncio.get_running_loop()
        self._timer = loop.call_later(self._idle_flush_s, self._on_idle, self._seq)

    def _on_idle(self, seq: int) -> None:
        self._timer = None
        if seq != self._seq or not self._buf:
            return  # more bytes arrived in the meantime; a new timer is armed
        self.flush_partial()
        self._writer.flush()

    def flush_partial(self) -> None:
        if self._buf:
            self._emit(bytes(self._buf), self._last_ns, partial=True)
            self._buf.clear()

    def _emit(
        self, raw: bytes, at_ns: int, *, partial: bool = False, truncated: bool = False
    ) -> None:
        text = raw.replace(b"\r", b"").decode("utf-8", errors="replace")
        if not text and not partial:
            return  # bare newline: nothing to reason about (raw file keeps it)
        self._writer.line(self.src, text, at_ns=at_ns, partial=partial, truncated=truncated)
        self.lines += 1

    def close(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self.flush_partial()
        if self._raw is not None:
            with contextlib.suppress(Exception):
                self._raw.close()


# ─── Source runner ─────────────────────────────────────────────────
async def _open_stream(factory: StreamFactory) -> AsyncIterator[bytes]:
    stream = factory()
    if inspect.isawaitable(stream):
        return await stream
    return stream


_EARLY_END_HINT: Final[dict[str, str]] = {
    "dmesg": " (dmesg -w needs root: run `adb root` or `alb shell su 0 id` first)",
    "logcat": " (is a device attached? check `alb devices`)",
    "uart": "",
}


async def _run_source(
    src: str,
    factory: StreamFactory,
    assembler: _LineAssembler,
    writer: _TimelineWriter,
    *,
    deadline_perf: float,
) -> None:
    """Pump one source until it ends, fails, or the capture is cancelled.

    A source that dies is noted in the timeline and that is all: one missing
    source must never abort the capture — the other two are the reason the
    user is here.
    """
    event_bus = bus()
    topic = _TOPIC.get(src, f"{src}.line")
    serial_down = False
    try:
        async for chunk in await _open_stream(factory):
            if src == "uart" and chunk.startswith(_SERIAL_OPEN_FAILED):
                if not serial_down:
                    reason = chunk.decode("utf-8", errors="replace").strip()
                    writer.unavailable(src, reason)
                    serial_down = True
                continue  # the reconnect loop will keep retrying; say it once
            if serial_down:
                writer.missing.discard(src)
                writer.note(src, "[alb] uart link up")
                serial_down = False
            assembler.feed(chunk)
            await event_bus.publish(topic, chunk)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        # Any failure of one source becomes a note; the others keep going.
        writer.unavailable(src, f"{type(e).__name__}: {e}")
        return
    if perf_counter() < deadline_perf - 0.05:
        # Ended on its own before the capture did. stderr of the child is
        # discarded by `spawn_stream`, so the reason is not available here;
        # say what was seen and the usual cause.
        if assembler.lines == 0:
            writer.note(
                src,
                f"[alb] {src} unavailable: stream ended with no output"
                f"{_EARLY_END_HINT.get(src, '')}",
            )
        else:
            writer.note(src, f"[alb] {src} stream ended early after {assembler.lines} lines")


# ─── Capture ───────────────────────────────────────────────────────
async def capture_timeline(
    duration_s: float,
    *,
    sources: Collection[str] = ALL_SOURCES,
    transport: Transport | None = None,
    serial: Transport | None = None,
    device: str | None = None,
    output: Path | str | None = None,
    streams: Mapping[str, StreamFactory] | None = None,
    logcat_filter: str | None = None,
    idle_flush_s: float = IDLE_FLUSH_S,
) -> Result[TimelineSummary]:
    """Capture the requested sources concurrently into one timeline directory.

    Args:
        duration_s: how long to capture (0 < s <= 3600).
        sources: any subset of ``logcat`` / ``dmesg`` / ``uart``.
        transport: adb / ssh transport for logcat and dmesg. ``None`` is
            noted in the timeline, not an error.
        serial: serial transport for uart. ``None`` is noted, not an error.
        device: device serial, selects ``workspace/devices/<device>/timeline/``.
        output: directory to write into instead of the workspace.
        streams: per-source byte-stream factories that replace the transport
            for that source (tests, or any caller with its own byte source).
        idle_flush_s: silence after which a trailing fragment is written out.

    LLM notes:
        - Read ``<dir>/timeline.jsonl`` (or ``alb timeline show <dir>``), not
          the raw files, when the question is "what happened in what order".
        - A line starting with ``[alb]`` is alb commenting on a source (went
          missing, came back, ended early), stamped like everything else.
    """
    if not duration_s > 0 or duration_s > 3600:
        return fail(
            code="INVALID_DURATION",
            message=f"duration must be > 0 and <= 3600 seconds, got {duration_s}",
            suggestion="Use a value between 1 and 3600 seconds",
            category="input",
        )
    wanted = [s for s in ALL_SOURCES if s in sources]
    unknown = sorted(set(sources) - set(ALL_SOURCES))
    if unknown or not wanted:
        return fail(
            code="INVALID_SOURCE",
            message=(
                f"unknown source(s): {', '.join(unknown)}" if unknown else "no source requested"
            ),
            suggestion=f"Choose at least one of: {', '.join(ALL_SOURCES)}",
            category="input",
        )

    try:
        directory = _capture_dir(output, device)
    except InvalidDeviceSerial as e:
        return fail(
            code="INVALID_DEVICE_SERIAL",
            message=str(e),
            suggestion="Use a serial returned by alb devices",
            category="input",
        )

    start = perf_counter()
    deadline = start + duration_s
    writer = _TimelineWriter(directory / "timeline.jsonl")
    writer.meta("start", sources=wanted, device=device, duration_s=duration_s)

    factories = await _pick_factories(
        wanted,
        writer,
        transport=transport,
        serial=serial,
        streams=streams,
        logcat_filter=logcat_filter,
        deadline_perf=deadline,
    )

    assemblers: dict[str, _LineAssembler] = {}
    tasks: list[asyncio.Task[None]] = []
    try:
        for src, factory in factories.items():
            raw: BinaryIO = (directory / _RAW_FILE[src]).open("wb")
            assembler = _LineAssembler(src, writer, raw, idle_flush_s=idle_flush_s)
            assemblers[src] = assembler
            tasks.append(
                asyncio.create_task(
                    _run_source(src, factory, assembler, writer, deadline_perf=deadline),
                    name=f"timeline-{src}",
                )
            )
        if tasks:
            # Sources that end early just stop; the rest run out the clock.
            # No point sleeping out the duration when nothing is left to read.
            _done, pending = await asyncio.wait(tasks, timeout=duration_s)
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
    finally:
        for assembler in assemblers.values():
            assembler.close()
        duration_ms = int((perf_counter() - start) * 1000)
        writer.meta("end", lines=dict(writer.lines), duration_ms=duration_ms)
        writer.close()

    summary = TimelineSummary(
        directory=directory,
        sources=wanted,
        lines={src: writer.lines.get(src, 0) for src in wanted},
        notes=list(writer.notes),
        duration_captured_ms=duration_ms,
    )
    _write_summary(directory, summary)
    artifacts = [directory / "timeline.jsonl"] + [directory / _RAW_FILE[src] for src in factories]
    if not any(summary.lines.values()) and set(wanted) <= writer.missing:
        # Not ok-with-zero-lines: a caller that reads only `ok` would take
        # "could not read anything" for "the board printed nothing".
        return Result(
            ok=False,
            error=ErrorInfo(
                code="TIMELINE_NO_SOURCE",
                message="none of the requested sources could be read: " + "; ".join(summary.notes),
                suggestion="dmesg usually needs `alb adb root`, uart needs `alb setup serial`, "
                "logcat needs an adb device",
                category="transport",
                details=summary.to_dict(),
            ),
            artifacts=artifacts,
            timing_ms=duration_ms,
        )
    return ok(data=summary, artifacts=artifacts, timing_ms=duration_ms)


async def _pick_factories(
    wanted: list[str],
    writer: _TimelineWriter,
    *,
    transport: Transport | None,
    serial: Transport | None,
    streams: Mapping[str, StreamFactory] | None,
    logcat_filter: str | None,
    deadline_perf: float,
) -> dict[str, StreamFactory]:
    """Decide where each requested source's bytes come from, noting the
    ones that have nowhere to come from. Only sources with a factory get a
    raw file and a task."""
    factories: dict[str, StreamFactory] = {}
    for src in wanted:
        if streams is not None and src in streams:
            factories[src] = streams[src]
            continue
        if src == "uart":
            if serial is None:
                writer.unavailable(src, "serial transport not configured")
                continue
            # The reconnecting reader, not a bare stream_read: TCP UART
            # bridges EOF when the port is idle, and "start capture, then
            # reboot the board" is the whole workflow.
            factories[src] = _serial_factory(serial, deadline_perf)
            continue
        if transport is None:
            writer.unavailable(src, "no adb/ssh transport")
            continue
        if src == "logcat":
            perm = await transport.check_permissions(
                "logging.logcat", {"duration": 0, "filter": logcat_filter}
            )
            if perm.behavior == "deny":
                writer.unavailable(src, perm.reason or "blocked")
                continue
            factories[src] = _stream_factory(transport, "logcat", filter=logcat_filter)
        else:
            factories[src] = _stream_factory(transport, "dmesg")
    return factories


def _capture_dir(output: Path | str | None, device: str | None) -> Path:
    directory = (
        Path(output).expanduser()
        if output is not None
        else workspace_path("timeline", f"capture-{iso_timestamp()}", device=device)
    )
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _write_summary(directory: Path, summary: TimelineSummary) -> None:
    (directory / "summary.json").write_text(
        json.dumps(summary.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _stream_factory(transport: Transport, source: str, **kwargs: Any) -> StreamFactory:
    def factory() -> Awaitable[AsyncIterator[bytes]]:
        return transport.stream_read(source, **kwargs)

    return factory


def _serial_factory(serial: Transport, deadline_perf: float) -> StreamFactory:
    def factory() -> AsyncIterator[bytes]:
        return _reconnecting_serial_stream(serial, "uart", deadline_perf=deadline_perf)

    return factory


# ─── Reading back ──────────────────────────────────────────────────
def timeline_file(path: Path) -> Path:
    """Accept either the capture directory or the jsonl itself."""
    return path / "timeline.jsonl" if path.is_dir() else path


def iter_timeline(
    path: Path,
    *,
    sources: Collection[str] | None = None,
    grep: str | None = None,
    around: float | None = None,
    window: float = 5.0,
) -> Iterator[dict[str, Any]]:
    """Yield line records in time order, streaming.

    Records are stamped at arrival and may be written slightly after their
    stamp (see module doc), so this re-sorts with a bounded look-ahead
    instead of reading the whole file: memory stays bounded for a long
    capture. A half-written last line (capture killed mid-write) is skipped.

    Args:
        sources: keep only these ``src`` values (None = all).
        grep: plain substring that must occur in the line (case-sensitive).
        around / window: keep ``t`` within ``[around - window, around + window]``.
    """
    file = timeline_file(path)
    lo = around - window if around is not None else None
    hi = around + window if around is not None else None
    heap: list[tuple[float, int, dict[str, Any]]] = []
    seq = 0
    with file.open("r", encoding="utf-8") as f:
        for raw in f:
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(rec, dict) or "line" not in rec:
                continue  # meta records are for tooling, not for reading
            if sources is not None and rec.get("src") not in sources:
                continue
            if grep is not None and grep not in str(rec.get("line", "")):
                continue
            t = float(rec.get("t", 0.0))
            if lo is not None and hi is not None and not (lo <= t <= hi):
                continue
            heapq.heappush(heap, (t, seq, rec))
            seq += 1
            while heap and heap[0][0] < t - _REORDER_WINDOW_S:
                yield heapq.heappop(heap)[2]
    while heap:
        yield heapq.heappop(heap)[2]


def format_line(rec: dict[str, Any]) -> str:
    """``+12.345s uart   | <line>`` — plain text, one line per record, so a
    transcript reads the same to a person and to a model."""
    stamp = f"+{float(rec.get('t', 0.0)):.3f}s"
    text = f"{stamp:>10} {rec.get('src', '?')!s:<6} | {rec.get('line', '')}"
    if rec.get("partial"):
        text += " [partial]"
    if rec.get("truncated"):
        text += " [truncated]"
    return text
