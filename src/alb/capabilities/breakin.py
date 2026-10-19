"""breakin capability — stop a board in its bootloader over UART.

Many bootloaders print a countdown such as ``Hit any key to stop autoboot:  3``
and drop to a console prompt if a key arrives in time. The window is one to
three seconds after reset. On a remote bench nobody can press that key at the
right moment, and typing after the fact is useless: the board is already in
the kernel. ``break_in`` watches the UART for the countdown and answers it
within milliseconds.

Order of operations is the whole point:

1. open the serial link (the reader) FIRST, so the first byte the board prints
   after reset is already being watched;
2. only then trigger the reboot (``adb reboot``, ``reboot`` over the same
   UART, or the operator by hand);
3. match ``expect`` across chunk boundaries, send ``send`` repeatedly until
   ``prompt`` shows up or the prompt window closes.

The link is the transport's own ``open_session`` — one reader/writer pair on
the same stream every other alb reader uses (the hub forwarder fans the UART
out to all of them, ADR-054), so a running ``alb serial capture`` keeps
working while this command types.

Some bootloaders never offer a key-press stop (they decide from a hardware
key combination instead). The command then ends with "pattern never seen"
and the last lines received, so the reader can see where boot went instead.
It never claims success without having seen a prompt, and never once the
board has started its kernel: the keys typed from then on land in the Linux
console, whose own ``$`` / ``#`` prompt answers each one.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, Literal, Protocol, cast

from alb.capabilities.power import reboot as power_reboot
from alb.infra.result import ErrorCategory, ErrorInfo, Result, fail, ok
from alb.infra.workspace import iso_timestamp, resolve_capture_path
from alb.transport.base import Transport
from alb.transport.serial import _classify_connect_error
from alb.transport.serial_state import DEFAULT_PATTERNS

# ─── Defaults (shared with the CLI so help text and behaviour agree) ────
DEFAULT_EXPECT = r"Hit any key to stop autoboot|Press any key|autoboot in"
DEFAULT_SEND = b" "
# A console prompt at the very end of what was received: ``=> ``,
# ``U-Boot> ``, ``CFE> ``, ``RedBoot> ``. Anchored to the end of the buffer,
# not to a line end, because the prompt is by definition the last thing the
# board printed; a ``=>`` inside an earlier line must not count.
# A false "stopped at prompt" is the one failure direction this command must
# never take, so two shapes are deliberately NOT covered — pass ``--prompt``:
# - a bare ``> `` (no name): too easy to find at the end of ordinary output;
# - a bare ``# `` / ``$ ``: that is what a Linux / Android console prints.
#   A board whose bootloader could not be stopped boots on into the kernel,
#   and every key typed there is answered with a fresh ``$ `` — which the
#   quiet check below then confirms as soon as the kernel log pauses.
DEFAULT_PROMPT = r"(?:=>|\w+\s*>+)\s*$"
# Text that only appears once the bootloader has handed over to the kernel
# (U-Boot's ``Starting kernel ...``, the kernel's own first lines). Seen after
# the countdown, it ends the attempt at once: the window is over, and any
# prompt from here on is the kernel's, not the bootloader's. Before the
# countdown they end nothing: the board may have been mid-boot when the reader
# came up, and the reset that matters (by hand, when told to) is still to
# come. The same markers the serial state machine uses for its kernel_boot
# state, so both agree on what a kernel start looks like.
_KERNEL_HANDOFF = re.compile(DEFAULT_PATTERNS["kernel_boot"])
# A prompt-shaped tail is only a candidate. It counts once the board has then
# stayed silent this long: a UART chunk can end anywhere, so text that merely
# ends in a prompt character mid-boot is followed by more bytes within
# milliseconds, while a real prompt is followed by nothing. Keys are not typed
# while a candidate is pending — at a real prompt each one would echo a new
# prompt and the silence would never come.
DEFAULT_PROMPT_QUIET_S = 0.2
DEFAULT_TIMEOUT_S = 120.0
DEFAULT_REPEAT_MS = 50
DEFAULT_PROMPT_WAIT_S = 3.0

REBOOT_MODES = ("adb", "serial")

# Bytes kept for the ``expect`` search. A countdown line is well under 100
# bytes; 4 KiB covers it even when the bootloader banner arrives in the same
# chunk, while bounding the regex cost per chunk.
_MATCH_WINDOW = 4096
# Bytes kept to render "the last ~40 lines received". Boot-log lines are
# short, so this holds comfortably more than 40 of them.
_TAIL_BYTES = 16 * 1024
_TAIL_LINES = 40
_READ_CHUNK = 4096
# Longest single read while waiting for the pattern: short enough that the
# overall timeout is honoured to within half a second. Data arriving ends
# the read at once, so this adds no latency to the match itself.
_IDLE_SLICE_S = 0.5
_RECONNECT_BACKOFF_S = 0.5

Outcome = Literal[
    "stopped_at_prompt",
    "matched_no_prompt",
    "booted_past",
    "pattern_never_seen",
    "link_error",
    "reboot_failed",
]

_LINK_ERRORS = (ConnectionResetError, BrokenPipeError, OSError)


class _Link(Protocol):
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter


class _SessionTransport(Protocol):
    """The two methods of SerialTransport this capability relies on."""

    async def open_session(self) -> Any: ...

    async def close_session(self, link: Any) -> None: ...


@dataclass(frozen=True)
class BreakInResult:
    """What happened, in the order the reader needs it: outcome first, then
    what was matched, where the board is now, and the evidence."""

    outcome: Outcome
    matched: str | None
    prompt_line: str | None
    sends: int
    tail: str
    duration_ms: int
    reboot: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "matched": self.matched,
            "prompt_line": self.prompt_line,
            "sends": self.sends,
            "tail": self.tail,
            "duration_ms": self.duration_ms,
            "reboot": self.reboot,
        }


_ESCAPES = {
    "r": b"\r",
    "n": b"\n",
    "t": b"\t",
    "e": b"\x1b",
    "0": b"\x00",
    "\\": b"\\",
}


def parse_send_keys(text: str) -> bytes:
    """Turn the ``--send`` argument into bytes.

    Shell quoting makes it awkward to pass a raw carriage return or Ctrl-C,
    so the usual C-style escapes are accepted: ``\\r`` ``\\n`` ``\\t``
    ``\\e`` ``\\0`` ``\\\\`` and ``\\xHH``. Everything else is UTF-8 as
    typed. A lone or unknown backslash sequence is an error rather than a
    silent literal: sending ``\\q`` to a bootloader by accident would be
    hard to notice.
    """
    out = bytearray()
    i = 0
    while i < len(text):
        ch = text[i]
        if ch != "\\":
            out += ch.encode("utf-8")
            i += 1
            continue
        nxt = text[i + 1 : i + 2]
        if nxt in _ESCAPES:
            out += _ESCAPES[nxt]
            i += 2
            continue
        if nxt == "x":
            hex_ = text[i + 2 : i + 4]
            if len(hex_) == 2 and all(c in "0123456789abcdefABCDEF" for c in hex_):
                out.append(int(hex_, 16))
                i += 4
                continue
            raise ValueError(f"bad \\x escape at offset {i}: expected two hex digits")
        raise ValueError(f"unknown escape {text[i : i + 2]!r} at offset {i}")
    return bytes(out)


class _Watch:
    """Rolling view of the UART stream plus the match/send bookkeeping.

    Kept separate from the I/O loop so the matching rules can be read (and
    tested) without the link plumbing around them.
    """

    def __init__(
        self,
        expect: re.Pattern[bytes],
        prompt: re.Pattern[bytes],
        keys: bytes,
        *,
        repeat_s: float,
    ) -> None:
        self._expect = expect
        self._prompt = prompt
        self._keys = keys
        self._repeat_s = repeat_s
        self._tail = bytearray()
        self._evicted = False
        # Bytes received after the expect match; the prompt is looked for
        # only here so a ``#`` from the kernel log cannot pass for a prompt.
        self._since_match = bytearray()
        self.matched: bytes | None = None
        self.matched_at: float | None = None
        self.prompt_line: str | None = None
        # The kernel-start marker seen after the match, if any.
        self.handoff: bytes | None = None
        self.sends = 0
        self.last_send_at = 0.0

    # ── stream side ──────────────────────────────────────────────
    def feed(self, chunk: bytes) -> None:
        self._tail += chunk
        if len(self._tail) > _TAIL_BYTES:
            del self._tail[: len(self._tail) - _TAIL_BYTES]
            self._evicted = True
        if self.matched is not None:
            self._since_match += chunk
            # Scanned before the trim: a marker split between the previous
            # chunk and this one is still whole here.
            self._scan_handoff()
            if len(self._since_match) > _MATCH_WINDOW:
                del self._since_match[: len(self._since_match) - _MATCH_WINDOW]

    def _scan_handoff(self) -> None:
        if self.handoff is not None:
            return
        m = _KERNEL_HANDOFF.search(self._since_match)
        if m is not None:
            self.handoff = bytes(m.group(0))

    def try_match(self, now: float) -> bool:
        """First time the countdown shows up in the rolling window."""
        if self.matched is not None:
            return False
        window = self._tail[-_MATCH_WINDOW:]
        m = self._expect.search(window)
        if m is None:
            return False
        self.matched = bytes(m.group(0))
        self.matched_at = now
        self._since_match = bytearray(window[m.end() :])
        # A countdown of 0 and the kernel start can arrive in one chunk.
        self._scan_handoff()
        return True

    def prompt_seen(self) -> bool:
        m = self._prompt.search(self._since_match)
        if m is None:
            return False
        line_start = self._since_match.rfind(b"\n", 0, m.start()) + 1
        self.prompt_line = (
            bytes(self._since_match[line_start:]).decode("utf-8", errors="replace").rstrip()
        )
        return True

    # ── send side ────────────────────────────────────────────────
    def send_due(self, now: float) -> bool:
        return self.sends == 0 or now - self.last_send_at >= self._repeat_s

    def next_send_at(self) -> float:
        return self.last_send_at + self._repeat_s

    async def send_keys(self, link: _Link, now: float) -> None:
        link.writer.write(self._keys)
        await link.writer.drain()
        self.sends += 1
        self.last_send_at = now

    # ── evidence ─────────────────────────────────────────────────
    def tail_text(self) -> str:
        text = bytes(self._tail).decode("utf-8", errors="replace").replace("\r", "")
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()  # a trailing newline is not an extra (empty) line
        if self._evicted and len(lines) > 1:
            lines = lines[1:]  # the first line was cut when old bytes were dropped
        return "\n".join(lines[-_TAIL_LINES:])

    def matched_text(self) -> str | None:
        if self.matched is None:
            return None
        return self.matched.decode("utf-8", errors="replace")

    def handoff_text(self) -> str | None:
        if self.handoff is None:
            return None
        return self.handoff.decode("utf-8", errors="replace")


def _compile(pattern: str, what: str) -> re.Pattern[bytes] | Result[Any]:
    try:
        return re.compile(pattern.encode("utf-8"))
    except re.error as e:
        return fail(
            code="INVALID_FILTER",
            message=f"invalid {what} regex: {e}",
            suggestion="Check the pattern syntax; escape special characters",
            category="input",
        )


async def break_in(
    transport: Transport,
    *,
    expect: str = DEFAULT_EXPECT,
    send: bytes = DEFAULT_SEND,
    reboot: str | None = None,
    reboot_transport: Transport | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    prompt: str | None = None,
    repeat_ms: int = DEFAULT_REPEAT_MS,
    prompt_wait_s: float = DEFAULT_PROMPT_WAIT_S,
    prompt_quiet_s: float = DEFAULT_PROMPT_QUIET_S,
    device: str | None = None,
    output: Path | str | None = None,
    reconnect_backoff_s: float = _RECONNECT_BACKOFF_S,
    on_ready: Callable[[], None] | None = None,
) -> Result[BreakInResult]:
    """Catch the bootloader countdown on the UART and stop the board there.

    Args:
        transport: the serial transport (must offer ``open_session``).
        expect: regex (str, applied to the raw bytes) announcing the window.
        send: bytes to type, repeated every ``repeat_ms`` once ``expect``
            matched, until ``prompt`` appears or ``prompt_wait_s`` elapses.
        reboot: ``"adb"`` → ``adb reboot`` through ``reboot_transport``;
            ``"serial"`` → ``reboot\\n`` over the same UART link; ``None`` →
            the operator resets the board by hand after this call starts.
        reboot_transport: the adb transport used when ``reboot == "adb"``.
        timeout_s: how long to wait for ``expect`` in total.
        prompt: regex for the console prompt; ``None`` → ``DEFAULT_PROMPT``.
        prompt_wait_s: how long after the match a prompt may take.
        prompt_quiet_s: how long the board must stay silent after a
            prompt-shaped tail before it counts as a prompt.
        device / output: where the full UART log lands, same rules as
            ``capture_uart`` (``workspace/.../logs/<ts>-breakin-uart.log``).
        on_ready: called once the reader is open, before any reboot is
            triggered — the moment it becomes safe for a human to press the
            reset button.

    Returns ``ok`` only for ``stopped_at_prompt``. The other outcomes are
    failures with the tail in ``error.details["tail"]`` and the log in
    ``artifacts`` — a caller that only reads ``ok`` can never mistake a
    board that booted past the window for one sitting at a prompt.
    """
    if transport.name != "serial" or not hasattr(transport, "open_session"):
        return fail(
            code="TRANSPORT_NOT_SUPPORTED",
            message=f"break_in requires the serial transport, got {transport.name}",
            suggestion="Run: alb setup serial (method G)",
            category="transport",
        )
    if reboot is not None and reboot not in REBOOT_MODES:
        return fail(
            code="BREAKIN_BAD_ARGUMENT",
            message=f"unknown reboot mode {reboot!r}",
            suggestion=f"Use one of {REBOOT_MODES}, or omit it and reset the board by hand",
            category="input",
        )
    if reboot == "adb" and reboot_transport is None:
        return fail(
            code="TRANSPORT_NOT_CONFIGURED",
            message="reboot='adb' needs an adb transport to send the reboot through",
            suggestion="Pass reboot_transport (the CLI builds it from the active profile)",
            category="transport",
        )
    if not send:
        return fail(
            code="BREAKIN_BAD_ARGUMENT",
            message="send must be at least one byte",
            suggestion="Typical values: a space, '\\r', or '\\x03' (Ctrl-C)",
            category="input",
        )
    if timeout_s <= 0 or prompt_wait_s <= 0 or prompt_quiet_s <= 0 or repeat_ms < 1:
        return fail(
            code="INVALID_DURATION",
            message=(
                f"timeout_s={timeout_s}, prompt_wait_s={prompt_wait_s}, "
                f"prompt_quiet_s={prompt_quiet_s}, repeat_ms={repeat_ms}: all must be positive"
            ),
            suggestion="Use e.g. timeout_s=120, prompt_wait_s=3, repeat_ms=50",
            category="input",
        )
    expect_re = _compile(expect, "expect")
    if isinstance(expect_re, Result):
        return expect_re
    prompt_re = _compile(prompt if prompt is not None else DEFAULT_PROMPT, "prompt")
    if isinstance(prompt_re, Result):
        return prompt_re

    session = cast(_SessionTransport, transport)
    artifact = resolve_capture_path(output, f"{iso_timestamp()}-breakin-uart.log", device=device)
    artifact.parent.mkdir(parents=True, exist_ok=True)
    watch = _Watch(expect_re, prompt_re, send, repeat_s=repeat_ms / 1000.0)
    start = perf_counter()
    deadline = start + timeout_s

    def _elapsed_ms() -> int:
        return int((perf_counter() - start) * 1000)

    def _failed(
        outcome: Outcome,
        code: str,
        message: str,
        suggestion: str,
        category: ErrorCategory,
    ) -> Result[BreakInResult]:
        # Not fail(): the log is evidence even when the attempt failed, and
        # fail() has no artifacts slot.
        return Result(
            ok=False,
            error=ErrorInfo(
                code=code,
                message=message,
                suggestion=suggestion,
                category=category,
                details={
                    "outcome": outcome,
                    "matched": watch.matched_text(),
                    "handoff": watch.handoff_text(),
                    "sends": watch.sends,
                    "tail": watch.tail_text(),
                    "reboot": reboot,
                    "artifact": str(artifact),
                },
            ),
            artifacts=[artifact],
            timing_ms=_elapsed_ms(),
        )

    def _link_failed(exc: Exception, when: str) -> Result[BreakInResult]:
        code = _classify_connect_error(exc)
        if code == "ADB_COMMAND_FAILED":  # the classifier's catch-all; wrong name here
            code = "SERIAL_LINK_LOST"
        return _failed(
            "link_error",
            code,
            f"serial link failed {when}: {type(exc).__name__}: {exc}",
            "Check the bridge / agent (alb serial health); a link that could not be "
            "opened says nothing about the board",
            "transport",
        )

    # ── 1. the reader goes up before anything can make the board print ──
    try:
        link: Any = await session.open_session()
    except Exception as e:  # every open failure is reported, never raised
        return _link_failed(e, "before the reboot was triggered (the board was not touched)")
    if on_ready is not None:
        on_ready()

    # From here on the link is released on every way out — a returned
    # failure, an exception, or the caller being cancelled. `_reopen` swaps
    # it and closes the old one itself, so `link` always names the live one.
    try:
        # ── 2. only now is it safe to trigger the reboot ──────────────
        if reboot == "adb":
            assert reboot_transport is not None  # validated above
            rr = await power_reboot(reboot_transport, "normal", wait_boot=False)
            if not rr.ok:
                err = rr.error
                return _failed(
                    "reboot_failed",
                    err.code if err else "ADB_COMMAND_FAILED",
                    "adb reboot did not go through, so the board was not restarted: "
                    + (err.message if err else "reboot failed"),
                    (err.suggestion if err and err.suggestion else "")
                    or "Reset the board by hand and run again without --reboot",
                    err.category if err else "transport",
                )
        elif reboot == "serial":
            try:
                link.writer.write(b"reboot\n")
                await link.writer.drain()
            except _LINK_ERRORS as e:
                return _link_failed(e, "while sending 'reboot' over the UART")

        # ── 3. watch, type, and judge ─────────────────────────────────
        outcome: Outcome
        # When the received bytes last ended in a prompt-shaped tail; None
        # while they do not. See DEFAULT_PROMPT_QUIET_S.
        candidate_at: float | None = None
        with artifact.open("wb") as f:
            while True:
                now = perf_counter()
                if watch.matched_at is None:
                    if now >= deadline:
                        outcome = "pattern_never_seen"
                        break
                    slice_s = min(_IDLE_SLICE_S, deadline - now)
                elif candidate_at is not None:
                    # Confirming a candidate may run past prompt_wait_s: it
                    # appeared in time, and cutting the silence short would
                    # report a prompt that was there as missing.
                    confirm_at = candidate_at + prompt_quiet_s
                    if now >= confirm_at:
                        outcome = "stopped_at_prompt"
                        break
                    slice_s = confirm_at - now
                else:
                    prompt_deadline = watch.matched_at + prompt_wait_s
                    if now >= prompt_deadline:
                        outcome = "matched_no_prompt"
                        break
                    if watch.send_due(now):
                        try:
                            await watch.send_keys(link, now)
                        except _LINK_ERRORS as e:
                            link = await _reopen(session, link, deadline, reconnect_backoff_s)
                            if link is None:
                                return _link_failed(e, "while typing at the countdown")
                            continue
                    slice_s = max(0.001, min(watch.next_send_at(), prompt_deadline) - now)

                try:
                    chunk = await asyncio.wait_for(link.reader.read(_READ_CHUNK), timeout=slice_s)
                except TimeoutError:
                    continue
                except _LINK_ERRORS:
                    chunk = b""

                if not chunk:
                    # EOF: TCP bridges drop idle clients, and some agents
                    # reopen the port around a reset. Reopen until the
                    # deadline, same as capture does — the countdown may
                    # still be coming. A pending candidate is dropped: bytes
                    # sent while the link was down were never seen, so the
                    # silence it needs cannot be vouched for.
                    candidate_at = None
                    link = await _reopen(session, link, deadline, reconnect_backoff_s)
                    if link is None:
                        if perf_counter() < deadline:
                            return _link_failed(
                                ConnectionError("the endpoint closed and would not reopen"),
                                "mid-attempt",
                            )
                        outcome = "matched_no_prompt" if watch.matched_at else "pattern_never_seen"
                        break
                    continue

                f.write(chunk)
                f.flush()  # the log is read while the attempt runs, and after a SIGTERM
                now = perf_counter()
                watch.feed(chunk)
                if watch.matched_at is None:
                    if watch.try_match(now):
                        if watch.handoff is not None:
                            outcome = "booted_past"
                            break
                        if watch.prompt_seen():
                            # Someone else (or the board itself) may already
                            # have stopped it; the silence decides.
                            candidate_at = now
                        else:
                            try:
                                await watch.send_keys(link, now)
                            except _LINK_ERRORS as e:
                                return _link_failed(e, "while typing at the countdown")
                elif watch.handoff is not None:
                    # Checked before the prompt, and it voids a pending
                    # candidate: past this point every prompt is the kernel's.
                    outcome = "booted_past"
                    break
                elif watch.prompt_seen():
                    candidate_at = now
                else:
                    candidate_at = None
    finally:
        if link is not None:
            with contextlib.suppress(Exception):
                await session.close_session(link)
        link = None

    if outcome == "stopped_at_prompt":
        return ok(
            data=BreakInResult(
                outcome=outcome,
                matched=watch.matched_text(),
                prompt_line=watch.prompt_line,
                sends=watch.sends,
                tail=watch.tail_text(),
                duration_ms=_elapsed_ms(),
                reboot=reboot,
            ),
            artifacts=[artifact],
            timing_ms=_elapsed_ms(),
        )
    if outcome == "booted_past":
        return _failed(
            outcome,
            "BREAKIN_BOOTED_PAST",
            f"the countdown appeared ({watch.matched_text()!r}) and {watch.sends} keypress(es) "
            f"were sent, but the board went on to start its kernel "
            f"({watch.handoff_text()!r}): it is not in the bootloader",
            "Read the tail between the countdown and the kernel start. The key may have come "
            "too late (match an earlier line with --expect, lower --repeat-ms), be the wrong "
            "key (try --send '\\x03' or '\\r'), or this build has the bootloader command line "
            "turned off — then no key stops it",
            "device",
        )
    if outcome == "matched_no_prompt":
        return _failed(
            outcome,
            "BREAKIN_NO_PROMPT",
            f"the countdown appeared ({watch.matched_text()!r}) and {watch.sends} keypress(es) "
            f"were sent, but no console prompt followed within {prompt_wait_s:g}s",
            "Run `alb serial status` to see where the board is now. At a prompt alb does "
            "not recognise (a bare '#' or '$' is not accepted by default) → pass --prompt; "
            "still booting → the key was ignored, try --send '\\r' or '\\x03' and a smaller "
            "--repeat-ms",
            "timeout",
        )
    return _failed(
        outcome,
        "BREAKIN_PATTERN_NOT_SEEN",
        f"no countdown matching {expect!r} appeared on the UART within {timeout_s:g}s",
        "Read the tail: if the board went straight into the kernel, this bootloader does "
        "not offer a key-press stop or prints a different text (pass --expect); if nothing "
        "arrived at all, check that the board actually reset and that the UART / baud are "
        "right (alb serial status)",
        "timeout",
    )


async def _reopen(
    session: _SessionTransport,
    dead: Any,
    deadline: float,
    backoff_s: float,
) -> Any | None:
    """Close ``dead`` and open a fresh session, or ``None`` when the deadline
    passed or the endpoint would not take us back."""
    # Closing a dead link may itself fail; there is nothing to do about it.
    with contextlib.suppress(Exception):
        await session.close_session(dead)
    remaining = deadline - perf_counter()
    if remaining <= 0:
        return None
    await asyncio.sleep(min(backoff_s, remaining))
    try:
        return await session.open_session()
    except Exception:  # reported by the caller as a link error
        return None
