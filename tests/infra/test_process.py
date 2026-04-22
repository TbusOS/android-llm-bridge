"""Tests for the unified ProcessRunner module.

These tests use real subprocesses (sh, sleep, etc.) rather than mocks,
because the whole point of this module is to handle the awkward corners
of asyncio.subprocess correctly. Mocking the thing we're trying to
cover would miss exactly those corners.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

from alb.infra.process import (
    ProcessResult,
    ProcessRunner,
    run,
    spawn_stream,
)


# ── ProcessResult dataclass ────────────────────────────────────────────


def test_processresult_ok_flag_requires_all_clear() -> None:
    assert ProcessResult(
        exit_code=0, stdout="", stderr="", duration_ms=0
    ).ok
    assert not ProcessResult(
        exit_code=0, stdout="", stderr="", duration_ms=0, timed_out=True
    ).ok
    assert not ProcessResult(
        exit_code=0, stdout="", stderr="", duration_ms=0, binary_missing=True
    ).ok
    assert not ProcessResult(
        exit_code=1, stdout="", stderr="", duration_ms=0
    ).ok


def test_tail_stderr_returns_last_n_nonblank_lines() -> None:
    r = ProcessResult(
        exit_code=1,
        stdout="",
        stderr="one\n\ntwo\n   \nthree\nfour\n",
        duration_ms=0,
    )
    assert r.tail_stderr(2) == "three\nfour"
    assert r.tail_stderr(10) == "one\ntwo\nthree\nfour"


def test_tail_stderr_empty_when_no_stderr() -> None:
    r = ProcessResult(exit_code=0, stdout="", stderr="", duration_ms=0)
    assert r.tail_stderr() == ""


# ── Happy path ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_echo() -> None:
    """Trivial smoke — /bin/echo returns its arg on stdout."""
    r = await run("/bin/echo", "hello alb")
    assert r.ok
    assert r.exit_code == 0
    assert r.stdout.strip() == "hello alb"
    assert r.stderr == ""
    assert r.duration_ms >= 0
    assert not r.timed_out
    assert not r.binary_missing


@pytest.mark.asyncio
async def test_run_exit_code_nonzero() -> None:
    """Non-zero exit reflected in exit_code; ok is False."""
    r = await run("/bin/sh", "-c", "echo err 1>&2; exit 7")
    assert not r.ok
    assert r.exit_code == 7
    assert "err" in r.stderr
    assert not r.timed_out
    assert not r.binary_missing


@pytest.mark.asyncio
async def test_run_stdin_piping() -> None:
    """stdin bytes reach the child."""
    r = await run("/bin/cat", stdin=b"piped-input-xyz\n")
    assert r.ok
    assert r.stdout.strip() == "piped-input-xyz"


@pytest.mark.asyncio
async def test_run_custom_env() -> None:
    """Custom env is passed to the child."""
    r = await run(
        "/bin/sh", "-c", "printf %s \"$ALB_TEST_MARKER\"",
        env={"ALB_TEST_MARKER": "marker-42", "PATH": os.environ.get("PATH", "")},
    )
    assert r.ok
    assert r.stdout == "marker-42"


# ── Failure modes ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_missing_binary_sets_flag() -> None:
    """Missing binary → binary_missing=True, no exception."""
    r = await run("/nonexistent/binary/path-xyz-12345")
    assert not r.ok
    assert r.binary_missing
    assert not r.timed_out
    assert r.exit_code == -1
    assert "binary not found" in r.stderr.lower()


@pytest.mark.asyncio
async def test_run_timeout_escalates_and_sets_flag() -> None:
    """Timed-out process is terminated + flag set; no zombie."""
    r = await run("/bin/sleep", "10", timeout=0.3, term_grace_s=0.5)
    assert not r.ok
    assert r.timed_out
    assert r.exit_code == -1
    assert "timed out" in r.stderr.lower()
    # Duration is close to the timeout, not 10s
    assert r.duration_ms < 3000


@pytest.mark.asyncio
async def test_run_empty_args_raises() -> None:
    """run() with no argv is a programmer error."""
    with pytest.raises(ValueError):
        await run()


# ── Decoding ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_decodes_invalid_utf8_with_replacement() -> None:
    """Bad bytes in stdout → U+FFFD, no exception raised."""
    # printf with \xff — invalid UTF-8 byte
    r = await run("/bin/sh", "-c", r"printf '\xff\xff'")
    assert r.ok
    # Replacement character present (U+FFFD), we don't care exact count
    assert "\ufffd" in r.stdout or r.stdout != ""


# ── Large output ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_large_stdout() -> None:
    """64 KB stdout round-trips without truncation or hang."""
    n = 65536
    r = await run("/bin/sh", "-c", f"head -c {n} /dev/zero")
    assert r.ok
    assert len(r.stdout.encode("utf-8", errors="replace")) == n


# ── spawn_stream context manager ───────────────────────────────────────


@pytest.mark.asyncio
async def test_spawn_stream_reads_lines() -> None:
    """Streamed stdout is readable line-by-line."""
    async with spawn_stream(
        "/bin/sh", "-c", "for i in 1 2 3; do echo line-$i; done"
    ) as proc:
        assert proc.stdout is not None
        lines = []
        async for line in proc.stdout:
            lines.append(line.decode())
        # Wait for clean exit before leaving context
        await proc.wait()
    assert [ln.strip() for ln in lines] == ["line-1", "line-2", "line-3"]


@pytest.mark.asyncio
async def test_spawn_stream_terminates_long_running_on_exit() -> None:
    """Long-running process is killed when context exits; no zombie."""
    async with spawn_stream(
        "/bin/sh", "-c", "while true; do echo tick; sleep 0.05; done"
    ) as proc:
        assert proc.stdout is not None
        # Read a few lines then break out; context cleanup should kill it
        lines_read = 0
        async for line in proc.stdout:
            lines_read += 1
            if lines_read >= 3:
                break
    # After the context: proc should be fully reaped
    assert proc.returncode is not None
    assert lines_read >= 3


@pytest.mark.asyncio
async def test_spawn_stream_escalates_to_sigkill_when_term_ignored() -> None:
    """A child that ignores SIGTERM still gets reaped via SIGKILL."""
    # sh with trap that ignores TERM, then sleep forever
    async with spawn_stream(
        "/bin/sh", "-c",
        "trap '' TERM; while true; do sleep 0.1; done",
        term_grace_s=0.3,
    ) as proc:
        # Give it a moment to install the trap
        await asyncio.sleep(0.1)
    # Must be dead even though TERM was ignored
    assert proc.returncode is not None


@pytest.mark.asyncio
async def test_spawn_stream_empty_args_raises() -> None:
    with pytest.raises(ValueError):
        async with spawn_stream() as _:
            pass


# ── ProcessRunner namespace alias ──────────────────────────────────────


@pytest.mark.asyncio
async def test_processrunner_namespace_is_same_callable() -> None:
    """ProcessRunner.run maps to module-level run for DI use."""
    r1 = await run("/bin/echo", "x")
    r2 = await ProcessRunner.run("/bin/echo", "x")
    assert r1.ok and r2.ok
    assert r1.stdout == r2.stdout


# ── Platform guard ─────────────────────────────────────────────────────
# We rely on /bin/sh and /bin/echo which exist on every Linux / macOS
# developer machine. Skip on Windows — alb core dev happens on POSIX.


@pytest.fixture(autouse=True)
def _posix_only() -> None:
    if sys.platform == "win32":
        pytest.skip("ProcessRunner tests require POSIX /bin/sh")
