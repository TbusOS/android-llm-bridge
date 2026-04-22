"""SerialTransport — method G (UART).

Two connection modes:
    1. TCP (ser2net + Xshell reverse tunnel, the remote-Windows setup
       described in docs/methods/07-uart-serial.md).
    2. Local device (/dev/ttyUSB0, /dev/ttyACM0, …), when the host is
       directly wired to the target.

Capabilities:
    - shell(cmd)          : prompt-based; best-effort, works for simple
                            commands once a shell is reachable on UART.
    - stream_read('uart') : raw byte stream; this is UART's primary use.
    - reboot('normal')    : sends 'reboot' to the shell.
    - push / pull         : unsupported (bandwidth too low).
    - forward             : unsupported.

UART's *unique* value is capturing boot logs / kernel panics / u-boot
output when adb / sshd aren't up yet. The shell wrapper is provided for
convenience but for heavy work you should use stream_read + alb_log_*.
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

from alb.transport.base import ShellResult, Transport
from alb.transport.serial_state import (
    PatternSet,
    SerialState,
    SerialStateMachine,
)


# ─── Constants ─────────────────────────────────────────────────────
DEFAULT_PROMPTS = (
    b"$ ",    # unprivileged Android shell
    b"# ",    # root shell
    b"=> ",   # u-boot
    b"> ",    # some bootloaders
)


# States where :meth:`SerialTransport.shell` can send a command and
# expect a meaningful response. Everything outside this set is rejected
# fast with a specific error code.
_SHELL_CAPABLE = frozenset({
    SerialState.SHELL_USER,
    SerialState.SHELL_ROOT,
    SerialState.UBOOT,
    SerialState.RECOVERY,
    SerialState.CRASH,     # soft crash — shell may still work; we warn
    SerialState.UNKNOWN,   # nothing matched — fall back to best-effort
})


# Subset of shell-capable states that are POSIX-like — they support
# command chaining with ``;`` and ``$?`` exit-code introspection. We
# use marker-based wrapping on these to recover real exit codes and
# precise command boundaries. U-Boot and Recovery don't qualify
# (u-boot has no ``$?``; Android recovery has a reduced shell).
_POSIX_SHELL_STATES = frozenset({
    SerialState.SHELL_USER,
    SerialState.SHELL_ROOT,
    SerialState.CRASH,   # kernel crashed but userspace shell still POSIX
})


# Map from non-shell-capable state → error code for shell() rejection.
_STATE_REJECT: dict[SerialState, str] = {
    SerialState.PANIC: "BOARD_PANICKED",
    SerialState.IDLE: "BOARD_UNREACHABLE",
    SerialState.CORRUPTED: "SERIAL_BAUD_MISMATCH",
    SerialState.SPL: "BOARD_BOOTING",
    SerialState.KERNEL_BOOT: "BOARD_BOOTING",
    SerialState.LINUX_INIT: "BOARD_BOOTING",
    SerialState.LOGIN_PROMPT: "BOARD_NEEDS_LOGIN",
    SerialState.FASTBOOT: "BOARD_IN_FASTBOOT",
}


@dataclass(frozen=True)
class _SerialLink:
    """An open reader/writer pair abstracting TCP and local-serial alike."""

    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    closer: Any  # optional extra callable to close on cleanup
    mode: str  # "tcp" | "local"


class SerialTransport(Transport):
    name = "serial"
    supports_boot_log = True
    supports_recovery = False  # sshd/adbd are gone in recovery; u-boot yes but not recovery

    def __init__(
        self,
        *,
        device: str | None = None,          # e.g. "/dev/ttyUSB0"
        tcp_host: str | None = None,        # e.g. "localhost"
        tcp_port: int | None = None,        # e.g. 9001
        baud: int = 115200,
        prompts: tuple[bytes, ...] = DEFAULT_PROMPTS,
        patterns: PatternSet | None = None,
        newline: bytes = b"\n",
        read_chunk: int = 4096,
        handshake_timeout: float = 2.0,
    ) -> None:
        """SerialTransport — UART + optional ser2net TCP bridge.

        Parameters
        ----------
        patterns
            Regex set used by the :class:`SerialStateMachine` to classify
            what's at the other end (shell / u-boot / kernel panic / etc).
            Pass ``None`` to use the built-in defaults, or build a custom
            set from ``config.toml`` via
            :meth:`PatternSet.from_mapping`.
        handshake_timeout
            How long to wait during the initial probe before giving up
            and declaring UNKNOWN. 2s is enough for ser2net even over a
            slow tunnel; bump for very remote links.
        prompts
            Legacy byte-level prompt list used by the best-effort
            ``_read_until_any`` / ``_strip_echo_and_prompt`` fallback
            path (state = UNKNOWN). New deployments should rely on
            ``patterns`` instead.
        """
        if device and (tcp_host or tcp_port):
            raise ValueError("Pass either `device` or `tcp_host`+`tcp_port`, not both")
        if not device and not (tcp_host and tcp_port):
            raise ValueError(
                "SerialTransport needs either a local device path "
                "(/dev/ttyUSB*) or tcp_host+tcp_port (ser2net)."
            )
        self.device = device
        self.tcp_host = tcp_host
        self.tcp_port = tcp_port
        self.baud = baud
        self.prompts = prompts
        self.patterns = patterns if patterns is not None else PatternSet.default()
        self.newline = newline
        self.read_chunk = read_chunk
        self.handshake_timeout = handshake_timeout
        self._link: _SerialLink | None = None

    # ── Connection management ────────────────────────────────────
    async def _open(self) -> _SerialLink:
        """Open a connection. Caller is responsible for closing via _close()."""
        if self.tcp_host and self.tcp_port:
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self.tcp_host, self.tcp_port),
                    timeout=10,
                )
            except (OSError, asyncio.TimeoutError) as e:
                raise ConnectionError(
                    f"Cannot reach ser2net endpoint {self.tcp_host}:{self.tcp_port}: {e}"
                ) from e
            return _SerialLink(reader=reader, writer=writer, closer=None, mode="tcp")

        assert self.device is not None
        if not os.path.exists(self.device):
            raise FileNotFoundError(
                f"Serial device not found: {self.device}. "
                "Check permissions (add user to `dialout` group) or the USB-serial cable."
            )
        try:
            # Lazy import so the rest of alb works even if pyserial-asyncio isn't installed.
            import serial_asyncio  # type: ignore[import-not-found]
        except ImportError as e:
            raise ImportError(
                "pyserial-asyncio is required for local serial devices. "
                "Install with: uv add pyserial-asyncio"
            ) from e

        reader, writer = await serial_asyncio.open_serial_connection(
            url=self.device, baudrate=self.baud
        )
        return _SerialLink(reader=reader, writer=writer, closer=None, mode="local")

    async def _close(self, link: _SerialLink) -> None:
        try:
            link.writer.close()
            try:
                await asyncio.wait_for(link.writer.wait_closed(), timeout=2)
            except asyncio.TimeoutError:
                pass
        except Exception:
            pass

    # ── Transport interface ──────────────────────────────────────
    async def shell(self, cmd: str, *, timeout: int = 30) -> ShellResult:
        """Run a command on the UART endpoint.

        Flow:

        1. Connect.
        2. **Handshake** — probe for up to ``handshake_timeout`` seconds,
           feeding all bytes into a :class:`SerialStateMachine`.
           Classify the initial state.
        3. **Route on state**:

           * :data:`SerialState.PANIC` → fail with ``BOARD_PANICKED``,
             return the panic tail as ``stdout`` for diagnostic value.
           * :data:`SerialState.IDLE` → ``BOARD_UNREACHABLE`` (no bytes
             observed at all — check power / cable / baud).
           * :data:`SerialState.CORRUPTED` → ``SERIAL_BAUD_MISMATCH``.
           * SPL / KERNEL_BOOT / LINUX_INIT → ``BOARD_BOOTING``.
           * :data:`SerialState.LOGIN_PROMPT` → ``BOARD_NEEDS_LOGIN``.
           * :data:`SerialState.FASTBOOT` → ``BOARD_IN_FASTBOOT``.
           * SHELL / UBOOT / RECOVERY / CRASH / UNKNOWN → proceed.

        4. Send ``cmd``.
        5. Read until a legacy prompt pattern reappears (for now).
           The richer marker-based exit-code handling lands in a later
           commit; this one preserves the existing output-stripping
           behaviour so all legacy tests keep passing.

        Notes
        -----
        - UNKNOWN falls through to the best-effort path instead of
          erroring. This preserves behaviour on weird endpoints that
          don't emit a classifiable prompt (custom bootloaders, bespoke
          RTOS shells), and matches the pre-state-machine contract.
        - If the state transitions INTO a panic during command execution,
          we return ``BOARD_PANICKED`` and include whatever we captured
          in ``stdout``.
        """
        start = perf_counter()
        try:
            link = await self._open()
        except (ConnectionError, FileNotFoundError, ImportError) as e:
            return ShellResult(
                ok=False,
                exit_code=-1,
                stderr=str(e),
                error_code=_classify_connect_error(e),
                duration_ms=int((perf_counter() - start) * 1000),
            )

        try:
            sm = SerialStateMachine(patterns=self.patterns)

            # ── Step 1: handshake ─────────────────────────────────
            initial_state = await self._handshake(link, sm)

            # ── Step 2: reject early if state can't run a command ─
            if initial_state not in _SHELL_CAPABLE:
                return self._reject_for_state(initial_state, sm, start)

            # ── Step 3: dispatch on state ──────────────────────────
            # POSIX shells → marker-based wrapper, real exit codes,
            # precise command boundaries even with printk noise.
            # Everything else → legacy "send + read until prompt" path.
            if initial_state in _POSIX_SHELL_STATES:
                return await self._run_with_marker(
                    link, sm, cmd, timeout, start, initial_state,
                )
            return await self._run_legacy(link, sm, cmd, timeout, start)
        finally:
            await self._close(link)

    # ── Command execution strategies ────────────────────────────────

    async def _run_with_marker(
        self,
        link: _SerialLink,
        sm: SerialStateMachine,
        cmd: str,
        timeout: int,
        start: float,
        initial_state: SerialState,
    ) -> ShellResult:
        """Execute ``cmd`` in a POSIX shell and capture real exit code.

        Wraps the user's command with BEGIN/END sentinel lines plus
        ``$?`` capture::

            echo __ALB_BEG_<nonce>__; <user_cmd>; echo __ALB_END_<nonce>__=$?

        Reads until the END marker line appears (with its ``=N`` suffix)
        or we time out. The nonce is a fresh UUID hex for every call,
        so collision with user content is practically impossible.

        Why this is better than the legacy path:

        - Real exit code in ``ShellResult.exit_code`` (non-zero → ``ok=False``).
        - Command boundaries are exact; printk lines spraying into the
          middle of output do not break stripping logic.
        - Multi-line or specially-quoted commands round-trip cleanly —
          the shell just runs them between our markers.

        If the END marker never appears before timeout, we fall back to
        a :code:`TIMEOUT_SHELL` result with whatever output arrived.
        """
        nonce = uuid.uuid4().hex[:12]
        beg_marker = f"__ALB_BEG_{nonce}__"
        end_marker = f"__ALB_END_{nonce}__"
        wrapped = (
            f"echo {beg_marker}; {cmd}; echo {end_marker}=$?\n"
        ).encode("utf-8", errors="replace")

        link.writer.write(wrapped)
        await link.writer.drain()

        deadline = perf_counter() + timeout
        response = bytearray()
        end_line_re = re.compile(
            re.escape(end_marker).encode("ascii") + rb"=(\d+)\r?\n"
        )
        match: re.Match[bytes] | None = None

        while perf_counter() < deadline:
            remaining = max(0.05, deadline - perf_counter())
            try:
                chunk = await asyncio.wait_for(
                    link.reader.read(self.read_chunk), timeout=remaining,
                )
            except asyncio.TimeoutError:
                break
            if not chunk:
                break
            response.extend(chunk)
            sm.feed(chunk)

            # Mid-command panic — return what we have.
            if sm.state == SerialState.PANIC:
                return ShellResult(
                    ok=False,
                    exit_code=-1,
                    stdout=bytes(response).decode("utf-8", errors="replace"),
                    stderr="kernel panic during command execution",
                    error_code="BOARD_PANICKED",
                    duration_ms=int((perf_counter() - start) * 1000),
                )

            match = end_line_re.search(response)
            if match:
                break

        if not match:
            return ShellResult(
                ok=False,
                exit_code=-1,
                stdout=bytes(response).decode("utf-8", errors="replace"),
                stderr=(
                    f"Serial shell timed out after {timeout}s; "
                    f"end marker {end_marker} never arrived"
                ),
                error_code="TIMEOUT_SHELL",
                duration_ms=int((perf_counter() - start) * 1000),
            )

        exit_code = int(match.group(1))
        text = bytes(response).decode("utf-8", errors="replace")
        stdout = _extract_between_markers(text, beg_marker, end_marker)

        stderr = ""
        if sm.state == SerialState.CRASH and initial_state != SerialState.CRASH:
            stderr = (
                "warning: kernel crash trace observed during command; "
                "shell is still up but state may be unstable"
            )

        return ShellResult(
            ok=(exit_code == 0),
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            error_code=None if exit_code == 0 else "SHELL_NONZERO_EXIT",
            duration_ms=int((perf_counter() - start) * 1000),
        )

    async def _run_legacy(
        self,
        link: _SerialLink,
        sm: SerialStateMachine,
        cmd: str,
        timeout: int,
        start: float,
    ) -> ShellResult:
        """Best-effort path for non-POSIX states (u-boot, recovery, unknown).

        Sends the command, reads until any legacy prompt pattern shows
        up at the tail, strips the echoed command line and trailing
        prompt. Exit code is always 0 (these environments don't report
        one) — ``ok=True`` means "we got a complete response", not
        "the command succeeded".
        """
        payload = (cmd + "\n").encode("utf-8", errors="replace")
        link.writer.write(payload)
        await link.writer.drain()

        try:
            raw = await asyncio.wait_for(
                _read_until_any(link.reader, self.prompts),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            return ShellResult(
                ok=False,
                exit_code=-1,
                stderr=f"Serial shell timed out after {timeout}s",
                error_code="TIMEOUT_SHELL",
                duration_ms=int((perf_counter() - start) * 1000),
            )

        sm.feed(raw)

        if sm.state == SerialState.PANIC:
            return ShellResult(
                ok=False,
                exit_code=-1,
                stdout=raw.decode("utf-8", errors="replace"),
                stderr="kernel panic during command execution",
                error_code="BOARD_PANICKED",
                duration_ms=int((perf_counter() - start) * 1000),
            )

        text = raw.decode("utf-8", errors="replace")
        stdout = _strip_echo_and_prompt(text, cmd, self.prompts)
        return ShellResult(
            ok=True,
            exit_code=0,
            stdout=stdout,
            duration_ms=int((perf_counter() - start) * 1000),
        )

    # ── State machine plumbing ──────────────────────────────────────

    async def _handshake(
        self,
        link: _SerialLink,
        sm: SerialStateMachine,
    ) -> SerialState:
        """Probe the endpoint to determine the initial state.

        Strategy, in order:

        1. Opportunistic read: gobble up anything already buffered
           (e.g., a prompt the device printed before we connected).
           Short per-chunk timeout keeps this from blocking.
        2. If that's inconclusive, send ``self.newline`` to nudge the
           device. Most shells respond with a fresh prompt.
        3. Read with the remaining handshake budget, feeding the state
           machine on every chunk. Stop early on decisive states
           (prompts, panic, corrupted).

        Returns the final classified state.
        """
        deadline = perf_counter() + self.handshake_timeout

        # Step 1: opportunistic pre-read of pending bytes
        for _ in range(5):
            remaining = deadline - perf_counter()
            if remaining <= 0:
                break
            try:
                chunk = await asyncio.wait_for(
                    link.reader.read(self.read_chunk), timeout=0.1,
                )
            except asyncio.TimeoutError:
                break
            if not chunk:
                break
            sm.feed(chunk)

        if _is_decisive(sm.state):
            return sm.state

        # Step 2: nudge with newline
        try:
            link.writer.write(self.newline)
            await link.writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            # Writer died during nudge; fall through with whatever we have.
            return sm.state

        # Step 3: drain until decisive or deadline
        while perf_counter() < deadline:
            remaining = max(0.05, deadline - perf_counter())
            try:
                chunk = await asyncio.wait_for(
                    link.reader.read(self.read_chunk), timeout=remaining,
                )
            except asyncio.TimeoutError:
                break
            if not chunk:
                break
            sm.feed(chunk)
            if _is_decisive(sm.state):
                break

        # An endpoint that emitted literally nothing across the whole
        # handshake window is ``IDLE`` — semantically richer than the
        # default ``UNKNOWN`` (which means "saw bytes but couldn't
        # classify"). Most common causes of IDLE: board powered off,
        # UART not wired on the device side, or some OTHER program
        # holding the COM port exclusively on the remote end.
        if not sm.buffer_tail and sm.state == SerialState.UNKNOWN:
            return SerialState.IDLE
        return sm.state

    def _reject_for_state(
        self,
        state: SerialState,
        sm: SerialStateMachine,
        start: float,
    ) -> ShellResult:
        """Build a structured :class:`ShellResult` for a non-runnable state.

        The panic case deserves special care: we include the tail of
        the buffer as ``stdout`` so the caller (or LLM) can see the
        panic message without a second round-trip.
        """
        code = _STATE_REJECT[state]
        duration = int((perf_counter() - start) * 1000)

        stdout = ""
        if state == SerialState.PANIC:
            stdout = sm.snapshot(tail_bytes=800)["tail"]

        # Default messages come from errors.py via the ShellResult
        # pipeline, but a state-specific stderr makes the returned
        # payload self-describing at a glance.
        stderr_map = {
            SerialState.PANIC:        "board is in kernel panic; only reboot can recover",
            SerialState.IDLE:         "no output observed on UART after handshake",
            SerialState.CORRUPTED:    "UART stream is mostly non-printable (wrong baud?)",
            SerialState.SPL:          "board is still in SPL / pre-u-boot phase",
            SerialState.KERNEL_BOOT:  "board is still in kernel boot phase",
            SerialState.LINUX_INIT:   "board is running init / systemd; no shell yet",
            SerialState.LOGIN_PROMPT: "login prompt waiting for a username",
            SerialState.FASTBOOT:     "board is in fastboot mode",
        }
        return ShellResult(
            ok=False,
            exit_code=-1,
            stdout=stdout,
            stderr=stderr_map[state],
            error_code=code,
            duration_ms=duration,
        )

    async def stream_read(
        self, source: str, **kwargs: Any
    ) -> AsyncIterator[bytes]:
        """Stream raw UART bytes.

        Accepts source='uart' (canonical) or source='dmesg' (for API
        compatibility with other transports — in practice you'll still see
        the full UART stream, filtered by capabilities/logging).
        """
        if source not in ("uart", "dmesg", "kmsg"):
            raise ValueError(f"SerialTransport does not support source {source!r}")

        try:
            link = await self._open()
        except (ConnectionError, FileNotFoundError, ImportError) as e:
            # Yield nothing and let the caller handle the empty stream via
            # its own timeout / duration limit. For better LLM diagnostics
            # we also write a single synthetic marker line.
            yield f"[alb serial open failed: {e}]\n".encode()
            return

        try:
            while True:
                try:
                    chunk = await link.reader.read(self.read_chunk)
                except (ConnectionResetError, OSError):
                    break
                if not chunk:
                    break
                yield chunk
        finally:
            await self._close(link)

    async def push(self, local: Path, remote: str) -> ShellResult:
        return ShellResult(
            ok=False,
            exit_code=-1,
            stderr="UART bandwidth is too low for file transfer; use adb/ssh instead.",
            error_code="TRANSPORT_NOT_SUPPORTED",
        )

    async def pull(self, remote: str, local: Path) -> ShellResult:
        return ShellResult(
            ok=False,
            exit_code=-1,
            stderr="UART bandwidth is too low for file transfer; use adb/ssh instead.",
            error_code="TRANSPORT_NOT_SUPPORTED",
        )

    async def reboot(self, mode: str = "normal") -> ShellResult:
        if mode != "normal":
            return ShellResult(
                ok=False,
                exit_code=-1,
                stderr=(
                    f"Serial reboot mode '{mode}' not supported. Use adb for "
                    "recovery/bootloader/fastboot/sideload; or interrupt u-boot "
                    "manually via alb serial send."
                ),
                error_code="TRANSPORT_NOT_SUPPORTED",
            )
        return await self.shell("reboot", timeout=5)

    async def detect_state(self) -> dict[str, Any]:
        """Connect, run handshake, and return a state snapshot.

        This is the programmatic entry point behind ``alb serial status``
        and the Web UI's device-state pill. It doesn't run a command —
        just classifies what the endpoint is currently doing.

        Returns a dict:

        - ``ok`` — True if connect + handshake completed (even if
          the state ended up UNKNOWN or IDLE).
        - ``connected`` — True if the transport reached the endpoint.
        - ``state`` — :class:`SerialState` value (string).
        - ``tail`` — last ~256 bytes as utf-8 text (human-readable).
        - ``history`` — list of transitions during the handshake.
        - ``endpoint`` — ``host:port`` or device path.
        - ``baud`` — configured baud rate.
        - ``duration_ms`` — handshake wall time.
        - On failure: ``error`` and ``error_code``.
        """
        start = perf_counter()
        info: dict[str, Any] = {
            "transport": "serial",
            "mode": "tcp" if self.tcp_host else "local",
            "endpoint": (
                f"{self.tcp_host}:{self.tcp_port}"
                if self.tcp_host else self.device
            ),
            "baud": self.baud,
        }

        try:
            link = await self._open()
        except (ConnectionError, FileNotFoundError, ImportError) as e:
            info["ok"] = False
            info["connected"] = False
            info["error"] = str(e)
            info["error_code"] = _classify_connect_error(e)
            info["duration_ms"] = int((perf_counter() - start) * 1000)
            return info

        try:
            sm = SerialStateMachine(patterns=self.patterns)
            state = await self._handshake(link, sm)
            snap = sm.snapshot()
            info.update(
                ok=True,
                connected=True,
                state=state.value,
                tail=snap["tail"],
                history=snap["history"],
                buffer_bytes=snap["buffer_bytes"],
                duration_ms=int((perf_counter() - start) * 1000),
            )
            return info
        finally:
            await self._close(link)

    async def health(self) -> dict[str, Any]:
        info: dict[str, Any] = {
            "transport": "serial",
            "mode": "tcp" if self.tcp_host else "local",
            "endpoint": (
                f"{self.tcp_host}:{self.tcp_port}"
                if self.tcp_host
                else self.device
            ),
            "baud": self.baud,
        }
        try:
            link = await self._open()
            info["ok"] = True
            info["connected"] = True
            await self._close(link)
        except Exception as e:  # noqa: BLE001
            info["ok"] = False
            info["connected"] = False
            info["error"] = str(e)
            info["error_code"] = _classify_connect_error(e)
        return info

    # ── Helpers unique to serial ─────────────────────────────────
    async def send_raw(self, data: bytes) -> ShellResult:
        """Fire-and-forget byte write — no prompt wait. Useful for u-boot
        interrupt sequences (e.g. repeated b'\\x03' for Ctrl-C).
        """
        start = perf_counter()
        try:
            link = await self._open()
        except Exception as e:  # noqa: BLE001
            return ShellResult(
                ok=False, exit_code=-1, stderr=str(e),
                error_code=_classify_connect_error(e),
                duration_ms=int((perf_counter() - start) * 1000),
            )
        try:
            link.writer.write(data)
            await link.writer.drain()
            return ShellResult(
                ok=True, exit_code=0, stdout="",
                duration_ms=int((perf_counter() - start) * 1000),
            )
        finally:
            await self._close(link)


# ─── Helpers ───────────────────────────────────────────────────────
async def _read_until_any(
    reader: asyncio.StreamReader,
    tokens: tuple[bytes, ...],
    *,
    max_bytes: int = 512 * 1024,
) -> bytes:
    """Read until we see ANY of the tokens (simulating `readuntil` with N markers)."""
    buf = b""
    while True:
        if len(buf) > max_bytes:
            return buf
        chunk = await reader.read(256)
        if not chunk:
            return buf
        buf += chunk
        for token in tokens:
            idx = buf.rfind(token)
            if idx != -1:
                return buf[: idx + len(token)]


def _strip_echo_and_prompt(
    text: str, cmd: str, prompts: tuple[bytes, ...]
) -> str:
    """Remove the echoed command line and trailing prompt from a captured segment."""
    # Drop leading echo: often '<cmd>\r\n'
    cmd_echo = cmd.strip()
    lines = text.splitlines(keepends=True)
    if lines and cmd_echo and cmd_echo in lines[0]:
        lines = lines[1:]
    # Strip trailing prompt line
    prompt_strs = tuple(p.decode("utf-8", errors="replace") for p in prompts)
    if lines:
        last = lines[-1].rstrip()
        for p in prompt_strs:
            if last.endswith(p.rstrip()):
                lines = lines[:-1]
                break
    # Filter trailing empty lines
    out = "".join(lines)
    return re.sub(r"\r\n", "\n", out).strip("\n") + ("\n" if out.endswith("\n") else "")


def _extract_between_markers(text: str, beg: str, end: str) -> str:
    """Pull the user command's stdout from between BEG/END marker lines.

    Raw text layout when a POSIX shell echoes back a wrapped command::

        echo __ALB_BEG_abc__; some_cmd; echo __ALB_END_abc__=$?
        __ALB_BEG_abc__
        user output line 1
        user output line 2
        __ALB_END_abc__=0
        root@host:/ #

    We want only lines 2-3 — everything between the BEG line (which
    the shell printed via ``echo``) and the END line (ditto).

    The first line (the echoed command itself) contains BOTH markers
    in substring form; we must skip it. Using a start-of-line anchor
    on both markers distinguishes the echo from the real output lines.

    Returns the extracted stdout. If markers aren't found on their own
    line, returns ``""`` (safer than garbage).
    """
    beg_match = re.search(
        rf"(?:^|\n){re.escape(beg)}\r?\n", text,
    )
    if beg_match is None:
        return ""
    body_start = beg_match.end()

    end_match = re.search(
        rf"(?:^|\n){re.escape(end)}=\d+\s*$",
        text[body_start:], flags=re.MULTILINE,
    )
    if end_match is None:
        # End marker missing — unusual, return whatever came after BEG
        return text[body_start:].rstrip("\r\n")

    body = text[body_start : body_start + end_match.start()]
    # Strip trailing newlines leftover from the line break before END
    return body.rstrip("\r\n")


def _is_decisive(state: SerialState) -> bool:
    """True when the state is conclusive enough to stop the handshake.

    "Decisive" means either:
      - we've seen a prompt (the command can run, or we can reject
        specifically),
      - the endpoint is clearly broken (panic / corrupted),
      - the endpoint is clearly pre-shell (fastboot / login / boot).

    UNKNOWN / IDLE / CRASH are NOT decisive — we keep reading in hope
    of a clearer signal.
    """
    return state in (
        SerialState.SHELL_USER,
        SerialState.SHELL_ROOT,
        SerialState.UBOOT,
        SerialState.RECOVERY,
        SerialState.FASTBOOT,
        SerialState.LOGIN_PROMPT,
        SerialState.PANIC,
        SerialState.CORRUPTED,
        SerialState.KERNEL_BOOT,
        SerialState.LINUX_INIT,
        SerialState.SPL,
    )


def _classify_connect_error(exc: Exception) -> str:
    if isinstance(exc, FileNotFoundError):
        return "SERIAL_PORT_NOT_FOUND"
    if isinstance(exc, PermissionError):
        return "SERIAL_PERMISSION_DENIED"
    if isinstance(exc, ImportError):
        return "SYSTEM_DEPENDENCY_MISSING"
    if isinstance(exc, ConnectionError):
        return "SERIAL_PORT_NOT_FOUND"
    return "ADB_COMMAND_FAILED"
