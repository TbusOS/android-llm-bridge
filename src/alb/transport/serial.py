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
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

from alb.transport.base import ShellResult, Transport


# ─── Constants ─────────────────────────────────────────────────────
DEFAULT_PROMPTS = (
    b"$ ",    # unprivileged Android shell
    b"# ",    # root shell
    b"=> ",   # u-boot
    b"> ",    # some bootloaders
)


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
        newline: bytes = b"\n",
        read_chunk: int = 4096,
    ) -> None:
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
        self.newline = newline
        self.read_chunk = read_chunk
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
        """Prompt-based shell wrapper. Best-effort.

        Flow:
            1. Connect (ser2net/local) fresh.
            2. Drain anything pending.
            3. Send the command + newline.
            4. Read until we hit a known prompt or timeout.
            5. Strip the echoed command and return what's between.
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
            # Nudge a newline so a fresh prompt shows up — this helps when the
            # device was idle and no marker is pending.
            link.writer.write(self.newline)
            await link.writer.drain()
            try:
                await asyncio.wait_for(
                    _read_until_any(link.reader, self.prompts), timeout=3
                )
            except asyncio.TimeoutError:
                # No prompt — could be u-boot without one, or stuck. Continue
                # anyway; we'll collect what the command emits within timeout.
                pass

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

            text = raw.decode("utf-8", errors="replace")
            stdout = _strip_echo_and_prompt(text, cmd, self.prompts)
            return ShellResult(
                ok=True,
                exit_code=0,  # UART can't reliably report exit codes
                stdout=stdout,
                duration_ms=int((perf_counter() - start) * 1000),
            )
        finally:
            await self._close(link)

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
