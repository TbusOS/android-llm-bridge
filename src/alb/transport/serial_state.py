"""Serial / UART state machine.

The serial transport talks to whatever happens to be on the other end of a
UART. That "whatever" can be:

- a primary bootloader (SPL / BL1 / BL2) spitting hex addresses
- u-boot at its ``=>`` prompt
- the kernel mid-boot spewing printk (``[  0.000000] Booting Linux …``)
- init / systemd bringing up services
- a login prompt waiting for a username
- a user shell (``$``) or root shell (``#``)
- Android recovery (``:/ #``)
- fastboot mode
- a kernel panic with the last 200 lines of registers and call stack
- an unrelated crash (Oops / BUG / WARNING) that's survivable
- nothing at all (board powered off, UART unplugged, driver stuck)
- garbage bytes because the baud rate is wrong

This module classifies the trailing bytes of the UART stream into one of
those states, tracks transitions, and emits events. Downstream (serial.py,
capability layer, Web UI) routes behaviour on the state.

The state machine is deliberately a **pure classifier + buffer** plus a
thin event-emitter wrapper. It never owns the socket. :class:`SerialTransport`
feeds bytes in, reads ``state`` back, and acts accordingly.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from time import perf_counter


class SerialState(str, Enum):
    """Coarse-grained states a UART endpoint can be in.

    Enum values are lowercase so they serialize cleanly into TOML / JSON
    (workspace state logs, ``/device/status/ws`` events, CLI output).
    """

    UNKNOWN = "unknown"
    """Just connected; nothing classifiable seen yet. Also the fallback
    when trailing bytes don't match any known pattern."""

    IDLE = "idle"
    """Buffer is empty. The endpoint emitted nothing. Likely causes:
    board is powered off, UART isn't wired, or the bridge / ser2net is
    connected but the device isn't."""

    CORRUPTED = "corrupted"
    """Mostly non-printable bytes. Almost always a baud-rate mismatch.
    The classifier flags this before looking at patterns so a garbled
    buffer doesn't get mislabelled as UNKNOWN."""

    SPL = "spl"
    """Primary / pre-u-boot bootloader chatter (SPL, BL1, BL2, DDR init,
    "ATF" on ARMv8). Shell is absolutely not available; only raw logs."""

    UBOOT = "uboot"
    """U-Boot is waiting at its shell prompt (``=>``, ``boot>``, ``U-Boot>``).
    A limited shell is available: ``printenv``, ``setenv``, ``bootm``, etc.
    No POSIX ``$?`` exit codes."""

    KERNEL_BOOT = "kernel_boot"
    """Kernel is booting; printk is active. Typical markers:
    ``Booting Linux on …`` / ``Starting kernel …`` / ``[    0.000000]``.
    Shell is not available yet."""

    LINUX_INIT = "linux_init"
    """Init / systemd / rc scripts are running. Still no login. This
    phase is short on Android and often invisible, but matters for
    embedded Linux boards."""

    LOGIN_PROMPT = "login_prompt"
    """A login prompt is waiting for a username. Common on buildroot
    and server distros. Android rarely shows this."""

    SHELL_USER = "shell_user"
    """Interactive non-root shell; prompt ends in ``$``."""

    SHELL_ROOT = "shell_root"
    """Interactive root shell; prompt ends in ``#``. This is what most
    Android devices give you over UART."""

    RECOVERY = "recovery"
    """Android recovery shell. Prompt is usually ``:/  #`` — more
    specific than a plain root ``#`` — so we pattern-match it first and
    avoid collapsing it into SHELL_ROOT."""

    FASTBOOT = "fastboot"
    """Board is in fastboot mode. UART usually echoes the fastboot
    command protocol; the shell wrapper is not applicable."""

    PANIC = "panic"
    """Kernel panic or fatal Oops. The buffer tail has the crash
    register dump / call stack. Only readable output is useful; no
    shell command can run until reboot."""

    CRASH = "crash"
    """Non-fatal: ``BUG:`` / ``WARNING:`` / ``Call Trace:`` appeared,
    but the kernel is still up. The shell may still be usable."""


# ─── Pattern set ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class PatternSet:
    """Compiled regex patterns for each classifiable state.

    Keyed by state name (matches :class:`SerialState` values).

    Regexes operate on ``bytes`` and are designed to match the trailing
    portion of a rolling buffer. They're deliberately conservative:
    prompt patterns anchor to ``$`` (end of string) so mid-stream
    occurrences don't trigger a false transition.
    """

    uboot: re.Pattern[bytes]
    spl: re.Pattern[bytes]
    kernel_boot: re.Pattern[bytes]
    linux_init: re.Pattern[bytes]
    login_prompt: re.Pattern[bytes]
    shell_user: re.Pattern[bytes]
    shell_root: re.Pattern[bytes]
    recovery: re.Pattern[bytes]
    fastboot: re.Pattern[bytes]
    panic: re.Pattern[bytes]
    crash: re.Pattern[bytes]

    @classmethod
    def default(cls) -> PatternSet:
        """The built-in pattern set — covers mainstream Android / embedded
        Linux boards without any configuration.
        """
        return cls._from_raw(DEFAULT_PATTERNS)

    @classmethod
    def from_mapping(
        cls,
        overrides: Mapping[str, str],
    ) -> PatternSet:
        """Build a pattern set from a mapping of ``state_name -> regex_str``.

        Any missing state falls back to the default. This is what binds
        ``[transport.serial.prompts]`` in ``config.toml`` into a usable
        :class:`PatternSet` at load time.

        The regex strings are compiled here — if a user's TOML has a
        broken pattern the error surfaces immediately.
        """
        merged = dict(DEFAULT_PATTERNS)
        for key, pattern in overrides.items():
            if key not in merged:
                raise ValueError(
                    f"unknown serial state pattern key: {key!r} "
                    f"(expected one of {sorted(merged)})"
                )
            merged[key] = pattern.encode("utf-8") if isinstance(pattern, str) else pattern
        return cls._from_raw(merged)

    @classmethod
    def _from_raw(cls, raw: Mapping[str, bytes]) -> PatternSet:
        try:
            return cls(
                uboot=re.compile(raw["uboot"]),
                spl=re.compile(raw["spl"]),
                kernel_boot=re.compile(raw["kernel_boot"]),
                linux_init=re.compile(raw["linux_init"]),
                login_prompt=re.compile(raw["login_prompt"]),
                shell_user=re.compile(raw["shell_user"]),
                shell_root=re.compile(raw["shell_root"]),
                recovery=re.compile(raw["recovery"]),
                fastboot=re.compile(raw["fastboot"]),
                panic=re.compile(raw["panic"]),
                crash=re.compile(raw["crash"]),
            )
        except re.error as e:
            raise ValueError(f"invalid serial state regex: {e}") from e


# Default patterns. Chosen conservatively: every prompt pattern anchors
# to end-of-buffer (``\s*$``) so a ``#`` character appearing inside a
# command's output doesn't get classified as a root shell.
#
# Order in this dict does NOT matter — priority is enforced in
# :func:`classify` below.
DEFAULT_PATTERNS: dict[str, bytes] = {
    # U-Boot interactive prompt. Covers ``=>``, ``U-Boot>``, generic
    # ``boot>``. We require end-of-buffer so the literal word
    # ``U-Boot`` inside a banner doesn't trigger.
    "uboot": rb"(?:U-Boot\s*>|=>|boot>)\s*$",
    # Very-early bootloader signatures. Match anywhere, not anchored.
    "spl": rb"(?:U-Boot SPL|BL1:|BL2:|BL31:|DDR init|PMIC init)",
    # Kernel boot-stage signatures. We only match the canonical early-
    # boot text markers — a bare printk timestamp ``[ 420.306232]``
    # keeps arriving on a fully-booted system too (wifi up/down,
    # thermal events, any dmesg activity), so matching it alone would
    # mis-classify a perfectly healthy shell as "still booting".
    # If the board is REALLY booting, one of these phrases will appear.
    "kernel_boot": rb"(?:Booting Linux|Starting kernel|Linux version\s+\d)",
    # Init / systemd / rc.d traces.
    "linux_init": rb"(?:systemd\[|init:|Running early|Reached target|type=AVC)",
    # ``login:`` / ``Username:`` — anchored so we don't catch the
    # literal word in a motd.
    "login_prompt": rb"(?:login|Username):\s*$",
    # POSIX-ish shell prompts. We accept anything that isn't a
    # control char before the final ``$`` / ``#`` and allow up to one
    # trailing whitespace. ``shell_root`` and ``shell_user`` are
    # mutually exclusive at the tail anchor.
    "shell_user": rb"[^\x00-\x1f#$]*\$\s*$",
    "shell_root": rb"[^\x00-\x1f#$]*#\s*$",
    # Android recovery — prompt is either the literal ``recovery:/…#``
    # with a path, or a bare ``~ #`` / ``~ # ``. A plain ``root@host:/ #``
    # must NOT match here (we want that as SHELL_ROOT).
    "recovery": rb"(?:recovery[:/][^\s]*|(?:^|\s)~)\s*#\s*$",
    # Fastboot doesn't usually print a prompt over UART, but some
    # bootloaders do echo ``fastboot>`` in debug mode.
    "fastboot": rb"fastboot\s*[>$]\s*$",
    # Panic markers. ``Kernel panic`` is canonical; the others are
    # early-crash variants.
    "panic": rb"(?:Kernel panic|Unable to handle kernel|die\+0x|Internal error:\s+Oops)",
    # Non-fatal crash traces. The kernel is still alive.
    "crash": rb"(?:BUG:|WARNING:|Call Trace:|--- cut here ---)",
}


# ─── Classifier ──────────────────────────────────────────────────────


def classify(
    buffer: bytes,
    patterns: PatternSet | None = None,
    *,
    window: int = 4096,
    corruption_threshold: float = 0.30,
) -> SerialState:
    """Classify the trailing ``window`` bytes of ``buffer`` into a state.

    Priority, highest first:

    1. :data:`SerialState.IDLE` if buffer is empty.
    2. :data:`SerialState.CORRUPTED` if non-printable density is too high
       (almost always a baud-rate mismatch).
    3. :data:`SerialState.PANIC`  — terminal; beats everything.
    4. Prompt states (all tail-anchored; represent CURRENT reality):
       :data:`SerialState.FASTBOOT` / :data:`SerialState.RECOVERY` /
       :data:`SerialState.SHELL_ROOT` / :data:`SerialState.SHELL_USER` /
       :data:`SerialState.LOGIN_PROMPT` / :data:`SerialState.UBOOT`.
    5. Boot-phase markers, **latest first**:
       :data:`SerialState.LINUX_INIT` → :data:`SerialState.KERNEL_BOOT`
       → :data:`SerialState.SPL`. Checking LINUX_INIT before KERNEL_BOOT
       before SPL keeps the classifier monotonic during boot: once the
       kernel is up we don't get dragged back to SPL just because an
       old SPL line is still in the rolling buffer.
    6. :data:`SerialState.CRASH`  — soft; only when no prompt matches
       the tail.
    7. :data:`SerialState.UNKNOWN`  — nothing matched.

    Why this order:

    - PANIC wins over CRASH because a BUG/WARNING line often shows up
      *as part of* a panic's output. Classifying PANIC first avoids
      downgrading.
    - Prompt states dominate log markers because a prompt just got
      printed — it IS the current truth. A late kernel printk line
      showing up after a shell is live should not demote us back to
      KERNEL_BOOT.
    - RECOVERY wins over SHELL_ROOT because recovery prompts contain
      ``#`` but we want the more specific label.
    - FASTBOOT wins over UBOOT because ``fastboot>`` would otherwise
      look close to a u-boot prompt.
    """
    if patterns is None:
        patterns = PatternSet.default()

    if not buffer:
        return SerialState.IDLE

    if _is_corrupted(buffer, threshold=corruption_threshold):
        return SerialState.CORRUPTED

    tail = buffer[-window:]

    # Panic wins over everything (terminal state).
    if patterns.panic.search(tail):
        return SerialState.PANIC

    # Prompt states are tail-anchored — they represent CURRENT reality
    # (a prompt just got printed). These beat any lingering log marker.
    if patterns.fastboot.search(tail):
        return SerialState.FASTBOOT
    if patterns.recovery.search(tail):
        return SerialState.RECOVERY
    if patterns.shell_root.search(tail):
        return SerialState.SHELL_ROOT
    if patterns.shell_user.search(tail):
        return SerialState.SHELL_USER
    if patterns.login_prompt.search(tail):
        return SerialState.LOGIN_PROMPT
    if patterns.uboot.search(tail):
        return SerialState.UBOOT

    # Boot-phase markers — check LATEST first. If both a KERNEL_BOOT
    # line and an earlier SPL line are in the window, we've clearly
    # progressed to kernel-booting, so KERNEL_BOOT wins. This keeps
    # the classifier monotonic as the board boots.
    if patterns.linux_init.search(tail):
        return SerialState.LINUX_INIT
    if patterns.kernel_boot.search(tail):
        return SerialState.KERNEL_BOOT
    if patterns.spl.search(tail):
        return SerialState.SPL

    # Soft crash (kernel BUG/WARNING with shell still alive) — low
    # priority because a trailing prompt, if present, already won above.
    if patterns.crash.search(tail):
        return SerialState.CRASH

    return SerialState.UNKNOWN


def _is_corrupted(
    buffer: bytes,
    *,
    sample_size: int = 512,
    threshold: float = 0.30,
) -> bool:
    """Heuristic: wrong baud → majority non-printable bytes.

    We check the trailing ``sample_size`` bytes and count bytes that
    are neither printable ASCII nor common control chars (``\\t``,
    ``\\n``, ``\\r``, ``\\x1b`` for ANSI). If more than ``threshold``
    of the sample is outside that set, declare corruption.

    Small buffers aren't checked — a 4-byte prompt fragment shouldn't
    be judged as noise.
    """
    sample = buffer[-sample_size:]
    if len(sample) < 32:
        return False
    bad = 0
    for b in sample:
        if 32 <= b < 127:
            continue  # printable ASCII
        if b in (9, 10, 13, 27):
            continue  # tab, LF, CR, ESC
        bad += 1
    return bad / len(sample) > threshold


# ─── Stateful wrapper ────────────────────────────────────────────────


@dataclass(frozen=True)
class StateTransition:
    """A single observed state change.

    Keeps both a monotonic clock reading (``at``, from
    :func:`time.perf_counter` — useful for measuring durations) and a
    wall-clock ISO8601 string (``wall_time`` — useful for workspace
    logs that humans read).
    """

    from_state: SerialState
    to_state: SerialState
    at: float
    wall_time: str

    def as_dict(self) -> dict[str, str | float]:
        return {
            "from": self.from_state.value,
            "to": self.to_state.value,
            "at": self.at,
            "wall_time": self.wall_time,
        }


TransitionListener = Callable[[StateTransition], None]


@dataclass
class SerialStateMachine:
    """Event-emitting stateful classifier over a rolling UART buffer.

    Typical usage from :class:`alb.transport.serial.SerialTransport`::

        sm = SerialStateMachine()
        async for chunk in read_forever(link):
            state = sm.feed(chunk)
            if state == SerialState.SHELL_ROOT:
                break

    The machine keeps a rolling buffer of the most recent ``buffer_size``
    bytes and reclassifies after every :meth:`feed`. On state changes,
    registered listeners fire synchronously (they should be cheap —
    logging, metric increment, WebSocket emit).

    The buffer deliberately uses ``bytearray`` and trims in place so
    long-lived machines don't accumulate unbounded memory.
    """

    patterns: PatternSet = field(default_factory=PatternSet.default)
    buffer_size: int = 16384
    _buffer: bytearray = field(default_factory=bytearray, init=False, repr=False)
    _current: SerialState = field(default=SerialState.UNKNOWN, init=False)
    _history: list[StateTransition] = field(default_factory=list, init=False, repr=False)
    _listeners: list[TransitionListener] = field(default_factory=list, init=False, repr=False)

    # ── Properties ──────────────────────────────────────────────────

    @property
    def state(self) -> SerialState:
        """Current classification result."""
        return self._current

    @property
    def buffer_tail(self) -> bytes:
        """A copy of the current rolling buffer."""
        return bytes(self._buffer)

    @property
    def history(self) -> list[StateTransition]:
        """All recorded transitions, in order.

        Returns a copy so callers can't mutate internal history.
        """
        return list(self._history)

    # ── Core API ────────────────────────────────────────────────────

    def feed(self, data: bytes) -> SerialState:
        """Append ``data`` to the rolling buffer, reclassify, return state.

        If the classification changed, a :class:`StateTransition` is
        recorded and all registered listeners are notified before this
        method returns.
        """
        if not data:
            return self._current

        self._buffer.extend(data)
        if len(self._buffer) > self.buffer_size:
            # Trim in place — keep only the most recent buffer_size bytes.
            del self._buffer[: len(self._buffer) - self.buffer_size]

        new_state = classify(bytes(self._buffer), self.patterns)
        if new_state != self._current:
            transition = StateTransition(
                from_state=self._current,
                to_state=new_state,
                at=perf_counter(),
                wall_time=datetime.now(timezone.utc).isoformat(),
            )
            self._current = new_state
            self._history.append(transition)
            for listener in self._listeners:
                listener(transition)
        return new_state

    def on_transition(self, callback: TransitionListener) -> None:
        """Register a callback invoked on every state change.

        Callbacks are synchronous and run in :meth:`feed`'s call site
        — keep them fast. Async side effects should ``asyncio.create_task``
        internally.
        """
        self._listeners.append(callback)

    def reset(self) -> None:
        """Clear the buffer, history, and reset state to UNKNOWN.

        Used when a connection is re-opened after a reboot / dropped link.
        Listeners remain registered.
        """
        self._buffer.clear()
        self._current = SerialState.UNKNOWN
        self._history.clear()

    # ── Observability ──────────────────────────────────────────────

    def snapshot(self, *, tail_bytes: int = 256, history_n: int = 10) -> dict:
        """Serializable state summary for workspace logs / WebSocket / CLI.

        Returns a dict with:

        - ``state``: current state value (string)
        - ``tail``: last ``tail_bytes`` of buffer, utf-8-decoded with
          replacement (humans read this, not machines)
        - ``history``: last ``history_n`` transitions as dicts
        - ``buffer_bytes``: total bytes in rolling buffer right now
        """
        tail = bytes(self._buffer[-tail_bytes:]) if self._buffer else b""
        return {
            "state": self._current.value,
            "tail": tail.decode("utf-8", errors="replace"),
            "history": [t.as_dict() for t in self._history[-history_n:]],
            "buffer_bytes": len(self._buffer),
        }
