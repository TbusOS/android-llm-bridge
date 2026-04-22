"""Tests for SerialTransport — TCP (ser2net) mode using a local echo server."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager

import pytest

from alb.transport.serial import (
    SerialTransport,
    _read_until_any,
    _strip_echo_and_prompt,
)


# ─── Test fixture: a tiny fake ser2net ─────────────────────────────
@asynccontextmanager
async def _fake_ser2net(
    handler: Callable[[asyncio.StreamReader, asyncio.StreamWriter], Coroutine],
) -> AsyncIterator[tuple[str, int]]:
    """Spawn an asyncio server on an ephemeral port; yield (host, port)."""
    server = await asyncio.start_server(handler, host="127.0.0.1", port=0)
    sock = server.sockets[0].getsockname()
    host, port = sock[0], sock[1]
    try:
        yield host, port
    finally:
        server.close()
        await server.wait_closed()


# ─── Construction ──────────────────────────────────────────────────
def test_requires_either_device_or_tcp() -> None:
    with pytest.raises(ValueError):
        SerialTransport()


def test_rejects_both_device_and_tcp() -> None:
    with pytest.raises(ValueError):
        SerialTransport(device="/dev/ttyUSB0", tcp_host="localhost", tcp_port=9001)


# ─── Helpers ───────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_read_until_any_hits_first_token() -> None:
    r = asyncio.StreamReader()
    r.feed_data(b"hello $ world")
    r.feed_eof()
    out = await _read_until_any(r, (b"$ ", b"# "))
    assert out == b"hello $ "


@pytest.mark.asyncio
async def test_read_until_any_returns_all_on_eof() -> None:
    r = asyncio.StreamReader()
    r.feed_data(b"no prompt here")
    r.feed_eof()
    out = await _read_until_any(r, (b"$ ",))
    assert out == b"no prompt here"


def test_strip_echo_and_prompt_removes_echo_and_prompt() -> None:
    raw = "ls /data\nfoo\nbar\n$ "
    out = _strip_echo_and_prompt(raw, "ls /data", (b"$ ",))
    # echo 'ls /data' removed from start, '$ ' removed from end
    assert "ls /data" not in out.splitlines()[0]
    assert "foo" in out
    assert "bar" in out
    assert "$" not in out[-3:]


# ─── Health (no real endpoint) ─────────────────────────────────────
@pytest.mark.asyncio
async def test_health_reports_failure_when_endpoint_dead() -> None:
    # Port 1 is reserved, never listening.
    t = SerialTransport(tcp_host="127.0.0.1", tcp_port=1)
    info = await t.health()
    assert info["ok"] is False
    assert info["transport"] == "serial"
    assert info["mode"] == "tcp"
    assert "error" in info


# ─── Shell round-trip over TCP ─────────────────────────────────────
@pytest.mark.asyncio
async def test_shell_roundtrip_over_tcp() -> None:
    """A fake server that echoes a prompt, echoes the command, then replies."""

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        # Initial prompt when the client sends its warmup newline.
        warmup = await reader.readline()  # the '\n' from _open()/warmup
        assert warmup == b"\n"
        writer.write(b"$ ")
        await writer.drain()
        # Read the real command line.
        line = await reader.readline()
        # Echo the command like a real shell would.
        writer.write(line)  # echo
        writer.write(b"output from fake shell\n")
        writer.write(b"$ ")  # final prompt
        await writer.drain()
        writer.close()

    async with _fake_ser2net(handler) as (host, port):
        t = SerialTransport(tcp_host=host, tcp_port=port)
        r = await t.shell("echo hi", timeout=5)

    assert r.ok
    assert r.exit_code == 0
    assert "output from fake shell" in r.stdout


@pytest.mark.asyncio
async def test_shell_timeout_over_tcp() -> None:
    async def handler(reader, writer):  # noqa: ANN001
        # Read warmup + command, but never send a prompt.
        try:
            await reader.readline()
            await reader.readline()
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            pass
        finally:
            writer.close()

    async with _fake_ser2net(handler) as (host, port):
        t = SerialTransport(tcp_host=host, tcp_port=port)
        r = await t.shell("sleep 30", timeout=1)

    assert not r.ok
    assert r.error_code == "TIMEOUT_SHELL"


# ─── stream_read yields bytes until server closes ──────────────────
@pytest.mark.asyncio
async def test_stream_read_yields_until_close() -> None:
    async def handler(reader, writer):  # noqa: ANN001
        writer.write(b"[boot] step 1\n")
        writer.write(b"[boot] step 2\n")
        await writer.drain()
        writer.close()

    async with _fake_ser2net(handler) as (host, port):
        t = SerialTransport(tcp_host=host, tcp_port=port)
        out = b""
        async for chunk in t.stream_read("uart"):
            out += chunk

    assert b"[boot] step 1" in out
    assert b"[boot] step 2" in out


@pytest.mark.asyncio
async def test_stream_read_yields_marker_when_endpoint_dead() -> None:
    t = SerialTransport(tcp_host="127.0.0.1", tcp_port=1)
    collected = b""
    async for chunk in t.stream_read("uart"):
        collected += chunk
    # Should have yielded a single diagnostic marker, not raised.
    assert b"[alb serial open failed" in collected


# ─── Unsupported operations ────────────────────────────────────────
@pytest.mark.asyncio
async def test_push_unsupported() -> None:
    from pathlib import Path

    t = SerialTransport(tcp_host="127.0.0.1", tcp_port=1)
    r = await t.push(Path("/etc/hosts"), "/tmp/x")
    assert not r.ok
    assert r.error_code == "TRANSPORT_NOT_SUPPORTED"


# ─── State-based routing (Phase 1 — handshake + reject) ────────────


@pytest.mark.asyncio
async def test_shell_rejects_when_board_is_panicked() -> None:
    """Handshake sees a kernel panic → shell() fails with BOARD_PANICKED
    and includes the panic tail in stdout for diagnostics.
    """

    async def handler(reader, writer):  # noqa: ANN001
        await reader.readline()  # swallow the handshake nudge
        writer.write(
            b"[   42.112345] BUG: unable to handle page fault\n"
            b"[   42.113456] Kernel panic - not syncing: Fatal exception\n"
            b"[   42.114567] CPU: 0 PID: 1 Comm: init\n"
        )
        await writer.drain()
        await asyncio.sleep(5)
        writer.close()

    async with _fake_ser2net(handler) as (host, port):
        t = SerialTransport(
            tcp_host=host, tcp_port=port, handshake_timeout=1.0,
        )
        r = await t.shell("echo hi", timeout=3)

    assert not r.ok
    assert r.error_code == "BOARD_PANICKED"
    assert "Kernel panic" in r.stdout


@pytest.mark.asyncio
async def test_shell_rejects_when_board_is_booting() -> None:
    """Handshake sees kernel-boot markers → BOARD_BOOTING, no command sent."""

    async def handler(reader, writer):  # noqa: ANN001
        await reader.readline()
        writer.write(
            b"[    0.000000] Booting Linux on physical CPU 0x0\n"
            b"[    0.012345] Linux version 6.1\n"
            b"[    0.023456] CPU features: detected: DCP\n"
        )
        await writer.drain()
        await asyncio.sleep(5)
        writer.close()

    async with _fake_ser2net(handler) as (host, port):
        t = SerialTransport(
            tcp_host=host, tcp_port=port, handshake_timeout=1.0,
        )
        r = await t.shell("echo hi", timeout=3)

    assert not r.ok
    assert r.error_code == "BOARD_BOOTING"


@pytest.mark.asyncio
async def test_shell_rejects_when_baud_corrupted() -> None:
    """Non-printable garbage → SERIAL_BAUD_MISMATCH."""

    async def handler(reader, writer):  # noqa: ANN001
        await reader.readline()
        writer.write(bytes(range(128, 200)) * 20)  # high-byte noise
        await writer.drain()
        await asyncio.sleep(5)
        writer.close()

    async with _fake_ser2net(handler) as (host, port):
        t = SerialTransport(
            tcp_host=host, tcp_port=port, handshake_timeout=1.0,
        )
        r = await t.shell("echo hi", timeout=3)

    assert not r.ok
    assert r.error_code == "SERIAL_BAUD_MISMATCH"


@pytest.mark.asyncio
async def test_shell_rejects_when_login_prompt_waiting() -> None:
    """login: prompt → BOARD_NEEDS_LOGIN (not a generic error)."""

    async def handler(reader, writer):  # noqa: ANN001
        await reader.readline()
        writer.write(b"Debian GNU/Linux 12 host ttyS0\nlogin: ")
        await writer.drain()
        await asyncio.sleep(5)
        writer.close()

    async with _fake_ser2net(handler) as (host, port):
        t = SerialTransport(
            tcp_host=host, tcp_port=port, handshake_timeout=1.0,
        )
        r = await t.shell("uptime", timeout=3)

    assert not r.ok
    assert r.error_code == "BOARD_NEEDS_LOGIN"


@pytest.mark.asyncio
async def test_shell_proceeds_when_uboot_prompt_present() -> None:
    """U-Boot prompt → handshake passes, u-boot command runs."""

    async def handler(reader, writer):  # noqa: ANN001
        # Swallow the handshake nudge + respond with u-boot prompt
        await reader.readline()
        writer.write(b"\n=> ")
        await writer.drain()
        # Read the real command
        cmd_line = await reader.readline()
        assert b"printenv" in cmd_line
        writer.write(cmd_line)  # echo
        writer.write(b"bootargs=console=ttyS0,1500000 root=/dev/mmcblk0p7\n")
        writer.write(b"=> ")  # final prompt
        await writer.drain()
        writer.close()

    async with _fake_ser2net(handler) as (host, port):
        t = SerialTransport(
            tcp_host=host, tcp_port=port, handshake_timeout=1.0,
        )
        r = await t.shell("printenv bootargs", timeout=5)

    assert r.ok
    assert "bootargs=console=ttyS0" in r.stdout


# ─── detect_state() API ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_detect_state_reports_shell_ready() -> None:
    """When the endpoint offers a root prompt, detect_state() classifies it."""

    async def handler(reader, writer):  # noqa: ANN001
        # Wait for the handshake nudge, then respond with a root prompt
        await reader.readline()
        writer.write(b"\nroot@host:/ # ")
        await writer.drain()
        await asyncio.sleep(0.3)
        writer.close()

    async with _fake_ser2net(handler) as (host, port):
        t = SerialTransport(
            tcp_host=host, tcp_port=port, handshake_timeout=1.0,
        )
        info = await t.detect_state()

    assert info["ok"]
    assert info["connected"]
    assert info["state"] == "shell_root"
    assert "#" in info["tail"]
    assert info["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_detect_state_classifies_kernel_boot() -> None:
    async def handler(reader, writer):  # noqa: ANN001
        await reader.readline()
        writer.write(b"[    0.000000] Booting Linux on physical CPU 0x0\n")
        await writer.drain()
        await asyncio.sleep(0.5)
        writer.close()

    async with _fake_ser2net(handler) as (host, port):
        t = SerialTransport(
            tcp_host=host, tcp_port=port, handshake_timeout=1.0,
        )
        info = await t.detect_state()

    assert info["state"] == "kernel_boot"


@pytest.mark.asyncio
async def test_detect_state_reports_connect_error() -> None:
    """A closed port → info['ok'] = False with structured error."""
    t = SerialTransport(
        tcp_host="127.0.0.1", tcp_port=59, handshake_timeout=0.5,
    )
    info = await t.detect_state()
    assert info["ok"] is False
    assert info["connected"] is False
    assert info["error_code"] in {"SERIAL_PORT_NOT_FOUND", "SYSTEM_DEPENDENCY_MISSING"}


# ─── Pattern overrides from config ────────────────────────────────


@pytest.mark.asyncio
async def test_custom_patterns_make_handshake_recognize_board_prompt() -> None:
    """Custom prompt regex lets a non-standard shell be classified as SHELL_ROOT."""
    from alb.transport.serial_state import PatternSet

    async def handler(reader, writer):  # noqa: ANN001
        await reader.readline()
        writer.write(b"myboard:~# ")
        await writer.drain()
        await asyncio.sleep(0.3)
        writer.close()

    # Default shell_root pattern would still match this (ends in #)
    # so we force override to something stricter that ONLY matches
    # "myboard:~#" to prove the override path works.
    patterns = PatternSet.from_mapping(
        {"shell_root": r"myboard:~\s*#\s*$"}
    )

    async with _fake_ser2net(handler) as (host, port):
        t = SerialTransport(
            tcp_host=host, tcp_port=port,
            patterns=patterns, handshake_timeout=1.0,
        )
        info = await t.detect_state()

    assert info["state"] == "shell_root"


@pytest.mark.asyncio
async def test_shell_reboot_non_normal_unsupported() -> None:
    t = SerialTransport(tcp_host="127.0.0.1", tcp_port=1)
    r = await t.reboot("recovery")
    assert not r.ok
    assert r.error_code == "TRANSPORT_NOT_SUPPORTED"
