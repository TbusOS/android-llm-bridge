"""Tests for SerialTransport — TCP (ser2net) mode using a local echo server."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager

import pytest

from alb.transport.serial import (
    SerialTransport,
    _extract_between_markers,
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


# ─── Shell round-trip over TCP (simulates a real POSIX shell) ──────
@pytest.mark.asyncio
async def test_shell_roundtrip_over_tcp() -> None:
    """POSIX-shell state path: marker wrapper + real exit code extraction.

    The fake server pretends to be a real ``sh``: it echoes the
    wrapped command line, then prints the BEG marker, the user
    output, and the END marker with ``=0`` (because ``echo``
    succeeded).
    """
    import re as _re

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        warmup = await reader.readline()
        assert warmup == b"\n"
        writer.write(b"$ ")                  # triggers SHELL_USER state
        await writer.drain()

        line = await reader.readline()       # wrapped command
        m = _re.search(rb"__ALB_BEG_([a-f0-9]+)__", line)
        assert m, f"expected a BEG marker in {line!r}"
        nonce = m.group(1)
        beg = b"__ALB_BEG_" + nonce + b"__"
        end = b"__ALB_END_" + nonce + b"__"

        writer.write(line)                   # echo the command line
        writer.write(beg + b"\n")            # echo BEG sentinel
        writer.write(b"output from fake shell\n")
        writer.write(end + b"=0\n")          # echo END=0 sentinel
        writer.write(b"$ ")
        await writer.drain()
        writer.close()

    async with _fake_ser2net(handler) as (host, port):
        t = SerialTransport(tcp_host=host, tcp_port=port, handshake_timeout=1.0)
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


# ─── Marker extraction helper ──────────────────────────────────────


def test_extract_between_markers_strips_echo_and_end_line() -> None:
    """The echoed command line (which contains both markers as substrings)
    must be skipped; only the lines between marker OUTPUT lines count.
    """
    raw = (
        "echo __ALB_BEG_abc__; ls /; echo __ALB_END_abc__=$?\n"   # echoed cmd
        "__ALB_BEG_abc__\n"                                       # BEG echo output
        "bin\n"
        "etc\n"
        "usr\n"
        "__ALB_END_abc__=0\n"                                     # END output
        "root@h:/ # "                                             # next prompt
    )
    out = _extract_between_markers(raw, "__ALB_BEG_abc__", "__ALB_END_abc__")
    assert out == "bin\netc\nusr"


def test_extract_between_markers_missing_end() -> None:
    """When END marker is missing, return everything after BEG."""
    raw = "__ALB_BEG_x__\nstuff\nmore stuff\n"
    out = _extract_between_markers(raw, "__ALB_BEG_x__", "__ALB_END_x__")
    assert out == "stuff\nmore stuff"


def test_extract_between_markers_no_beg_returns_empty() -> None:
    raw = "no markers at all\njust text\n"
    out = _extract_between_markers(raw, "__ALB_BEG_x__", "__ALB_END_x__")
    assert out == ""


def test_extract_between_markers_handles_crlf() -> None:
    raw = (
        "__ALB_BEG_zz__\r\n"
        "windows line endings\r\n"
        "__ALB_END_zz__=7\r\n"
    )
    out = _extract_between_markers(raw, "__ALB_BEG_zz__", "__ALB_END_zz__")
    assert out == "windows line endings"


# ─── Marker-based command execution ────────────────────────────────


async def _fake_posix_shell_handler(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    body: bytes = b"",
    exit_code: int = 0,
    injection: bytes = b"",
) -> None:
    """Fake shell server that honours BEG/END marker protocol.

    - body:       bytes returned as the user command's stdout
    - exit_code:  emitted as ``=N`` after the END marker
    - injection:  printk-style bytes emitted BEFORE the BEG line
                  (simulates kernel messages interleaving with command
                  output — real boards do this constantly)
    """
    import re as _re

    await reader.readline()  # handshake nudge
    writer.write(b"$ ")
    await writer.drain()

    wrapped = await reader.readline()
    m = _re.search(rb"__ALB_BEG_([a-f0-9]+)__", wrapped)
    nonce = m.group(1) if m else b"xxx"
    beg = b"__ALB_BEG_" + nonce + b"__"
    end = b"__ALB_END_" + nonce + b"__"

    writer.write(wrapped)                                 # echo
    if injection:
        writer.write(injection)
    writer.write(beg + b"\n")
    writer.write(body)
    writer.write(end + b"=" + str(exit_code).encode() + b"\n")
    writer.write(b"$ ")
    await writer.drain()
    writer.close()


@pytest.mark.asyncio
async def test_shell_returns_real_exit_code_on_success() -> None:
    async def handler(r, w):  # noqa: ANN001
        await _fake_posix_shell_handler(r, w, body=b"hello\n", exit_code=0)

    async with _fake_ser2net(handler) as (host, port):
        t = SerialTransport(tcp_host=host, tcp_port=port, handshake_timeout=1.0)
        r = await t.shell("true", timeout=5)

    assert r.ok
    assert r.exit_code == 0
    assert r.stdout == "hello"


@pytest.mark.asyncio
async def test_shell_returns_real_exit_code_on_failure() -> None:
    """Non-zero exit → ok=False AND the specific exit code is surfaced."""
    async def handler(r, w):  # noqa: ANN001
        await _fake_posix_shell_handler(r, w, body=b"not found\n", exit_code=127)

    async with _fake_ser2net(handler) as (host, port):
        t = SerialTransport(tcp_host=host, tcp_port=port, handshake_timeout=1.0)
        r = await t.shell("nonexistent_cmd", timeout=5)

    assert not r.ok
    assert r.exit_code == 127
    assert "not found" in r.stdout


@pytest.mark.asyncio
async def test_shell_printk_injection_does_not_break_output_extraction() -> None:
    """Simulated kernel printk lines interleaving should not confuse
    marker extraction — the extracted stdout comes only from between
    BEG and END markers, not before BEG.
    """
    async def handler(r, w):  # noqa: ANN001
        await _fake_posix_shell_handler(
            r, w,
            body=b"my real output\n",
            exit_code=0,
            injection=b"[   42.123456] wlan0: link up\n",
        )

    async with _fake_ser2net(handler) as (host, port):
        t = SerialTransport(tcp_host=host, tcp_port=port, handshake_timeout=1.0)
        r = await t.shell("ls", timeout=5)

    assert r.ok
    # The printk line must not be in the extracted stdout
    assert "wlan0" not in r.stdout
    assert r.stdout == "my real output"


@pytest.mark.asyncio
async def test_shell_multiline_command_roundtrips_cleanly() -> None:
    """Commands with semicolons / multi-line output work end-to-end."""
    async def handler(r, w):  # noqa: ANN001
        await _fake_posix_shell_handler(
            r, w,
            body=b"line-1\nline-2\nline-3\n",
            exit_code=0,
        )

    async with _fake_ser2net(handler) as (host, port):
        t = SerialTransport(tcp_host=host, tcp_port=port, handshake_timeout=1.0)
        r = await t.shell("for i in 1 2 3; do echo line-$i; done", timeout=5)

    assert r.ok
    assert r.stdout == "line-1\nline-2\nline-3"


@pytest.mark.asyncio
async def test_shell_uses_fresh_nonce_per_call() -> None:
    """Two back-to-back calls must use independent marker nonces."""
    nonces: list[bytes] = []

    async def handler(r, w):  # noqa: ANN001
        import re as _re
        await r.readline()
        w.write(b"$ ")
        await w.drain()
        wrapped = await r.readline()
        m = _re.search(rb"__ALB_BEG_([a-f0-9]+)__", wrapped)
        nonce = m.group(1) if m else b"xxx"
        nonces.append(nonce)
        beg = b"__ALB_BEG_" + nonce + b"__"
        end = b"__ALB_END_" + nonce + b"__"
        w.write(wrapped)
        w.write(beg + b"\nok\n" + end + b"=0\n")
        w.write(b"$ ")
        await w.drain()
        w.close()

    async with _fake_ser2net(handler) as (host, port):
        t = SerialTransport(tcp_host=host, tcp_port=port, handshake_timeout=1.0)
        await t.shell("echo 1", timeout=5)
        await t.shell("echo 2", timeout=5)

    assert len(nonces) == 2
    assert nonces[0] != nonces[1]


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
