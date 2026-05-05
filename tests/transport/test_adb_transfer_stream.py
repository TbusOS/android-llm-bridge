"""Tests for AdbTransport.push_stream / pull_stream (MID-6).

Streams TransferEvent updates and supports cancel-via-aclose. The
underlying subprocess is mocked so we can drive deterministic stderr
progress lines and stdout summary lines without touching real adb.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from unittest.mock import patch

import pytest

from alb.transport.adb import AdbTransport
from alb.transport.base import TransferEvent


# ─── Fake subprocess that yields canned stdout/stderr lines ─────────


class _FakeStream:
    """Minimal asyncio.StreamReader stand-in.

    Supports `async for line in stream` by yielding pre-canned lines
    (each terminated with `\\n`), then EOF.
    """

    def __init__(self, lines: list[bytes]) -> None:
        self._buf = io.BytesIO(b"".join(lines))

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        line = self._buf.readline()
        if not line:
            raise StopAsyncIteration
        return line


class _FakeProc:
    """asyncio.subprocess.Process stand-in for streaming push/pull tests."""

    def __init__(
        self,
        stderr_lines: list[bytes],
        stdout_lines: list[bytes],
        returncode: int = 0,
        wait_delay: float = 0.0,
    ) -> None:
        self.stderr = _FakeStream(stderr_lines)
        self.stdout = _FakeStream(stdout_lines)
        self.returncode: int | None = None
        self._final_rc = returncode
        self._wait_delay = wait_delay
        self.terminated = False
        self.killed = False

    async def wait(self) -> int:
        if self._wait_delay > 0:
            await asyncio.sleep(self._wait_delay)
        if self.returncode is None:
            self.returncode = self._final_rc
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        if self.returncode is None:
            self.returncode = -15  # SIGTERM

    def kill(self) -> None:
        self.killed = True
        if self.returncode is None:
            self.returncode = -9  # SIGKILL


# ─── push_stream happy path ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_push_stream_yields_progress_then_done(tmp_path) -> None:
    """Standard happy path: 3 stderr progress lines + 1 stdout summary →
    3 progress events + 1 done event with bytes from summary."""
    local = tmp_path / "f.bin"
    local.write_bytes(b"x" * 1234)

    fake = _FakeProc(
        stderr_lines=[
            b"[  0%] /sdcard/f.bin\n",
            b"[ 50%] /sdcard/f.bin\n",
            b"[100%] /sdcard/f.bin\n",
        ],
        stdout_lines=[
            b"f.bin: 1 file pushed. 12.3 MB/s (1234 bytes in 0.100s)\n",
        ],
        returncode=0,
    )

    async def fake_exec(*args, **kw):
        return fake

    t = AdbTransport()
    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        events = [ev async for ev in t.push_stream(local, "/sdcard/f.bin")]

    progress = [e for e in events if e.kind == "progress"]
    done = [e for e in events if e.kind == "done"]

    assert len(progress) == 3
    assert [p.percent for p in progress] == [0.0, 50.0, 100.0]
    assert all(p.file == "/sdcard/f.bin" for p in progress)

    assert len(done) == 1
    assert done[0].ok is True
    assert done[0].bytes_transferred == 1234
    assert done[0].percent == 100.0
    assert done[0].duration_ms >= 0


@pytest.mark.asyncio
async def test_push_stream_local_missing_yields_done_error(tmp_path) -> None:
    """Local path missing → single done event ok=False, no subprocess spawned."""
    t = AdbTransport()
    spawned = {"called": False}

    async def fake_exec(*args, **kw):
        spawned["called"] = True
        return _FakeProc([], [])

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        events = [
            ev async for ev in t.push_stream(tmp_path / "nope", "/sdcard/x")
        ]

    assert spawned["called"] is False
    assert len(events) == 1
    assert events[0].kind == "done"
    assert events[0].ok is False
    assert "not found" in (events[0].error or "")


@pytest.mark.asyncio
async def test_push_stream_nonzero_exit_surfaces_error(tmp_path) -> None:
    """adb exits non-zero → done event with ok=False + error from stderr tail."""
    local = tmp_path / "f.bin"
    local.write_bytes(b"x")

    fake = _FakeProc(
        stderr_lines=[
            b"adb: error: failed to copy 'f.bin' to '/sdcard/f.bin': remote error\n",
        ],
        stdout_lines=[],
        returncode=1,
    )

    async def fake_exec(*args, **kw):
        return fake

    t = AdbTransport()
    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        events = [ev async for ev in t.push_stream(local, "/sdcard/f.bin")]

    assert events[-1].kind == "done"
    assert events[-1].ok is False
    assert "remote error" in (events[-1].error or "")


@pytest.mark.asyncio
async def test_push_stream_binary_missing_yields_error(tmp_path) -> None:
    """If adb binary is missing, FileNotFoundError on spawn → done event."""
    local = tmp_path / "f.bin"
    local.write_bytes(b"x")

    async def fake_exec(*args, **kw):
        raise FileNotFoundError("[Errno 2] adb not found")

    t = AdbTransport()
    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        events = [ev async for ev in t.push_stream(local, "/sdcard/x")]

    assert len(events) == 1
    assert events[0].kind == "done"
    assert events[0].ok is False
    assert "not found" in (events[0].error or "")


# ─── push_stream argv shape ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_push_stream_argv_includes_serial_and_socket(tmp_path) -> None:
    """Sanity: argv carries `-s <serial>` and ADB_SERVER_SOCKET env."""
    local = tmp_path / "f.bin"
    local.write_bytes(b"x")
    captured: dict = {}

    async def fake_exec(*args, **kw):
        captured["args"] = args
        captured["env"] = kw.get("env") or {}
        return _FakeProc([], [b"f.bin: 1 file pushed. (1 bytes in 0s)\n"], 0)

    t = AdbTransport(serial="SERIAL01", server_socket="tcp:localhost:5037")
    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        async for _ in t.push_stream(local, "/sdcard/f.bin"):
            pass

    argv = captured["args"]
    assert "-s" in argv and "SERIAL01" in argv
    assert "push" in argv and str(local) in argv and "/sdcard/f.bin" in argv
    assert captured["env"].get("ADB_SERVER_SOCKET") == "tcp:localhost:5037"


# ─── pull_stream + cancel ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_pull_stream_creates_local_parent_dir(tmp_path) -> None:
    """Pull creates the local parent directory before spawning adb."""
    local = tmp_path / "deep" / "nested" / "out.bin"

    async def fake_exec(*args, **kw):
        return _FakeProc(
            [b"[100%] /sdcard/x\n"],
            [b"x: 1 file pulled. (10 bytes in 0s)\n"],
            0,
        )

    t = AdbTransport()
    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        async for _ in t.pull_stream("/sdcard/x", local):
            pass

    assert local.parent.is_dir()


@pytest.mark.asyncio
async def test_push_stream_cancel_terminates_subprocess(tmp_path) -> None:
    """Consumer breaks out → finally block terminates subprocess."""
    local = tmp_path / "f.bin"
    local.write_bytes(b"x")

    # _FakeStream that yields when control returns — pauses between
    # lines so aclose() can interrupt mid-iteration. Synchronous
    # readline-based fake processes all lines in one event-loop turn,
    # which doesn't model a real adb stream's drip pattern.
    class _SlowStream:
        def __init__(self, lines):
            self._lines = list(lines)

        def __aiter__(self):
            return self

        async def __anext__(self) -> bytes:
            if not self._lines:
                raise StopAsyncIteration
            await asyncio.sleep(0.01)  # let consumer aclose between lines
            return self._lines.pop(0)

    fake = _FakeProc(
        stderr_lines=[],
        stdout_lines=[],
        returncode=0,
        wait_delay=0.5,  # short wait so cleanup test doesn't hang
    )
    fake.stderr = _SlowStream(
        [f"[ {p}%] /sdcard/big\n".encode() for p in range(0, 100, 5)]
    )
    fake.stdout = _SlowStream([])

    async def fake_exec(*args, **kw):
        return fake

    t = AdbTransport()
    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        agen = t.push_stream(local, "/sdcard/big")
        # Consume 1 progress event, then close.
        first = await agen.__anext__()
        assert first.kind == "progress"
        await agen.aclose()

    # Cancel must have terminated the fake subprocess.
    assert fake.terminated is True


# ─── Default ABC behaviour for non-adb transports ───────────────────


@pytest.mark.asyncio
async def test_base_transport_push_stream_raises_not_implemented() -> None:
    """Other transports (serial / ssh) inherit the ABC default that
    raises NotImplementedError — matches existing pattern for
    interactive_shell()."""
    from alb.transport.base import Transport, ShellResult
    from typing import Any

    class _Stub(Transport):
        async def shell(self, cmd: str, *, timeout: int = 30) -> ShellResult:
            return ShellResult(ok=True)

        async def stream_read(self, source: str, **kwargs: Any):  # noqa: ANN001
            if False:
                yield b""

        async def push(self, local: Path, remote: str) -> ShellResult:
            return ShellResult(ok=True)

        async def pull(self, remote: str, local: Path) -> ShellResult:
            return ShellResult(ok=True)

        async def reboot(self, mode: str = "normal") -> ShellResult:
            return ShellResult(ok=True)

        async def health(self) -> dict[str, Any]:
            return {"ok": True}

    s = _Stub()
    with pytest.raises(NotImplementedError):
        async for _ in s.push_stream(Path("/x"), "/y"):
            pass
    with pytest.raises(NotImplementedError):
        async for _ in s.pull_stream("/y", Path("/x")):
            pass


# ─── Parser unit tests ──────────────────────────────────────────────


def test_progress_regex_handles_padding() -> None:
    from alb.transport.adb import _ADB_PROGRESS_RE

    for line, pct, file in [
        ("[  0%] /sdcard/foo", 0, "/sdcard/foo"),
        ("[ 50%] /sdcard/foo", 50, "/sdcard/foo"),
        ("[100%] /sdcard/foo", 100, "/sdcard/foo"),
        ("[  5%] /a/b with space.bin", 5, "/a/b with space.bin"),
    ]:
        m = _ADB_PROGRESS_RE.search(line)
        assert m, f"regex failed on: {line!r}"
        assert int(m.group("pct")) == pct
        assert m.group("file") == file


def test_summary_regex_extracts_bytes() -> None:
    from alb.transport.adb import _ADB_SUMMARY_RE

    for line, expected in [
        ("f.bin: 1 file pushed. 12.3 MB/s (1234 bytes in 0.100s)", 1234),
        ("/sdcard/x: 1 file pulled. 999 KB/s (50000 bytes in 0.5 s)", 50000),
        ("foo (8 bytes in 0.001s)", 8),
    ]:
        m = _ADB_SUMMARY_RE.search(line)
        assert m, f"summary regex failed on: {line!r}"
        assert int(m.group("bytes")) == expected
