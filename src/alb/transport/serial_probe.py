"""UART baud-rate probe.

When bringing up an unfamiliar board, the very first question is "what
baud rate is this UART?". Guessing produces garbage bytes and wastes
time. :func:`probe_bauds` cycles through a configurable list of common
rates, opens the serial device at each rate for a short window, and
measures how printable the received bytes are. The combination of
**printable ASCII density** and **whether the state machine can
classify what it saw** gives a clear signal for which baud is right.

Scope — local devices only
---------------------------
This works for local ``/dev/ttyUSB*`` / ``/dev/ttyACM*`` style paths.
It does **not** work for the ser2net TCP-bridge setup (our
Xshell-tunnel + Windows-side ``windows_serial_bridge.py`` topology),
because the bridge's baud is fixed at launch time — to try a different
rate the user has to restart the bridge. :func:`probe_hint_for_tcp`
generates the exact shell commands the user needs to run for that path.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import perf_counter

from alb.transport.serial_state import (
    PatternSet,
    SerialState,
    SerialStateMachine,
    classify,
)


# Rates ordered from most common downwards. Covering the 95 % of real
# boards: default Linux console 115200, mid-speed UART 921600, high
# end UART 1500000 (common on newer Rockchip / MediaTek), plus a few
# oddballs. Users can override with ``--rates`` in the CLI.
DEFAULT_RATES: tuple[int, ...] = (
    115200,
    921600,
    1500000,
    230400,
    9600,
    460800,
    3000000,
)


@dataclass(frozen=True)
class ProbeResult:
    """Per-baud measurement from one :func:`probe_bauds` pass."""

    baud: int
    """The baud rate used for this probe."""

    bytes_received: int
    """Total bytes read during the measurement window."""

    duration_s: float
    """Actual measurement window (honours early EOF)."""

    ascii_density: float
    """Fraction of bytes that are printable ASCII or common control
    chars. Range 0.0–1.0. Higher = more likely correct baud. ``0.0``
    when no bytes were received (undefined — treat as "no signal")."""

    state: SerialState
    """Result of running the collected buffer through :func:`classify`.
    A non-trivial state (SHELL_*/UBOOT/KERNEL_BOOT/LINUX_INIT/CRASH)
    is a strong positive signal that the baud is correct."""

    sample: bytes
    """Up to the first 256 bytes of what was received, for UI display."""

    error: str | None = None
    """If the attempt failed (permission denied / device busy / pyserial
    missing), a short human-readable reason. The baud is reported as
    ``0`` density and ``UNKNOWN`` state in that case."""

    @property
    def ok(self) -> bool:
        """True if the probe actually got useful bytes to judge."""
        return self.error is None and self.bytes_received > 0

    @property
    def is_recommended_candidate(self) -> bool:
        """Heuristic: clear candidate when the buffer classifies into a
        non-trivial state (shell / boot / prompt) or ASCII density is
        >= 0.90 with >= 64 bytes collected. Used by the CLI to pick
        the ★ row.
        """
        if not self.ok:
            return False
        if self.state in (
            SerialState.SHELL_USER,
            SerialState.SHELL_ROOT,
            SerialState.UBOOT,
            SerialState.RECOVERY,
            SerialState.LOGIN_PROMPT,
            SerialState.KERNEL_BOOT,
            SerialState.LINUX_INIT,
            SerialState.PANIC,
        ):
            return True
        return self.ascii_density >= 0.90 and self.bytes_received >= 64


async def probe_bauds(
    device: str,
    *,
    rates: tuple[int, ...] = DEFAULT_RATES,
    duration_s: float = 2.0,
    patterns: PatternSet | None = None,
) -> list[ProbeResult]:
    """Open ``device`` at each rate in turn, measure briefly, return all
    :class:`ProbeResult`\\ s in the order probed.

    Each probe opens a fresh serial connection (the board stays the
    same; only our side's rate changes). We send **no data** — just
    listen. If the board is silent at a given rate, the result has
    ``bytes_received=0`` and we move on.

    Parameters
    ----------
    device
        Local serial path (``/dev/ttyUSB0`` / ``/dev/ttyACM0`` …).
    rates
        Ordered list of baud rates to try.
    duration_s
        Listen window per rate. 2s is plenty — a healthy UART at the
        right baud fills the buffer in < 500 ms.
    patterns
        Custom :class:`PatternSet` for state classification. ``None``
        uses the built-in defaults.
    """
    if not device.startswith("/dev/"):
        raise ValueError(
            "probe_bauds() is for local /dev/tty* paths; "
            "for TCP/ser2net use probe_hint_for_tcp() instead"
        )

    try:
        import serial_asyncio  # type: ignore[import-not-found]
    except ImportError:
        return [
            ProbeResult(
                baud=r, bytes_received=0, duration_s=0.0,
                ascii_density=0.0, state=SerialState.UNKNOWN,
                sample=b"", error="pyserial-asyncio not installed (uv add pyserial-asyncio)",
            )
            for r in rates
        ]

    results: list[ProbeResult] = []
    for baud in rates:
        results.append(
            await _probe_one(device, baud, duration_s, patterns, serial_asyncio)
        )
    return results


async def _probe_one(
    device: str,
    baud: int,
    duration_s: float,
    patterns: PatternSet | None,
    serial_asyncio,  # noqa: ANN001
) -> ProbeResult:
    """Open ``device`` at ``baud``, listen for ``duration_s``, measure."""
    start = perf_counter()
    buf = bytearray()

    try:
        reader, writer = await serial_asyncio.open_serial_connection(
            url=device, baudrate=baud,
        )
    except Exception as e:
        return ProbeResult(
            baud=baud, bytes_received=0, duration_s=0.0,
            ascii_density=0.0, state=SerialState.UNKNOWN,
            sample=b"", error=str(e),
        )

    try:
        deadline = perf_counter() + duration_s
        while perf_counter() < deadline:
            remaining = max(0.05, deadline - perf_counter())
            try:
                chunk = await asyncio.wait_for(reader.read(1024), timeout=remaining)
            except asyncio.TimeoutError:
                break
            if not chunk:
                break
            buf.extend(chunk)
    finally:
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), timeout=1.0)
        except (asyncio.TimeoutError, Exception):
            pass

    elapsed = perf_counter() - start
    density = _ascii_density(bytes(buf))
    state = classify(bytes(buf), patterns) if buf else SerialState.IDLE

    return ProbeResult(
        baud=baud,
        bytes_received=len(buf),
        duration_s=elapsed,
        ascii_density=density,
        state=state,
        sample=bytes(buf[:256]),
    )


def _ascii_density(data: bytes) -> float:
    """Fraction of printable ASCII + common control chars in the sample.

    Matches :func:`alb.transport.serial_state._is_corrupted`'s notion
    of "printable" for consistency. Returns 0.0 for empty input.
    """
    if not data:
        return 0.0
    good = 0
    for b in data:
        if 32 <= b < 127:
            good += 1
        elif b in (9, 10, 13, 27):
            good += 1
    return good / len(data)


def pick_best(results: list[ProbeResult]) -> ProbeResult | None:
    """Choose the single best candidate from a probe run.

    Ranking rule, simple and explicit:

    1. Prefer results where the state machine classified into a prompt
       state (shell/uboot/recovery/…) — those are definitive wins.
    2. Else prefer the highest ASCII density (break ties by bytes
       received).
    3. ``None`` when nothing is usable.
    """
    usable = [r for r in results if r.ok]
    if not usable:
        return None

    def rank(r: ProbeResult) -> tuple[int, float, int]:
        strong_state = r.state in (
            SerialState.SHELL_USER, SerialState.SHELL_ROOT,
            SerialState.UBOOT, SerialState.RECOVERY,
            SerialState.LOGIN_PROMPT, SerialState.KERNEL_BOOT,
            SerialState.LINUX_INIT, SerialState.PANIC,
        )
        return (1 if strong_state else 0, r.ascii_density, r.bytes_received)

    usable.sort(key=rank, reverse=True)
    return usable[0]


def probe_hint_for_tcp(
    tcp_host: str,
    tcp_port: int,
    rates: tuple[int, ...] = DEFAULT_RATES,
    *,
    bridge_script: str = "windows_serial_bridge.py",
    com_port: str = "COM<your-port>",
) -> str:
    """Return a helpful message for TCP/ser2net topology.

    The TCP bridge fixes the baud at launch. Our probe can't iterate
    rates over a TCP socket — it would need the bridge to support a
    control protocol we haven't built. Instead we give the user the
    exact commands to try manually, plus a note that each run requires
    restarting the bridge.
    """
    lines = [
        f"TCP endpoint ({tcp_host}:{tcp_port}) — the Windows-side bridge fixes the baud at launch.",
        "To try different rates, restart the bridge with each one and call",
        f"  alb --json serial status",
        f"after each restart. Suggested rates, in priority order:",
        "",
    ]
    for r in rates:
        lines.append(f"  python {bridge_script} --com {com_port} --baud {r}")
    lines.extend([
        "",
        "The rate that produces state=shell_root / uboot / kernel_boot is the one.",
        "(If all rates give state=corrupted, the board may not be emitting UART at all —",
        "check power, cable, and that the bridge truly opened the COM port.)",
    ])
    return "\n".join(lines)
