"""Interactive shell session — PTY-backed bidirectional byte streamer.

Used by:
  - Web UI Terminal panel via WS /terminal/ws
  - Future `alb shell` interactive mode (M3)

Each transport that supports interactive shells (adb, serial, ssh)
returns an InteractiveShell instance from its `interactive_shell()`
context manager. The instance presents one minimal interface:
  - write(bytes)
  - read()  → bytes
  - resize(rows, cols)
  - close()

Reading from a PTY master fd inside asyncio: we use loop.add_reader()
to feed an asyncio.Queue so consumers can `await shell.read()` without
blocking on os.read directly.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import os
import struct
import termios
from typing import Protocol


class InteractiveShell:
    """Bidirectional async byte streamer over a PTY master fd."""

    def __init__(
        self,
        *,
        master_fd: int,
        proc: asyncio.subprocess.Process | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self._master_fd = master_fd
        self._proc = proc
        self._loop = loop or asyncio.get_event_loop()
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=1024)
        self._closed = False
        self._reader_attached = False
        self._attach_reader()

    # ── Reading ──────────────────────────────────────────────────────

    def _attach_reader(self) -> None:
        if self._reader_attached:
            return
        try:
            self._loop.add_reader(self._master_fd, self._on_readable)
            self._reader_attached = True
        except OSError:
            # fd may already be closed / unsupported on platform — let
            # read() return empty bytes which signals EOF.
            self._closed = True

    def _on_readable(self) -> None:
        try:
            chunk = os.read(self._master_fd, 4096)
        except OSError:
            chunk = b""
        if not chunk:
            self._mark_eof()
            return
        if self._queue.full():
            # Drop the oldest chunk so the loop never blocks on a slow
            # consumer; terminals are interactive so a stale frame is
            # better than a stalled session.
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()
        self._queue.put_nowait(chunk)

    def _mark_eof(self) -> None:
        self._detach_reader()
        # Sentinel: empty bytes signals EOF to readers.
        with contextlib.suppress(asyncio.QueueFull):
            self._queue.put_nowait(b"")

    def _detach_reader(self) -> None:
        if self._reader_attached:
            with contextlib.suppress(Exception):
                self._loop.remove_reader(self._master_fd)
            self._reader_attached = False

    async def read(self) -> bytes:
        """Wait for at least one chunk of bytes from the shell stdout.

        Returns b"" when the session ends. Subsequent calls keep
        returning b"" so callers can treat it as a clean EOF signal.
        """
        if self._closed and self._queue.empty():
            return b""
        return await self._queue.get()

    # ── Writing ──────────────────────────────────────────────────────

    async def write(self, data: bytes) -> None:
        """Forward bytes to the shell stdin. No echo handling — the PTY
        does that for us based on its termios setup."""
        if self._closed:
            return
        # os.write may EAGAIN under load; chunk + retry to keep the
        # session responsive even on small kernel buffers.
        view = memoryview(data)
        while view:
            try:
                n = await self._loop.run_in_executor(
                    None, os.write, self._master_fd, bytes(view)
                )
            except OSError:
                self._closed = True
                return
            view = view[n:]

    # ── PTY size ─────────────────────────────────────────────────────

    async def resize(self, rows: int, cols: int) -> None:
        """Send TIOCSWINSZ to the master so the child shell sees the
        right `$LINES` / `$COLUMNS`."""
        if self._closed:
            return
        rows = max(1, min(500, int(rows)))
        cols = max(1, min(1000, int(cols)))
        # struct: rows, cols, xpixel, ypixel — last two unused
        size = struct.pack("HHHH", rows, cols, 0, 0)
        try:
            fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ, size)
        except OSError:
            # Some tty emulations don't support TIOCSWINSZ — ignore
            # gracefully so the session keeps working.
            pass

    # ── Lifecycle ────────────────────────────────────────────────────

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def returncode(self) -> int | None:
        return self._proc.returncode if self._proc else None

    async def close(self, *, term_grace_s: float = 2.0) -> None:
        """Stop the session: detach reader, close fd, terminate child."""
        if self._closed:
            return
        self._closed = True
        self._detach_reader()
        with contextlib.suppress(OSError):
            os.close(self._master_fd)
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=term_grace_s)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    self._proc.kill()
                with contextlib.suppress(Exception):
                    await self._proc.wait()


# ─── Helper: spawn a child attached to a fresh PTY ──────────────────


async def open_pty_subprocess(
    *args: str,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    rows: int = 24,
    cols: int = 80,
) -> InteractiveShell:
    """Fork `args` with stdin/stdout/stderr wired to a new PTY.

    Returns an InteractiveShell that owns the master fd. Caller is
    responsible for `await shell.close()` (use the
    Transport.interactive_shell() async context manager which does
    this automatically).
    """
    import pty  # local import — Linux/macOS only

    master_fd, slave_fd = pty.openpty()
    # Set initial size before we exec so the child sees the right env.
    size = struct.pack("HHHH", rows, cols, 0, 0)
    with contextlib.suppress(OSError):
        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, size)

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            env=env,
            cwd=cwd,
            start_new_session=True,
        )
    finally:
        # Parent has the master; child has its own copy of the slave
        # via fork. Always close our slave handle.
        os.close(slave_fd)

    return InteractiveShell(master_fd=master_fd, proc=proc)


# ─── Protocol for type hints ────────────────────────────────────────


class SupportsInteractiveShell(Protocol):
    """Protocol — anything that can hand out an InteractiveShell."""

    async def interactive_shell(
        self,
        *,
        rows: int = 24,
        cols: int = 80,
    ) -> InteractiveShell:
        ...
