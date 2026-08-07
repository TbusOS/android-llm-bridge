"""One timeline for a flash job and the UART it happens on (ADR-056 §决定 4).

The problem this solves: "watch the UART while flashing" is only worth
anything if you can line the two up afterwards. A raw serial stream carries
**no timestamps of its own** — they exist only at the moment of capture. Tell
the caller to run `alb flash` in one terminal and a UART capture in another,
and the ordering information is destroyed right there; no amount of later
processing recovers "what did the board print during the three seconds it
was writing vendor_cfg". That question is the entire reason for watching.

So the merge happens at capture time, into `timeline.jsonl`:

    {"t": 0.000, "src": "job",  "ev": "accepted", ...}
    {"t": 0.412, "src": "uart", "line": "[    0.331] mmc0: ..."}
    {"t": 1.980, "src": "job",  "ev": "progress", "phase": "flash", ...}

One clock, one file, strictly increasing `t`. `uart.log` sits alongside with
the untouched bytes, because a bootloader emits plenty that is not
line-oriented and line-splitting is lossy — the JSONL is for reasoning, the
raw log is for evidence.

Where the UART comes from: the serial forwarder already shares one agent
channel and fans out to every local connection (ADR-054). This attaches as
one more reader, so watching costs nothing and conflicts with nobody — a
human `alb serial capture` can be running at the same time and both see the
same bytes.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from pathlib import Path
from types import TracebackType
from typing import Any

from alb.infra.workspace import iso_timestamp, workspace_path

_log = logging.getLogger(__name__)

_CHUNK = 65536
# Longest a partial line waits before being written out anyway. A bootloader
# can print a prompt with no trailing newline and then go quiet for the whole
# flash; holding that text back would hide the single most informative line.
_PARTIAL_FLUSH_S = 2.0
# A line longer than this is not a line — it is a binary blob arriving on a
# console at the wrong baud. Cut it so one such run cannot grow unbounded in
# memory; `uart.log` still holds every byte.
_MAX_LINE = 8192


def serial_endpoint() -> tuple[str, int] | None:
    """Where to attach as another reader, or None if there is nothing to watch.

    Reads the forwarder singleton WITHOUT creating it: a flash on a bench
    with no UART configured must not have the side effect of binding a port.
    """
    from alb.remote import forwarder

    fwd = forwarder._SERIAL_FORWARDER
    if fwd is None or not fwd.is_bound:
        return None
    return "127.0.0.1", fwd.port


class FlashTimeline:
    """Artifact writer for one job. Not reusable — one job, one directory."""

    def __init__(self, directory: Path, *, device: str | None = None) -> None:
        self.dir = directory
        self.device = device
        self.started_wall = time.time()
        self._t0 = time.monotonic()
        # Long-lived handles, closed in aclose() — a `with` block cannot
        # span the job, which is the whole point of the object.
        self._timeline = open(  # noqa: SIM115
            directory / "timeline.jsonl", "w", encoding="utf-8"
        )
        self._uart_raw: Any = None
        self._task: asyncio.Task[None] | None = None
        self._buf = bytearray()
        self._last_byte_at = 0.0
        self.uart_attached = False
        self.uart_note = ""

    # ── clock ────────────────────────────────────────────────────────

    def _t(self) -> float:
        """Seconds since the job started. Monotonic, so a clock adjustment
        mid-flash cannot reorder the file."""
        return round(time.monotonic() - self._t0, 3)

    def _write(self, obj: dict[str, Any]) -> None:
        self._timeline.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self._timeline.flush()  # a crashed job must still leave its trail

    # ── job side ─────────────────────────────────────────────────────

    def header(self, *, label: str, detail: dict[str, Any]) -> None:
        self._write(
            {
                "t": self._t(),
                "src": "meta",
                "ev": "start",
                "wall": iso_timestamp(),
                "label": label,
                **detail,
            }
        )

    def job_event(self, ev: Any) -> None:
        self._write(
            {
                "t": self._t(),
                "src": "job",
                "ev": "progress",
                "phase": getattr(ev, "phase", ""),
                "done": getattr(ev, "done", 0),
                "total": getattr(ev, "total", 0),
                "text": getattr(ev, "text", ""),
            }
        )

    def job_result(self, result: Any) -> None:
        self._write({"t": self._t(), "src": "job", "ev": "done", **result.as_dict()})
        (self.dir / "job.json").write_text(
            json.dumps(
                {
                    "started": iso_timestamp(),
                    "duration_s": self._t(),
                    "uart_attached": self.uart_attached,
                    "uart_note": self.uart_note,
                    **result.as_dict(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    # ── uart side ────────────────────────────────────────────────────

    async def attach_uart(self) -> None:
        """Start reading the UART, if there is one. Never fatal.

        A flash must not fail because the console could not be watched —
        losing the commentary is bad, losing the flash is worse.
        """
        endpoint = serial_endpoint()
        if endpoint is None:
            self.uart_note = "no serial forwarder bound — nothing to watch"
            self._write({"t": self._t(), "src": "meta", "ev": "uart", "note": self.uart_note})
            return
        host, port = endpoint
        try:
            reader, writer = await asyncio.open_connection(host, port)
        except OSError as e:
            self.uart_note = f"cannot attach to the serial forwarder: {e}"
            self._write({"t": self._t(), "src": "meta", "ev": "uart", "note": self.uart_note})
            return
        # off-loop open (ASYNC230); the per-chunk writes below stay inline —
        # a 115200 console is ~11 KB/s, far under what a thread hop would cost.
        self._uart_raw = await asyncio.to_thread(open, self.dir / "uart.log", "wb")
        self.uart_attached = True
        self._write({"t": self._t(), "src": "meta", "ev": "uart", "note": "attached"})
        self._task = asyncio.create_task(self._pump(reader, writer))

    async def _pump(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                try:
                    data = await asyncio.wait_for(reader.read(_CHUNK), _PARTIAL_FLUSH_S)
                except TimeoutError:
                    self._flush_partial()  # quiet console: emit what we hold
                    continue
                if not data:
                    return
                if self._uart_raw is not None:
                    self._uart_raw.write(data)
                    self._uart_raw.flush()
                self._absorb(data)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            _log.warning("flash timeline: uart reader stopped: %s", e)
        finally:
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()

    def _absorb(self, data: bytes) -> None:
        self._buf.extend(data)
        self._last_byte_at = time.monotonic()
        while True:
            idx = self._buf.find(b"\n")
            if idx < 0:
                if len(self._buf) > _MAX_LINE:
                    self._emit_line(bytes(self._buf[:_MAX_LINE]), truncated=True)
                    del self._buf[:_MAX_LINE]
                    continue
                return
            line = bytes(self._buf[:idx])
            del self._buf[: idx + 1]
            self._emit_line(line)

    def _flush_partial(self) -> None:
        if self._buf:
            self._emit_line(bytes(self._buf), partial=True)
            self._buf.clear()

    def _emit_line(self, raw: bytes, *, partial: bool = False, truncated: bool = False) -> None:
        text = raw.replace(b"\r", b"").decode("utf-8", errors="replace")
        if not text and not partial:
            return  # bare newline: nothing to reason about
        rec: dict[str, Any] = {"t": self._t(), "src": "uart", "line": text}
        if partial:
            rec["partial"] = True  # no newline yet — likely a prompt
        if truncated:
            rec["truncated"] = True
        self._write(rec)

    # ── lifecycle ────────────────────────────────────────────────────

    async def aclose(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        self._flush_partial()
        for handle in (self._uart_raw, self._timeline):
            with contextlib.suppress(Exception):
                if handle is not None:
                    handle.close()

    async def __aenter__(self) -> FlashTimeline:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()


def new_timeline(kind: str, *, device: str | None = None) -> FlashTimeline:
    """Create `workspace/devices/<device>/flash/<kind>-<ts>/` and open it.

    `kind` names the job ("flash-boot", "reboot"), so a directory listing
    reads as a history of what was done to the board rather than a column of
    timestamps.
    """
    # Collapse anything that is not a plain name character. The security
    # property is that the result is ONE path segment (no separator survives);
    # stripping leading dots on top of that is hygiene — a directory called
    # `..-..-etc` traverses nowhere but reads like a bug report.
    safe_kind = "".join(c if c.isalnum() or c in "-_." else "-" for c in kind)
    safe_kind = safe_kind.lstrip(".-")[:48] or "job"
    directory = workspace_path("flash", f"{safe_kind}-{iso_timestamp()}", device=device)
    directory.mkdir(parents=True, exist_ok=True)
    return FlashTimeline(directory, device=device)
