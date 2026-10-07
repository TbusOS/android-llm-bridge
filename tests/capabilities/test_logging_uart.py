"""Tests for the capture_uart capability (serial transport only)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from alb.capabilities.logging import capture_uart, send_uart, watch_uart_panic
from alb.infra.permissions import PermissionResult
from alb.transport.base import ShellResult


async def _stream(chunks: list[bytes]) -> AsyncIterator[bytes]:
    for c in chunks:
        yield c


def _mk_serial_mock(chunks: list[bytes]) -> AsyncMock:
    """Mock serial transport. ``stream_read`` yields ``chunks`` exactly
    once; any subsequent call (e.g. after capture_uart reconnects on EOF)
    yields nothing — same as a real TCP bridge where you can't re-read
    a stream past EOF.
    """
    t = AsyncMock()
    t.name = "serial"
    t.check_permissions = AsyncMock(return_value=PermissionResult(behavior="allow"))

    consumed = {"v": False}

    def _factory(*_a, **_kw) -> AsyncIterator[bytes]:
        async def _gen() -> AsyncIterator[bytes]:
            if consumed["v"]:
                if False:  # makes _gen an async-gen with zero yields
                    yield b""
                return
            consumed["v"] = True
            for c in chunks:
                yield c

        return _gen()

    t.stream_read = _factory
    return t


@pytest.mark.asyncio
async def test_capture_uart_refuses_non_serial() -> None:
    t = AsyncMock()
    t.name = "adb"
    r = await capture_uart(t, duration=1)
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "TRANSPORT_NOT_SUPPORTED"
    assert "serial" in (r.error.suggestion or "").lower()


@pytest.mark.asyncio
async def test_capture_uart_invalid_duration() -> None:
    t = _mk_serial_mock([])
    r = await capture_uart(t, duration=0)
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "INVALID_DURATION"


@pytest.mark.asyncio
async def test_capture_uart_happy_path(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    chunks = [
        b"[    0.000000] Booting Linux on CPU 0x0\n",
        b"[    1.234567] kernel BUG: unable to handle\n",
        b"[    2.345678] panic: sync\n",
    ]
    t = _mk_serial_mock(chunks)
    r = await capture_uart(t, duration=1, device="abc")
    assert r.ok
    assert r.data is not None
    assert r.data.lines == 3
    assert r.data.errors >= 2
    assert len(r.artifacts) == 1
    # Artifact should land under devices/abc/logs/
    assert "devices/abc/logs" in str(r.artifacts[0])
    assert r.artifacts[0].exists()


@pytest.mark.asyncio
async def test_capture_uart_output_as_directory(monkeypatch, tmp_path: Path) -> None:
    """--output <dir>/ (trailing slash): auto-create dir + <ts>-uart.log inside.

    Users signal "this path is a directory" by either pre-creating it or
    adding a trailing slash. Bare `Path('/x/y')` without trailing slash is
    treated as a file path (UNIX convention).
    """
    out_dir = tmp_path / "my_logs"
    t = _mk_serial_mock([b"ABC\n"])
    r = await capture_uart(t, duration=1, output=str(out_dir) + "/")
    assert r.ok
    assert len(r.artifacts) == 1
    art = Path(r.artifacts[0])
    # Created inside our chosen directory, file name auto-generated
    assert art.parent == out_dir
    assert art.name.endswith("-uart.log")
    assert art.exists()
    assert art.read_bytes() == b"ABC\n"


@pytest.mark.asyncio
async def test_capture_uart_output_as_existing_directory(tmp_path: Path) -> None:
    """If --output points at an existing dir (no trailing slash), still treated as dir."""
    (tmp_path / "exists").mkdir()
    t = _mk_serial_mock([b"XYZ\n"])
    r = await capture_uart(t, duration=1, output=tmp_path / "exists")
    assert r.ok
    art = Path(r.artifacts[0])
    assert art.parent == tmp_path / "exists"
    assert art.name.endswith("-uart.log")


@pytest.mark.asyncio
async def test_capture_uart_output_as_file_path(tmp_path: Path) -> None:
    """--output <file.log>: log written to that exact file."""
    target = tmp_path / "subdir" / "my-run.log"  # parent doesn't exist
    t = _mk_serial_mock([b"hello world\n"])
    r = await capture_uart(t, duration=1, output=target)
    assert r.ok
    art = Path(r.artifacts[0])
    assert art == target
    assert art.exists()
    assert art.read_bytes() == b"hello world\n"
    # parent dir was created automatically
    assert target.parent.is_dir()


@pytest.mark.asyncio
async def test_capture_uart_output_trailing_slash(tmp_path: Path) -> None:
    """String path with trailing slash → always treated as directory."""
    out = str(tmp_path / "fresh_dir") + "/"
    t = _mk_serial_mock([b"ok\n"])
    r = await capture_uart(t, duration=1, output=out)
    assert r.ok
    art = Path(r.artifacts[0])
    assert art.parent == tmp_path / "fresh_dir"
    assert art.name.endswith("-uart.log")


# ─── idle-bridge bug regression (BUG_serial_capture_idle_auto_exit) ───
@pytest.mark.asyncio
async def test_capture_uart_holds_duration_when_stream_idles(monkeypatch, tmp_path: Path) -> None:
    """When the bridge immediately EOFs (idle COM port), capture_uart must
    keep reconnecting until the requested duration elapses — not return
    after ~100 ms with a 0-byte log.
    """
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))

    call_count = 0

    def _empty_stream(*_a, **_kw):
        nonlocal call_count
        call_count += 1

        async def _gen():
            if False:  # makes _gen an async-generator function that yields nothing
                yield b""

        return _gen()

    t = AsyncMock()
    t.name = "serial"
    t.check_permissions = AsyncMock(return_value=PermissionResult(behavior="allow"))
    t.stream_read = _empty_stream

    duration = 1
    r = await capture_uart(t, duration=duration)

    assert r.ok
    assert r.data is not None
    # Pre-fix the function returned in ~120 ms; require at least 80 % of
    # the requested duration so the regression is impossible to miss.
    assert r.data.duration_captured_ms >= int(duration * 1000 * 0.8), (
        f"idle stream auto-exited at {r.data.duration_captured_ms} ms "
        f"instead of holding for ~{duration * 1000} ms"
    )
    # And we must have actually reconnected — otherwise the fix is a no-op.
    assert call_count >= 2, (
        f"only attempted {call_count} reconnect(s) in {duration}s — "
        "backoff too long or reconnect loop missing"
    )


@pytest.mark.asyncio
async def test_capture_uart_catches_late_data_after_reconnect(monkeypatch, tmp_path: Path) -> None:
    """Workflow: start capture → reboot board → boot log only arrives on a
    later reconnect. Pre-fix the capture had already given up; post-fix it
    keeps reopening the stream until real bytes show up.
    """
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))

    call_count = 0
    data_delivered = {"v": False}

    def _late_data_stream(*_a, **_kw):
        nonlocal call_count
        call_count += 1
        current = call_count

        async def _gen():
            # First two opens: bridge immediately EOFs (board still idle).
            if current < 3 or data_delivered["v"]:
                if False:  # unreachable; keeps _gen an async-gen function
                    yield b""
                return
            # Third open: real bytes finally arrive (e.g. reboot started).
            # Mark delivered so a later reconnect doesn't replay the same
            # bytes — matches a real TCP bridge.
            data_delivered["v"] = True
            yield b"[boot] u-boot 2024.04\n"
            yield b"[boot] kernel start\n"

        return _gen()

    t = AsyncMock()
    t.name = "serial"
    t.check_permissions = AsyncMock(return_value=PermissionResult(behavior="allow"))
    t.stream_read = _late_data_stream

    r = await capture_uart(t, duration=2)

    assert r.ok
    assert r.data is not None
    assert r.data.lines == 2, f"expected to catch the 2 late boot lines, got {r.data.lines}"
    content = Path(r.artifacts[0]).read_bytes()
    assert b"u-boot" in content
    assert b"kernel start" in content


@pytest.mark.asyncio
async def test_capture_uart_counts_lines_not_chunks(tmp_path: Path) -> None:
    """A serial link hands over arbitrary byte chunks, not lines: one chunk
    can carry several lines, one line can straddle two chunks. The summary
    must count real lines. Pre-fix it counted chunks, so a 30870-line boot
    log reported ``lines: 5540`` — and an error keyword split across two
    chunks was never seen at all.
    """
    chunks = [
        b"[    0.100000] Booting Linux\n[    0.200000] mmc0: err",
        b"or -110 whilst initialising\n[    0.300000] ok\n[    0.400",
        b"000] no trailing newline",
    ]
    t = _mk_serial_mock(chunks)
    out = tmp_path / "split.log"
    r = await capture_uart(t, duration=1, output=out)

    assert r.ok
    assert r.data is not None
    assert r.data.lines == 4, f"expected 4 lines, got {r.data.lines}"
    assert r.data.errors == 1, f"the 'error' split across chunks must count once, got {r.data.errors}"
    assert out.read_bytes() == b"".join(chunks)


@pytest.mark.asyncio
async def test_capture_uart_flushes_each_chunk_to_disk(tmp_path: Path) -> None:
    """Every received chunk must be on disk before the next one is read.

    Otherwise up to one write buffer (8 KiB) sits in the process: a capture
    stopped with plain ``kill`` (SIGTERM) loses it, and ``tail`` on the
    artifact lags behind the board. Measured 2026-10-07: a capture killed
    with SIGTERM left a 0-byte file although a probe line had arrived.
    """
    out = tmp_path / "flush.log"
    seen_on_disk: list[bytes] = []

    def _factory(*_a, **_kw):
        async def _gen():
            yield b"first line\n"
            # Resumed only after capture_uart handled the chunk above.
            seen_on_disk.append(out.read_bytes())
            yield b"second line\n"
            seen_on_disk.append(out.read_bytes())

        return _gen()

    t = AsyncMock()
    t.name = "serial"
    t.check_permissions = AsyncMock(return_value=PermissionResult(behavior="allow"))
    t.stream_read = _factory

    r = await capture_uart(t, duration=1, output=out)

    assert r.ok
    assert seen_on_disk[:2] == [b"first line\n", b"first line\nsecond line\n"]


# ── send_uart (P3) ───────────────────────────────────────────────────


def _mk_send_mock(ok: bool = True):
    t = AsyncMock()
    t.name = "serial"
    captured: dict = {}

    async def _send_raw(data: bytes):
        captured["data"] = data
        return ShellResult(
            ok=ok,
            exit_code=0 if ok else -1,
            stdout="",
            stderr="" if ok else "link down",
            error_code=None if ok else "SERIAL_LINK_DOWN",
        )

    t.send_raw = _send_raw
    return t, captured


@pytest.mark.asyncio
async def test_send_uart_appends_newline() -> None:
    t, captured = _mk_send_mock()
    r = await send_uart(t, "printenv")
    assert r.ok
    assert captured["data"] == b"printenv\n"
    assert r.data["sent_bytes"] == len(b"printenv\n")
    assert r.data["appended_newline"] is True


@pytest.mark.asyncio
async def test_send_uart_raw_no_newline() -> None:
    t, captured = _mk_send_mock()
    r = await send_uart(t, "\x03", append_newline=False)  # Ctrl-C to stop autoboot
    assert r.ok
    assert captured["data"] == b"\x03"
    assert r.data["appended_newline"] is False


@pytest.mark.asyncio
async def test_send_uart_refuses_non_serial() -> None:
    t = AsyncMock()
    t.name = "adb"
    r = await send_uart(t, "x")
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "TRANSPORT_NOT_SUPPORTED"


@pytest.mark.asyncio
async def test_send_uart_propagates_link_failure() -> None:
    t, _ = _mk_send_mock(ok=False)
    r = await send_uart(t, "x")
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "SERIAL_LINK_DOWN"


# ── watch_uart_panic (P3) ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_watch_uart_panic_detects(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    chunks = [
        b"[    0.000000] Booting Linux\n",
        b"[    1.234567] Kernel panic - not syncing: VFS: Unable to mount root\n",
        b"[    1.234600] CPU: 0 PID: 1 Comm: swapper\n",
    ]
    t = _mk_serial_mock(chunks)
    r = await watch_uart_panic(t, duration=1, device="dev1")
    assert r.ok
    assert r.data["panic_detected"] is True
    assert "Kernel panic" in r.data["marker"]
    assert "not syncing" in r.data["tail"]
    assert len(r.artifacts) == 1


@pytest.mark.asyncio
async def test_watch_uart_panic_clean(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    t = _mk_serial_mock([b"[ 0.0] all good\n", b"buildroot login: \n"])
    r = await watch_uart_panic(t, duration=1)
    assert r.ok
    assert r.data["panic_detected"] is False
    assert r.data["marker"] is None


@pytest.mark.asyncio
async def test_watch_uart_panic_refuses_non_serial() -> None:
    t = AsyncMock()
    t.name = "adb"
    r = await watch_uart_panic(t, duration=1)
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "TRANSPORT_NOT_SUPPORTED"
