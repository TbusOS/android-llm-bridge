"""Unified subprocess management for external binaries (adb / ssh / rsync / …).

Before this module existed, each transport had its own inline handling of
`asyncio.create_subprocess_exec`, timeout escalation, stderr decoding, and
binary-missing detection. The duplication was a real problem — zombie
leaks on serial-side timeouts, inconsistent "binary not found" reporting,
different term-grace windows between adb and ssh.

This module centralises all of that. Transports call `run()` for
run-and-collect jobs and `spawn_stream()` for long-running reads. They map
the returned :class:`ProcessResult` into their own transport-specific
:class:`alb.infra.errors.ErrorInfo` code; this module stays transport
agnostic.

Design notes
------------
* :func:`run` **never** raises for the three common failure modes
  (missing binary / timeout / non-zero exit). Instead the result has
  ``binary_missing`` or ``timed_out`` set, with a populated ``stderr``.
  This lets the caller produce a structured :class:`ShellResult` without
  scattering try/except.
* Timeout escalation is uniform: ``SIGTERM`` → ``term_grace_s`` wait →
  ``SIGKILL`` → final ``await proc.wait()`` so we never leak zombies.
* Decoding is always ``utf-8`` with ``errors="replace"`` — we never raise
  on bad bytes from a device that garbled its console. The replacement
  char is visible in the result and the caller can decide.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from time import perf_counter


@dataclass(slots=True)
class ProcessResult:
    """Structured outcome of a :func:`run` invocation.

    ``ok`` is a convenience: True only when the process exited cleanly
    with code 0 AND did not time out AND the binary was found. Callers
    that care about the difference can inspect the flags directly.
    """

    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False
    binary_missing: bool = False

    @property
    def ok(self) -> bool:
        return (
            not self.timed_out
            and not self.binary_missing
            and self.exit_code == 0
        )

    def tail_stderr(self, n: int = 10) -> str:
        """Last *n* non-blank stderr lines.

        Intended for feeding ``ErrorInfo.suggestion`` — when a command
        fails, the tail of stderr is usually what the LLM (or human)
        needs to see to diagnose.
        """
        lines = [line for line in self.stderr.splitlines() if line.strip()]
        if not lines:
            return ""
        return "\n".join(lines[-n:])


async def run(
    *args: str,
    timeout: float = 30.0,
    stdin: bytes | None = None,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    term_grace_s: float = 2.0,
) -> ProcessResult:
    """Run an external binary, wait, and return a :class:`ProcessResult`.

    Parameters
    ----------
    *args
        argv of the child process. First element is the binary name.
    timeout
        Seconds before the child is terminated. On timeout the result
        has ``timed_out=True`` and ``exit_code=-1``.
    stdin
        If given, bytes piped into the child's stdin.
    env
        If given, full environment for the child. ``None`` inherits
        the parent's environment (same as create_subprocess_exec).
    cwd
        Working directory for the child.
    term_grace_s
        After SIGTERM, wait up to this many seconds before SIGKILL.

    Returns
    -------
    ProcessResult
        Never raises for missing binary / timeout / non-zero exit —
        those are reflected in the result's flags.
    """
    if not args:
        raise ValueError("run() requires at least a binary name")

    start = perf_counter()

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE if stdin is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=cwd,
        )
    except (FileNotFoundError, NotADirectoryError) as e:
        return ProcessResult(
            exit_code=-1,
            stdout="",
            stderr=f"binary not found or not executable: {args[0]} ({e})",
            duration_ms=int((perf_counter() - start) * 1000),
            binary_missing=True,
        )

    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(input=stdin),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        await _escalate_terminate(proc, term_grace_s=term_grace_s)
        return ProcessResult(
            exit_code=-1,
            stdout="",
            stderr=f"process timed out after {timeout}s",
            duration_ms=int((perf_counter() - start) * 1000),
            timed_out=True,
        )

    duration_ms = int((perf_counter() - start) * 1000)
    return ProcessResult(
        exit_code=proc.returncode or 0,
        stdout=_decode(stdout_b),
        stderr=_decode(stderr_b),
        duration_ms=duration_ms,
    )


@asynccontextmanager
async def spawn_stream(
    *args: str,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    stderr_devnull: bool = True,
    term_grace_s: float = 2.0,
) -> AsyncIterator[asyncio.subprocess.Process]:
    """Spawn a long-running subprocess for streamed stdout reads.

    Use as an async context manager — the child is guaranteed to be
    reaped on exit (SIGTERM → grace → SIGKILL → wait). This replaces
    the hand-written try/finally pattern that previously lived in
    every transport that streams (adb logcat / dmesg / kmsg).

    Example
    -------
    >>> async with spawn_stream("adb", "logcat", "-v", "threadtime") as proc:
    ...     assert proc.stdout is not None
    ...     async for line in proc.stdout:
    ...         yield line

    Parameters
    ----------
    stderr_devnull
        If True (default), the child's stderr is discarded. Long-running
        readers almost always want this — interleaved stderr noise hurts
        line-oriented parsing. Set False if you need to read stderr too.
    """
    if not args:
        raise ValueError("spawn_stream() requires at least a binary name")

    proc: asyncio.subprocess.Process | None = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=(
                asyncio.subprocess.DEVNULL
                if stderr_devnull
                else asyncio.subprocess.PIPE
            ),
            env=env,
            cwd=cwd,
        )
        yield proc
    finally:
        if proc is not None and proc.returncode is None:
            await _escalate_terminate(proc, term_grace_s=term_grace_s)


# ── Internals ─────────────────────────────────────────────────────────


async def _escalate_terminate(
    proc: asyncio.subprocess.Process,
    *,
    term_grace_s: float,
) -> None:
    """SIGTERM → wait ``term_grace_s`` → SIGKILL.

    Always ends with ``await proc.wait()`` so the child is fully reaped
    and we never leak zombies. Swallows :class:`ProcessLookupError` —
    the child may have already exited in a race with our signal.
    """
    try:
        proc.terminate()
    except ProcessLookupError:
        return

    try:
        await asyncio.wait_for(proc.wait(), timeout=term_grace_s)
        return
    except asyncio.TimeoutError:
        pass

    try:
        proc.kill()
    except ProcessLookupError:
        return
    await proc.wait()


def _decode(data: bytes | None) -> str:
    """Decode bytes as UTF-8 with replacement — never raises."""
    if not data:
        return ""
    return data.decode("utf-8", errors="replace")


# ── Namespace alias ───────────────────────────────────────────────────
# Some callers prefer an explicit attribute-style import for DI / mock
# replacement in tests. Both spellings map to the same implementation.
class ProcessRunner:
    """Static namespace — ``ProcessRunner.run(...)`` equivalent to
    ``process.run(...)``.

    Convenient for tests that want to monkey-patch ``.run`` on a
    per-test basis without modifying the module globally.
    """

    run = staticmethod(run)
    spawn_stream = staticmethod(spawn_stream)
