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
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from alb.remote.bench_ops import OpSpec, check_params, legacy_ops, ops_of
from alb.remote.flash_plan import AgentView, Plan, PlanResult, PlanRow
from alb.remote.jobframe import FrameReader, JobProtocolError, encode_control, encode_data
from alb.remote.protocol import (
    CAP_FASTBOOT,
    ChannelRole,
    ChannelType,
    JobEvent,
    JobPhase,
    job_devices,
    job_erase,
    job_flash,
    job_getvar,
    job_oem,
    job_op,
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

# After a plan's `reboot-bootloader`: how long to wait for the board to come
# back to fastboot (callers may override per run), how often to look, and how
# long a board still listed after the reboot is given before it counts as
# "back". That last one exists because a fast reboot can leave and return
# between two polls — never seen gone is not proof it never left.
REBOOT_WAIT_S = 120.0
REBOOT_POLL_S = 1.0
REBOOT_SETTLE_S = 5.0

# `fastboot getvar` exits 0 even when the device answers FAIL: it prints
# `getvar:<name>   FAILED (remote: '...')` to stderr and carries on (AOSP
# fastboot.cpp, DisplayVarOrError — unlike oem / flash, whose failure ends
# the tool with a non-zero code). A plan's getvar row is the check a vendor
# puts before its flashes, so that line is what decides the row. It is the
# FAIL status fastboot prints; the variable's value is still not read.
# Matched loosely on purpose — any line with the word FAILED — because a
# missed FAIL lets a package meant for another board through, while an
# OKAY answer prints `<name>: <value>` and has no reason to contain it.
_GETVAR_FAILED_RE = re.compile(r"^.*\bFAILED\b.*$", re.MULTILINE)


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
    # Structured answer from ops whose result is not tool output (usb-list).
    data: dict[str, Any] = field(default_factory=dict)

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
            "data": self.data,
        }


EventSink = Callable[[FlashEvent], None]
_R = TypeVar("_R", FlashResult, PlanResult)


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


def _label(name: str, values: dict[str, Any]) -> str:
    """`erase misc`, `set-active b`, `log-tail agent 200` — the op and its
    values in order, for the busy message and the timeline directory."""
    shown = [str(v) for v in values.values() if v not in ("", None)]
    return " ".join([name, *shown])


def _unknown_op(agent: Any, name: str, table: dict[str, OpSpec]) -> FlashResult:
    """Why an op is not in the table, worded for the fix.

    An agent from before the table gets one inferred from its caps, so a
    missing op there usually means "update the agent", not "no such op"."""
    caps = list(getattr(agent, "caps", []) or [])
    if not getattr(agent, "ops_reported", False):
        if name in legacy_ops([CAP_FASTBOOT]) and CAP_FASTBOOT not in caps:
            return _fail(
                "FASTBOOT_UNAVAILABLE",
                f"agent {agent.agent_id} does not advertise the fastboot capability",
            )
        return _fail(
            "BENCH_AGENT_TOO_OLD",
            f"agent {agent.agent_id} predates the op table and cannot run {name!r} — "
            "update alb_agent.py on the agent host",
        )
    offered = ", ".join(sorted(table)) or "nothing"
    note = "" if CAP_FASTBOOT in caps else " (it has no fastboot executable, so no fastboot ops)"
    return _fail(
        "BENCH_OP_UNKNOWN", f"agent {agent.agent_id} has no op {name!r}{note}; it offers: {offered}"
    )


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

    def agent_view(self) -> AgentView | None:
        """What a flash plan checks itself against before its first row."""
        agent = self._get_agent()
        if agent is None:
            return None
        return AgentView(
            caps=list(getattr(agent, "caps", []) or []),
            partitions=self.partitions(),
            ops=ops_of(agent),
        )

    def ops(self) -> dict[str, OpSpec]:
        """The connected agent's op table (inferred from caps for an agent
        from before the table). Empty when no agent is connected."""
        return ops_of(self._get_agent())

    def ops_reported(self) -> bool:
        return bool(getattr(self._get_agent(), "ops_reported", False))

    def agent_summary(self) -> dict[str, str] | None:
        agent = self._get_agent()
        if agent is None:
            return None
        return {
            "id": str(agent.agent_id),
            "name": str(getattr(agent, "name", "") or ""),
            "build": str(getattr(agent, "build", "") or ""),
        }

    def status(self) -> dict[str, Any]:
        return {
            "available": self.available(),
            "busy": self.busy,
            "job": self._current,
            "partitions": self.partitions(),
        }

    # ── operations ───────────────────────────────────────────────────
    #
    # Every op goes through run_op (ADR-059 决定 3). The named methods below
    # are the pre-table vocabulary, kept because the CLI, the web and the
    # tests speak it; each is one line onto the generic path.

    async def run_op(
        self,
        name: str,
        params: dict[str, Any] | None = None,
        *,
        image: Path | None = None,
        label: str = "",
        on_event: EventSink | None = None,
    ) -> FlashResult:
        """Run one op from the connected agent's table.

        Everything the hub decides is read from the table row: whether the
        values have the right shape, whether an image goes first, whether the
        single job lock is needed, what gets recorded. The agent re-checks the
        values and builds the argv; nothing here names a path or a command.
        """
        agent = self._get_agent()
        if agent is None:
            return _fail("FASTBOOT_UNAVAILABLE", "no agent connected")
        table = ops_of(agent)
        spec = table.get(name)
        if spec is None:
            return _unknown_op(agent, name, table)
        allowlist = list(getattr(agent, "flash_partitions", []) or [])
        values, code, problem = check_params(spec, dict(params or {}), allowlist)
        if problem:
            return _fail(code, problem)
        if spec.image and image is None:
            return _fail("BENCH_PARAM_REJECTED", f"{name} needs an image")
        if not spec.image and image is not None:
            return _fail("BENCH_PARAM_REJECTED", f"{name} does not take an image")

        size: int | None = None
        sha = ""
        after_request: Callable[[DataChannel], Any] | None = None
        if image is not None:
            try:
                size, sha = await digest_file(image)
            except OSError as e:
                return _fail("FLASH_IMAGE_CORRUPT", f"cannot read {image}: {e}")
            if size == 0:
                return _fail("FLASH_IMAGE_CORRUPT", f"{image} is empty")
            if spec.max_image_bytes and size > spec.max_image_bytes:
                code = "FLASH_AGENT_NO_SPACE"
                if not getattr(agent, "ops_reported", False):
                    code = "BENCH_AGENT_TOO_OLD"
                return _fail(
                    code,
                    f"the agent refuses images over {spec.max_image_bytes} bytes "
                    f"({image} is {size}) — update alb_agent.py on the agent host",
                )
            src = image

            async def send_image(channel: DataChannel) -> None:
                await _stream_file(channel, src)

            after_request = send_image

        request = job_op(name, values, size=size, sha256=sha) if image else job_op(name, values)
        result = await self._run(
            label=label or _label(name, values),
            request=request,
            on_event=on_event,
            after_request=after_request,
            record=spec.record != "none",
            watch_uart=spec.record == "uart",
            exclusive=spec.exclusive,
            need_fastboot=spec.group == "fastboot" or spec.needs == "fastboot-device",
        )
        if name == "usb-list" and result.ok:
            from alb.remote.usb_modes import annotate

            result.data = annotate(result.data)
        return result

    async def flash(
        self, partition: str, image: Path, *, on_event: EventSink | None = None
    ) -> FlashResult:
        """Stream `image` to the agent and have it flash `partition`."""
        return await self.run_op(
            "flash",
            {"partition": partition},
            image=image,
            label=f"flash {partition}",
            on_event=on_event,
        )

    async def reboot(self, target: str = "", *, on_event: EventSink | None = None) -> FlashResult:
        """`fastboot reboot [target]`. With no target this is the way back to
        the system — the direct remedy for a board alb itself pushed into
        fastboot and could not previously get out of. Records events but no
        UART: the command returns in ~0.1 s, long before the board has said
        anything worth correlating; the boot log belongs to a capture."""
        return await self.run_op(
            "reboot", {"target": target}, label=f"reboot {target or 'normal'}", on_event=on_event
        )

    async def devices(self, *, on_event: EventSink | None = None) -> FlashResult:
        """`fastboot devices` on the agent host — a board in fastboot has
        vanished from adb, so this is the only way to see it.

        Records nothing: a ~60 ms query whose whole answer is its return
        value, and the op most likely to be POLLED. Recording used to mean
        opening the board's PHYSICAL serial port on every single call — a poll
        loop of these wedged a live agent (keepalive ping timeout, 2026-08-10).
        """
        return await self.run_op("devices", {}, label="devices", on_event=on_event)

    async def getvar(self, name: str = "", *, on_event: EventSink | None = None) -> FlashResult:
        """`fastboot getvar <name>` (empty = `getvar all`), answer passed
        through untouched.

        Transport, not interpretation. The verb is protocol level and
        survives a platform change; the meaning of the values does not —
        this bench answers `partition-size:cfg` with `0` on flashes that
        succeed. Turning that into "the partition is missing" would ship the
        misreading that cost a day on 2026-08-10, so nothing here parses the
        output. Recorded nowhere, no UART — same tier as `devices`.
        """
        return await self.run_op("getvar", {"name": name}, label="getvar", on_event=on_event)

    async def oem(self, command: str, *, on_event: EventSink | None = None) -> FlashResult:
        """`fastboot oem <command>`; the agent refuses anything off its
        allowlist. Recorded like `reboot`, because it changes the device and a
        later "why is this board locked" deserves a timeline entry."""
        return await self.run_op(
            "oem", {"command": command}, label=f"oem {command}", on_event=on_event
        )

    # ── plumbing ─────────────────────────────────────────────────────

    async def run_plan(
        self,
        plan: Plan,
        *,
        reboot_wait_s: float = REBOOT_WAIT_S,
        on_event: EventSink | None = None,
    ) -> PlanResult:
        """Run every row of a checked plan, in order, under ONE hold of the lock.

        One hold, not one per row: between rows the lock would otherwise be
        free, and a web click or a second CLI could write a partition in the
        middle of a package — a board no manifest describes. The first
        failing row stops the run; later rows are reported as not run.

        One timeline for the whole plan, UART attached throughout: what the
        board printed across a `reboot-bootloader` is exactly the evidence a
        failed package flash needs, and it falls between rows.
        """
        if not plan.ok:
            return PlanResult(
                ok=False,
                manifest=plan.manifest,
                rows=plan.rows,
                code="FLASH_PLAN_REJECTED",
                error="; ".join(plan.problems),
            )
        refused = self._refusal()
        if refused is not None:
            return PlanResult(
                ok=False,
                manifest=plan.manifest,
                rows=plan.rows,
                code=refused.code,
                error=refused.error,
            )

        async with self._lock:
            started = time.monotonic()
            label = f"plan {Path(plan.manifest).stem}"
            self._current = label
            detail = {"op": "plan", **plan.as_dict()}
            timeline = await _open_timeline(label, detail, enabled=True, watch_uart=True)
            sink = _tee(on_event, timeline)
            result = PlanResult(ok=True, manifest=plan.manifest, rows=plan.rows)
            todo = sum(1 for r in plan.rows if r.runs)
            n = 0
            try:
                for row in plan.rows:
                    if not row.runs:
                        row.status = "skipped"
                        _say(
                            sink, "skip", n, todo, f"line {row.line}: {row.describe()} ({row.skip})"
                        )
                        continue
                    n += 1
                    self._current = f"{label}: line {row.line} ({n}/{todo}) {row.describe()}"
                    _say(sink, "step", n, todo, f"line {row.line}: {row.describe()}")
                    t0 = time.monotonic()
                    outcome = await self._run_row(row, sink, reboot_wait_s)
                    row.duration_s = time.monotonic() - t0
                    if outcome.ok:
                        row.status = "ok"
                        continue
                    row.status, row.code, row.error = "failed", outcome.code, outcome.error
                    result.ok = False
                    result.code = outcome.code or "FLASH_FAILED"
                    result.error = outcome.error
                    result.failed_line = row.line
                    result.stdout, result.stderr = outcome.stdout, outcome.stderr
                    break
                for row in plan.rows:
                    if row.status == "pending":
                        row.status = "not-run" if row.runs else "skipped"
                result.duration_s = time.monotonic() - started
                return _finish(result, timeline)
            finally:
                self._current = ""
                if timeline is not None:
                    await timeline.aclose()
                _log.info("flash plan %r finished in %.1fs", label, time.monotonic() - started)

    async def _run_row(
        self, row: PlanRow, sink: EventSink | None, reboot_wait_s: float
    ) -> FlashResult:
        label = row.describe()
        if row.action == "flash" and row.path is not None:
            image = row.path

            async def send_image(channel: DataChannel) -> None:
                await _stream_file(channel, image)

            # The digest is the one computed when the plan was built — the
            # one the caller confirmed. A file changed since then fails the
            # agent's check, and nothing is written.
            request = job_flash(partition=row.partition, size=row.size, sha256=row.sha256)
            return await self._step(request, sink, send_image, label=label)
        if row.action == "erase":
            return await self._step(job_erase(partition=row.partition), sink, None, label=label)
        if row.action == "getvar":
            outcome = await self._step(job_getvar(name=row.key), sink, None, label=label)
            failed = _GETVAR_FAILED_RE.search(f"{outcome.stderr}\n{outcome.stdout}")
            if outcome.ok and failed is not None:
                # Either the device's FAIL (`remote: '...'`) or a local
                # error on the way; the line itself says which.
                outcome.ok = False
                outcome.code = "FLASH_PLAN_CHECK_FAILED"
                outcome.error = (
                    f"line {row.line}: {label} reported {failed.group(0).strip()!r} — "
                    "the rows after it were not run"
                )
            return outcome
        if row.action == "oem":
            return await self._step(job_oem(command=row.command), sink, None, label=label)
        if row.action == "reboot":
            return await self._step(job_reboot(target=""), sink, None, label=label)
        if row.action == "reboot-bootloader":
            outcome = await self._step(job_reboot(target="bootloader"), sink, None, label=label)
            if not outcome.ok:
                return outcome
            return await self._await_fastboot(reboot_wait_s, sink)
        # build_plan never lets such a row run; refuse rather than guess.
        return _fail("FLASH_PLAN_REJECTED", f"line {row.line}: {label} cannot run")

    async def _await_fastboot(self, timeout_s: float, sink: EventSink | None) -> FlashResult:
        """Wait for the board to be listed by `fastboot devices` again.

        Back means: seen gone and then listed, or still listed once
        REBOOT_SETTLE_S has passed (a reboot quicker than one poll interval
        is never seen gone). Polls go straight to `_drive` — this runs inside
        the plan's hold of the lock, and `devices()` would report busy."""
        started = time.monotonic()
        _say(
            sink,
            "wait",
            0,
            0,
            f"waiting up to {timeout_s:.0f}s for the device to return to fastboot",
        )
        seen_gone = False
        while True:
            await asyncio.sleep(REBOOT_POLL_S)
            elapsed = time.monotonic() - started
            if not await self._fastboot_listed():
                seen_gone = True
            elif seen_gone or elapsed >= REBOOT_SETTLE_S:
                how = "" if seen_gone else " (never seen leaving — it rebooted between polls)"
                _say(sink, "wait", 0, 0, f"device back in fastboot after {elapsed:.1f}s{how}")
                return FlashResult(ok=True, rc=0, duration_s=elapsed)
            if time.monotonic() - started >= timeout_s:
                return _fail(
                    "FASTBOOT_NO_DEVICE",
                    f"the device did not come back to fastboot within {timeout_s:.0f}s "
                    "after reboot-bootloader",
                )

    async def _fastboot_listed(self) -> bool:
        try:
            outcome = await asyncio.wait_for(
                self._drive(job_devices(), None, None), DIAL_BACK_TIMEOUT_S + 10.0
            )
        except Exception:
            # The agent's USB view can be briefly confused while the board
            # re-enumerates; one failed poll is "not back yet", not the end.
            return False
        return outcome.ok

    def _refusal(self, *, need_fastboot: bool = True, exclusive: bool = True) -> FlashResult | None:
        """Why a job cannot start right now, or None.

        A host-only op (the USB listing, a Windows log) needs an agent but
        not fastboot, and is not exclusive: it must answer during a
        30-minute flash — that is often exactly when it is wanted."""
        agent = self._get_agent()
        if agent is None:
            return _fail("FASTBOOT_UNAVAILABLE", "no agent connected")
        if need_fastboot and not self.available():
            return _fail(
                "FASTBOOT_UNAVAILABLE",
                f"agent {agent.agent_id} does not advertise the fastboot capability",
            )
        if exclusive and self._lock.locked():
            # Refuse NOW rather than await the lock: see the module docstring.
            return _fail("FASTBOOT_BUSY", f"another job is running ({self._current})")
        return None

    async def _run(
        self,
        *,
        label: str,
        request: dict[str, Any],
        on_event: EventSink | None,
        after_request: Callable[[DataChannel], Any] | None = None,
        record: bool = True,
        watch_uart: bool = True,
        exclusive: bool = True,
        need_fastboot: bool = True,
    ) -> FlashResult:
        refused = self._refusal(need_fastboot=need_fastboot, exclusive=exclusive)
        if refused is not None:
            return refused
        if not exclusive:
            return await self._run_unlocked(
                label, request, on_event, after_request, record, watch_uart
            )

        async with self._lock:
            self._current = label
            started = time.monotonic()
            timeline = await _open_timeline(label, request, enabled=record, watch_uart=watch_uart)
            sink = _tee(on_event, timeline)
            try:
                result = await self._step(request, sink, after_request, label=label)
                return _finish(result, timeline)
            finally:
                self._current = ""
                if timeline is not None:
                    await timeline.aclose()
                _log.info("flash job %r finished in %.1fs", label, time.monotonic() - started)

    async def _run_unlocked(
        self,
        label: str,
        request: dict[str, Any],
        on_event: EventSink | None,
        after_request: Callable[[DataChannel], Any] | None,
        record: bool,
        watch_uart: bool,
    ) -> FlashResult:
        """A non-exclusive op: same channel, same recording rules, no lock —
        and therefore no `current` label, which names the one exclusive job."""
        timeline = await _open_timeline(label, request, enabled=record, watch_uart=watch_uart)
        sink = _tee(on_event, timeline)
        try:
            return _finish(await self._step(request, sink, after_request, label=label), timeline)
        finally:
            if timeline is not None:
                await timeline.aclose()

    async def _step(
        self,
        request: dict[str, Any],
        sink: EventSink | None,
        after_request: Callable[[DataChannel], Any] | None,
        *,
        label: str,
    ) -> FlashResult:
        """One job on one channel, every failure turned into a FlashResult.
        The caller must already hold the lock."""
        try:
            return await asyncio.wait_for(self._drive(request, sink, after_request), JOB_TIMEOUT_S)
        except TimeoutError:
            return _fail(
                "FLASH_FAILED", f"job '{label}' exceeded {JOB_TIMEOUT_S:.0f}s and was abandoned"
            )
        except JobProtocolError as e:
            return _fail("FLASH_FAILED", f"agent spoke an unexpected frame: {e}")
        except Exception as e:  # channel open / send failures
            return _fail("FLASH_FAILED", f"job '{label}' failed: {e or type(e).__name__}")

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
            # The op name rides the open request so the agent can refuse a
            # busy / unsupported job BEFORE dialling back (ADR-059 §5 of the
            # contract); the request frame that follows is still the authority.
            params={"op": str(request.get("op") or "")},
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
                # Lets a caller that can learn the outcome another way (the
                # agent update: from the next hello) tell this apart.
                data={"result_lost": True},
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
                data=msg["data"] if isinstance(msg.get("data"), dict) else {},
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


def _say(sink: EventSink | None, phase: str, done: int, total: int, text: str) -> None:
    """A plan's own events (which row, skipped, waiting) ride the same
    stream as the agent's, so the CLI, the web and the timeline all see
    them in order with the job progress they frame."""
    if sink is not None:
        sink(FlashEvent(phase=phase, done=done, total=total, text=text))


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


def _finish(result: _R, timeline: FlashTimeline | None) -> _R:
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
