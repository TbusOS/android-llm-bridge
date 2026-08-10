"""Hub-side driver for fastboot jobs (ADR-056).

Deliberately NOT a ChannelForwarder. The adb and serial forwarders own an OS
listener and shuttle bytes between it and the agent; there is no local port
to bind here, because fastboot speaks USB on the agent host and nothing on
this one. What this service owns instead is a *job*: open a channel, send a
structured request, stream the image, consume progress, return a verdict.

Two rules shape the whole file:

* **One job at a time, refused rather than queued** (§决定 3). ADR-054 says an
  exclusively-opened endpoint must be SHARED with fan-out — that was right
  for UART, where the medium is a broadcast and every reader wants the same
  bytes. It is wrong here: two callers flashing one partition has no correct
  meaning. So the lock is exclusive, and a second caller is told "busy" at
  once instead of waiting behind work it cannot see, holding a board it
  believes is about to be written.
* **The hub sends fields, never a command line** (§决定 5). Partition name,
  size, digest. The agent builds its own argv from its own config. A job
  channel that forwarded a command string would be remote execution wearing
  a flashing-tool costume.

Progress is fanned out to whoever is listening (CLI, web, the timeline
writer) via a callback — that half of ADR-054 does apply: many observers of
one job all want the same events.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from alb.remote.jobframe import FrameReader, JobProtocolError, encode_control, encode_data
from alb.remote.protocol import (
    CAP_FASTBOOT,
    ChannelRole,
    ChannelType,
    JobEvent,
    JobPhase,
    job_devices,
    job_flash,
    job_reboot,
)
from alb.remote.registry import DataChannel

if TYPE_CHECKING:
    from alb.remote.flash_timeline import FlashTimeline

_log = logging.getLogger(__name__)

# Image chunk size on the wire. 64 KiB matches the byte-stream forwarders and
# keeps a progress event landing several times a second on a slow tunnel —
# small enough to look alive, large enough not to drown the link in framing.
CHUNK = 64 * 1024

# How long to wait for the agent's dial-back. Longer than the byte channels'
# because a flash request arrives when the board has just been rebooted into
# fastboot and the agent host may still be enumerating USB.
DIAL_BACK_TIMEOUT_S = 30.0

# A job may legitimately take minutes (a large partition write). The ceiling
# exists so a wedged tool cannot hold the lock forever and lock out every
# later caller — the failure mode this is protecting against is not a slow
# flash, it is a permanently busy bench.
JOB_TIMEOUT_S = 30 * 60.0


@dataclass(frozen=True)
class FlashEvent:
    """One progress datum. `total == 0` means indeterminate (fastboot's own
    output is not always quantified) — renderers must not divide by it."""

    phase: str
    done: int = 0
    total: int = 0
    text: str = ""


@dataclass
class FlashResult:
    ok: bool
    rc: int = -1
    code: str = ""
    error: str = ""
    stdout: str = ""
    stderr: str = ""
    duration_s: float = 0.0
    events: list[FlashEvent] = field(default_factory=list)
    # Where the timeline + raw UART landed. Empty when nothing was recorded.
    artifacts: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "rc": self.rc,
            "code": self.code,
            "error": self.error,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_s": round(self.duration_s, 3),
            "artifacts": self.artifacts,
        }


EventSink = Callable[[FlashEvent], None]


def _as_int(value: object, default: int) -> int:
    """Coerce a wire field to int without trusting the peer's typing. A
    malformed `rc` must not turn a completed job into an exception on this
    side — we still need to report what the agent said about the device."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _fail(code: str, error: str) -> FlashResult:
    return FlashResult(ok=False, code=code, error=error)


async def digest_file(path: Path) -> tuple[int, str]:
    """(size, sha256) computed off the event loop.

    Hashing happens here, on the hub, and the digest travels in the opening
    frame — so the agent can refuse a damaged transfer before it touches the
    device (§决定 6). Reading the file twice (once to hash, once to send) is
    the price of not buffering an arbitrarily large image in memory.
    """

    def _work() -> tuple[int, str]:
        h = hashlib.sha256()
        size = 0
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                h.update(chunk)
        return size, h.hexdigest()

    return await asyncio.to_thread(_work)


class FlashService:
    """Process-level singleton (ADR-051 posture: one hub process owns device
    traffic). Resolves the agent lazily, so a reconnecting agent needs no
    re-wiring here."""

    def __init__(self, get_agent: Callable[[], Any | None]) -> None:
        self._get_agent = get_agent
        self._lock = asyncio.Lock()
        self._current: str = ""  # human label of the job in flight, "" = idle

    # ── status surface ───────────────────────────────────────────────

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    @property
    def current(self) -> str:
        return self._current

    def available(self) -> bool:
        """True when an agent is connected AND advertises fastboot.

        Checked from `caps` rather than by trying: the point of the
        capability handshake is that "this bench cannot flash" is an instant
        answer instead of a timeout (§决定 7)."""
        agent = self._get_agent()
        return agent is not None and CAP_FASTBOOT in getattr(agent, "caps", [])

    def partitions(self) -> list[str]:
        """The agent's own allowlist. Empty = it configured none, which means
        "any well-formed name" — NOT "none allowed". Callers that render a
        picker must treat empty as "we don't know, offer something generic",
        because offering an empty list is indistinguishable from a bench that
        cannot flash at all."""
        agent = self._get_agent()
        return list(getattr(agent, "flash_partitions", []) or []) if agent else []

    def status(self) -> dict[str, Any]:
        return {
            "available": self.available(),
            "busy": self.busy,
            "job": self._current,
            "partitions": self.partitions(),
        }

    # ── operations ───────────────────────────────────────────────────

    async def flash(
        self, partition: str, image: Path, *, on_event: EventSink | None = None
    ) -> FlashResult:
        """Stream `image` to the agent and have it flash `partition`."""
        try:
            size, sha = await digest_file(image)
        except OSError as e:
            return _fail("FLASH_IMAGE_CORRUPT", f"cannot read {image}: {e}")
        if size == 0:
            return _fail("FLASH_IMAGE_CORRUPT", f"{image} is empty")

        async def send_image(channel: DataChannel) -> None:
            await _stream_file(channel, image)

        return await self._run(
            label=f"flash {partition}",
            request=job_flash(partition=partition, size=size, sha256=sha),
            on_event=on_event,
            after_request=send_image,
        )

    async def reboot(self, target: str = "", *, on_event: EventSink | None = None) -> FlashResult:
        """`fastboot reboot [target]`. With no target this is the way back to
        the system — the direct remedy for a board alb itself pushed into
        fastboot and could not previously get out of."""
        return await self._run(
            label=f"reboot {target or 'normal'}",
            request=job_reboot(target=target),
            on_event=on_event,
            # No UART: the command returns in ~0.1 s, long before the board
            # has said anything worth correlating. The boot log people
            # actually want arrives afterwards and belongs to a capture.
            watch_uart=False,
        )

    async def devices(self, *, on_event: EventSink | None = None) -> FlashResult:
        """`fastboot devices` on the agent host — a board in fastboot has
        vanished from adb, so this is the only way to see it."""
        return await self._run(
            label="devices",
            request=job_devices(),
            on_event=on_event,
            # Records nothing: a ~60 ms query whose whole answer is its return
            # value, and the op most likely to be POLLED. Recording used to
            # mean opening the board's PHYSICAL serial port on every single
            # call — a poll loop of these wedged a live agent (keepalive ping
            # timeout, 2026-08-10).
            record=False,
        )

    # ── plumbing ─────────────────────────────────────────────────────

    async def _run(
        self,
        *,
        label: str,
        request: dict[str, Any],
        on_event: EventSink | None,
        after_request: Callable[[DataChannel], Any] | None = None,
        record: bool = True,
        watch_uart: bool = True,
    ) -> FlashResult:
        if not self.available():
            agent = self._get_agent()
            if agent is None:
                return _fail("FASTBOOT_UNAVAILABLE", "no agent connected")
            return _fail(
                "FASTBOOT_UNAVAILABLE",
                f"agent {agent.agent_id} does not advertise the fastboot capability",
            )
        if self._lock.locked():
            # Refuse NOW rather than await the lock: see the module docstring.
            return _fail("FASTBOOT_BUSY", f"another job is running ({self._current})")

        async with self._lock:
            self._current = label
            started = time.monotonic()
            timeline = await _open_timeline(label, request, enabled=record, watch_uart=watch_uart)
            sink = _tee(on_event, timeline)
            try:
                result = await asyncio.wait_for(
                    self._drive(request, sink, after_request), JOB_TIMEOUT_S
                )
                return _finish(result, timeline)
            except TimeoutError:
                return _finish(
                    _fail(
                        "FLASH_FAILED",
                        f"job '{label}' exceeded {JOB_TIMEOUT_S:.0f}s and was abandoned",
                    ),
                    timeline,
                )
            except JobProtocolError as e:
                return _finish(
                    _fail("FLASH_FAILED", f"agent spoke an unexpected frame: {e}"), timeline
                )
            except Exception as e:  # channel open / send failures
                return _finish(
                    _fail("FLASH_FAILED", f"job '{label}' failed: {e or type(e).__name__}"),
                    timeline,
                )
            finally:
                self._current = ""
                if timeline is not None:
                    await timeline.aclose()
                _log.info("flash job %r finished in %.1fs", label, time.monotonic() - started)

    async def _drive(
        self,
        request: dict[str, Any],
        on_event: EventSink | None,
        after_request: Callable[[DataChannel], Any] | None,
    ) -> FlashResult:
        agent = self._get_agent()
        if agent is None:
            return _fail("FASTBOOT_UNAVAILABLE", "agent disconnected before the job started")
        started = time.monotonic()
        channel = await agent.open_data_channel(
            ctype=ChannelType.JOB,
            role=ChannelRole.JOB,
            params={},
            timeout=DIAL_BACK_TIMEOUT_S,
        )
        try:
            await channel.send(encode_control(request))
            if after_request is not None:
                await after_request(channel)
            return await _collect(channel, on_event, started)
        finally:
            with contextlib.suppress(Exception):
                await channel.aclose()


async def _stream_file(channel: DataChannel, path: Path) -> None:
    """Send the image as data frames, then a zero-length frame to mark the end.

    Reads happen in a worker thread — a synchronous read of a large image
    would stall this loop, and the serial forwarder that lets a caller watch
    the UART *while* this runs shares it (§决定 4)."""
    fh = await asyncio.to_thread(open, path, "rb")
    try:
        while True:
            chunk = await asyncio.to_thread(fh.read, CHUNK)
            if not chunk:
                break
            await channel.send(encode_data(chunk))
    finally:
        await asyncio.to_thread(fh.close)
    await channel.send(encode_data(b""))


async def _collect(channel: DataChannel, on_event: EventSink | None, started: float) -> FlashResult:
    """Consume agent events until the terminal frame.

    A closed channel with no `done` is its own failure: the caller must be
    able to tell "the flash failed" from "we never learned whether it did",
    because only the second one leaves a board in an unknown state.
    """
    reader = FrameReader(channel)
    events: list[FlashEvent] = []
    while True:
        msg = await reader.read_control()
        if msg is None:
            return FlashResult(
                ok=False,
                code="FLASH_FAILED",
                error="agent closed the job channel without reporting a result — "
                "the device state is unknown",
                duration_s=time.monotonic() - started,
                events=events,
            )
        ev = msg.get("ev")
        if ev == JobEvent.PROGRESS.value:
            item = FlashEvent(
                phase=str(msg.get("phase") or JobPhase.FLASH.value),
                done=int(msg.get("done") or 0),
                total=int(msg.get("total") or 0),
                text=str(msg.get("text") or ""),
            )
            events.append(item)
            if on_event is not None:
                on_event(item)
        elif ev == JobEvent.DONE.value:
            return FlashResult(
                ok=bool(msg.get("ok")),
                rc=_as_int(msg.get("rc"), -1),
                code=str(msg.get("code") or ""),
                error=str(msg.get("error") or ""),
                stdout=str(msg.get("stdout") or ""),
                stderr=str(msg.get("stderr") or ""),
                duration_s=time.monotonic() - started,
                events=events,
            )
        # ACCEPTED and anything unknown: ignore. A forward-compatible agent
        # may add events; refusing to proceed on an unrecognised one would
        # make every future agent addition a breaking change.


# ── timeline plumbing (ADR-056 §决定 4) ──────────────────────────────
#
# Recording lives HERE rather than in the CLI or the route because every
# caller funnels through this service. Put it at the edge and the answer to
# "what was the board saying while it was written" would depend on which
# client happened to start the job.


async def _open_timeline(
    label: str, request: dict[str, Any], *, enabled: bool, watch_uart: bool
) -> FlashTimeline | None:
    """Start recording. Any failure here downgrades to "no recording" —
    a bench that cannot write artifacts must still be able to flash.

    `watch_uart` is a SEPARATE knob from `enabled` because the two costs
    differ by orders of magnitude. Writing a timeline file is free; attaching
    the UART opens a channel to the agent, which opens the board's PHYSICAL
    serial port. Worth it while a partition is being written, absurd for a
    query — and doing it on a polled op is what took a live agent down
    (keepalive ping timeout, 2026-08-10)."""
    if not enabled:
        return None
    try:
        from alb.remote.flash_timeline import new_timeline

        timeline = new_timeline(label.replace(" ", "-"))
        # The whole request, digest included: six months later "which exact
        # image went onto this board" is the question the record has to
        # answer, and the digest is the only thing that answers it.
        timeline.header(label=label, detail=dict(request))
        if watch_uart:
            await timeline.attach_uart()
        return timeline
    except Exception as e:
        _log.warning("flash timeline unavailable (continuing without it): %s", e)
        return None


def _tee(on_event: EventSink | None, timeline: FlashTimeline | None) -> EventSink | None:
    """Fan one event stream out to the caller and the recorder — the half of
    ADR-054 that DOES apply here: many observers, same data."""
    if timeline is None:
        return on_event

    def sink(ev: FlashEvent) -> None:
        with contextlib.suppress(Exception):
            timeline.job_event(ev)
        if on_event is not None:
            on_event(ev)

    return sink


def _finish(result: FlashResult, timeline: FlashTimeline | None) -> FlashResult:
    """Record the verdict and tell the caller where to read the story."""
    if timeline is None:
        return result
    with contextlib.suppress(Exception):
        timeline.job_result(result)
        result.artifacts = str(timeline.dir)
    return result


_SERVICE: FlashService | None = None


def get_flash_service() -> FlashService:
    """The process-wide service. Created on first use against the live agent
    registry, mirroring how the forwarders resolve their agent."""
    global _SERVICE
    if _SERVICE is None:
        from alb.remote.registry import get_agent_registry

        _SERVICE = FlashService(lambda: get_agent_registry().current_agent())
    return _SERVICE


def reset_flash_service() -> None:
    """Test hook — drop the singleton so a fresh one binds to a fresh registry."""
    global _SERVICE
    _SERVICE = None
