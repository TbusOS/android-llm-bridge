"""Tests for the InteractiveShell PTY wrapper.

Spawns real subprocesses against a fresh PTY (cat / sh) — Linux/macOS
required, so we skip on Windows. The point is to exercise the actual
asyncio fd-reader path; mocking it would defeat the purpose.
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from alb.transport.interactive import InteractiveShell, open_pty_subprocess


pytestmark = pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="PTY tests need Unix",
)


@pytest.mark.asyncio
async def test_pty_echo_roundtrip() -> None:
    # `cat` echoes whatever we write back (PTY does line-discipline echo too,
    # so the output we get back is "hi\r\nhi\r\n" — own input + cat's output).
    shell = await open_pty_subprocess("cat")
    try:
        await shell.write(b"hi\n")
        out = await asyncio.wait_for(_read_until(shell, b"hi"), timeout=2.0)
        assert b"hi" in out
    finally:
        await shell.close()


@pytest.mark.asyncio
async def test_pty_close_marks_eof() -> None:
    shell = await open_pty_subprocess("cat")
    await shell.close()
    assert shell.closed is True
    # After close, read returns b"" (EOF sentinel).
    out = await asyncio.wait_for(shell.read(), timeout=1.0)
    assert out == b""


@pytest.mark.asyncio
async def test_pty_resize_no_crash() -> None:
    shell = await open_pty_subprocess("cat")
    try:
        await shell.resize(40, 120)
        await shell.resize(0, 0)        # clamped to (1, 1)
        await shell.resize(1000, 5000)  # clamped to (500, 1000)
    finally:
        await shell.close()


@pytest.mark.asyncio
async def test_pty_write_after_close_is_noop() -> None:
    shell = await open_pty_subprocess("cat")
    await shell.close()
    # Should not raise — closed shells silently drop writes.
    await shell.write(b"ignored")


@pytest.mark.asyncio
async def test_pty_child_exit_signals_eof() -> None:
    # `true` exits immediately. Wait for the child to fully reap before
    # reading — the PTY master may keep the queue empty briefly while
    # the kernel drains the slave side.
    shell = await open_pty_subprocess("/bin/sh", "-c", "exit 0")
    try:
        if shell._proc:  # type: ignore[attr-defined]
            await asyncio.wait_for(shell._proc.wait(), timeout=2.0)  # type: ignore[attr-defined]
        # After the child exits, eventually we should see EOF (b"").
        for _ in range(20):
            try:
                chunk = await asyncio.wait_for(shell.read(), timeout=0.2)
            except asyncio.TimeoutError:
                continue
            if not chunk:
                return
        pytest.fail("did not see EOF after child exited")
    finally:
        await shell.close()


@pytest.mark.asyncio
async def test_pty_returncode_after_exit() -> None:
    shell = await open_pty_subprocess("/bin/sh", "-c", "exit 7")
    try:
        # Drain any output and let child exit
        for _ in range(10):
            try:
                chunk = await asyncio.wait_for(shell.read(), timeout=0.3)
                if not chunk:
                    break
            except asyncio.TimeoutError:
                break
        # Give the process a moment to actually reap
        if shell._proc:  # type: ignore[attr-defined]
            await asyncio.wait_for(shell._proc.wait(), timeout=1.0)  # type: ignore[attr-defined]
        assert shell.returncode == 7
    finally:
        await shell.close()


@pytest.mark.asyncio
async def test_pty_concurrent_write_and_read() -> None:
    # PTY input buffer is bounded by the kernel — a producer that
    # only writes blocks once the buffer fills. The shell.write() chunk
    # loop should keep the session unstuck as long as a reader drains.
    shell = await open_pty_subprocess("cat")
    target_xs = 4096
    seen_buf = bytearray()

    async def reader() -> None:
        while seen_buf.count(b"x") < target_xs:
            try:
                chunk = await asyncio.wait_for(shell.read(), timeout=2.0)
            except asyncio.TimeoutError:
                return
            if not chunk:
                return
            seen_buf.extend(chunk)

    async def writer() -> None:
        await shell.write(b"x" * target_xs + b"\n")

    try:
        await asyncio.gather(writer(), reader())
        assert seen_buf.count(b"x") >= target_xs
    finally:
        await shell.close()


# ─── Internal helper ──────────────────────────────────────────────


async def _read_until(shell: InteractiveShell, needle: bytes) -> bytes:
    buf = b""
    while needle not in buf:
        chunk = await shell.read()
        if not chunk:
            break
        buf += chunk
    return buf
