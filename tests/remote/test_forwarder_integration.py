"""AdbForwarder integration tests — fully in-process, NO real device.

A fake adb server (echo) stands in for the Windows adb server; a fake agent
implements ChannelOpener by bridging each channel to that fake server. This
exercises the real AdbForwarder: OS-level listener -> data channel -> echo and
back, plus the ADR-052 daemon "no retry on failure" rule.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from alb.remote.forwarder import AdbForwarder, SerialForwarder
from alb.remote.protocol import ChannelRole, ChannelType
from alb.remote.registry import DataChannel


class _PipeChannel:
    """DataChannel backed by a TCP connection to the fake adb server."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer

    async def recv(self) -> bytes:
        return await self._reader.read(65536)

    async def send(self, data: bytes) -> None:
        self._writer.write(data)
        await self._writer.drain()

    async def aclose(self) -> None:
        with contextlib.suppress(Exception):
            self._writer.close()
            await self._writer.wait_closed()


class _FakeAgent:
    """Implements ChannelOpener; bridges each channel to the fake adb server."""

    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self.open_calls = 0
        self.fail = False
        self.last_ctype: Any = None
        self.last_role: Any = None
        self.last_params: dict[str, Any] | None = None

    async def open_data_channel(
        self, *, ctype: Any, role: Any, params: dict[str, Any], timeout: float
    ) -> DataChannel:
        self.open_calls += 1
        self.last_ctype = ctype
        self.last_role = role
        self.last_params = params
        if self.fail:
            raise ConnectionResetError("simulated reset")
        reader, writer = await asyncio.open_connection(self._host, self._port)
        return _PipeChannel(reader, writer)


async def _start_echo_server() -> tuple[asyncio.AbstractServer, int]:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    return
                writer.write(data)
                await writer.drain()
        finally:
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, port


async def test_forwarder_shuttles_bytes_round_trip():
    server, adb_port = await _start_echo_server()
    agent = _FakeAgent("127.0.0.1", adb_port)
    fwd = AdbForwarder(lambda: agent, port=0)
    await fwd.attach()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", fwd.port)
        writer.write(b"host:version")
        await writer.drain()
        got = await asyncio.wait_for(reader.readexactly(len(b"host:version")), timeout=5)
        assert got == b"host:version"
        assert agent.open_calls == 1
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
    finally:
        await fwd.detach()
        server.close()
        with contextlib.suppress(Exception):
            await server.wait_closed()


async def test_adb_channel_no_retry_on_open_failure():
    """ADR-052: the adb (daemon) channel must NOT retry. One failed attempt
    closes the local connection — open_data_channel is called exactly once."""
    agent = _FakeAgent("127.0.0.1", 1)
    agent.fail = True
    fwd = AdbForwarder(lambda: agent, port=0)
    await fwd.attach()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", fwd.port)
        data = await asyncio.wait_for(reader.read(), timeout=5)
        assert data == b""  # forwarder closed it after the single failed attempt
        assert agent.open_calls == 1  # NO retry
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
    finally:
        await fwd.detach()


async def test_detach_closes_listener_no_eaddrinuse():
    """Teardown must free the port so a re-attach on the same port works
    (checklist #4 — no listener leak)."""
    server, adb_port = await _start_echo_server()
    agent = _FakeAgent("127.0.0.1", adb_port)
    fwd = AdbForwarder(lambda: agent, port=0)
    await fwd.attach()
    bound = fwd.port
    await fwd.detach()
    # re-attach on the SAME port must succeed (port was actually released)
    fwd2 = AdbForwarder(lambda: agent, port=bound)
    await fwd2.attach()
    try:
        assert fwd2.port == bound
    finally:
        await fwd2.detach()
        server.close()
        with contextlib.suppress(Exception):
            await server.wait_closed()


async def test_attach_is_idempotent():
    agent = _FakeAgent("127.0.0.1", 1)
    fwd = AdbForwarder(lambda: agent, port=0)
    await fwd.attach()
    port = fwd.port
    await fwd.attach()  # second attach is a no-op, same listener
    try:
        assert fwd.port == port
    finally:
        await fwd.detach()


async def test_no_agent_connected_fails_fast():
    """Listener up but no agent → local connection closed immediately, no error,
    and open_data_channel is never reached."""
    calls = {"n": 0}

    def _no_agent():
        calls["n"] += 1
        return None

    fwd = AdbForwarder(_no_agent, port=0)
    await fwd.attach()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", fwd.port)
        data = await asyncio.wait_for(reader.read(), timeout=5)
        assert data == b""  # closed without an agent
        assert calls["n"] == 1  # resolved once, found none
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
    finally:
        await fwd.detach()


async def test_adb_forwarder_opens_daemon_tcp_channel():
    """The adb forwarder asks for a TCP/DAEMON channel to 127.0.0.1:5037."""
    server, adb_port = await _start_echo_server()
    agent = _FakeAgent("127.0.0.1", adb_port)
    fwd = AdbForwarder(lambda: agent, port=0)
    await fwd.attach()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", fwd.port)
        writer.write(b"x")
        await writer.drain()
        await asyncio.wait_for(reader.readexactly(1), timeout=5)
        assert agent.last_ctype is ChannelType.TCP
        assert agent.last_role is ChannelRole.DAEMON
        assert agent.last_params == {"target": "127.0.0.1:5037"}
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
    finally:
        await fwd.detach()
        server.close()
        with contextlib.suppress(Exception):
            await server.wait_closed()


def test_serial_configured_and_forwarder_reads_env(monkeypatch):
    from alb.remote import forwarder as fwd_mod

    monkeypatch.delenv("ALB_AGENT_SERIAL_COM", raising=False)
    assert fwd_mod.serial_configured() is False

    monkeypatch.setenv("ALB_AGENT_SERIAL_COM", "COM9")
    monkeypatch.setenv("ALB_AGENT_SERIAL_BAUD", "921600")
    monkeypatch.setenv("ALB_SERIAL_FORWARD_PORT", "0")
    assert fwd_mod.serial_configured() is True
    f = fwd_mod.get_serial_forwarder()
    assert f._params == {"com": "COM9", "baud": 921600}


async def test_serial_forwarder_passes_com_baud_and_round_trips():
    """The serial forwarder asks for a SERIAL/GATEWAY channel carrying the
    configured COM + baud, and shuttles bytes (P1)."""
    server, dev_port = await _start_echo_server()
    agent = _FakeAgent("127.0.0.1", dev_port)
    fwd = SerialForwarder(lambda: agent, com="COM_X", baud=1500000, port=0)
    await fwd.attach()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", fwd.port)
        writer.write(b"u-boot=> ")
        await writer.drain()
        got = await asyncio.wait_for(reader.readexactly(len(b"u-boot=> ")), timeout=5)
        assert got == b"u-boot=> "
        assert agent.last_ctype is ChannelType.SERIAL
        assert agent.last_role is ChannelRole.GATEWAY
        assert agent.last_params == {"com": "COM_X", "baud": 1500000}
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
    finally:
        await fwd.detach()
        server.close()
        with contextlib.suppress(Exception):
            await server.wait_closed()
