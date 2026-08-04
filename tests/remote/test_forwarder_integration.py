"""Forwarder integration tests — fully in-process, NO real device.

A fake adb server (echo) stands in for the agent-side adb server; a fake agent
implements ChannelOpener by bridging each channel to that fake server. This
exercises the real AdbForwarder: OS-level listener -> data channel -> echo and
back, plus the ADR-052 daemon "no retry on failure" rule.

The SerialForwarder half additionally covers issue #4: an OS serial port is
exclusive, so every local connection must SHARE one agent channel instead of
racing for its own.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from alb.remote.forwarder import AdbForwarder, SerialForwarder
from alb.remote.protocol import ChannelRole, ChannelType
from alb.remote.registry import ChannelOpenError, DataChannel


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


def test_err_text_names_an_empty_message_exception():
    """`str(TimeoutError())` is "" — the log line used to end in a bare colon
    and tell the operator nothing (7-06 field report)."""
    from alb.remote.forwarder import _err_text

    assert "dial back" in _err_text(TimeoutError())
    assert _err_text(ConnectionError()) == "ConnectionError"
    assert _err_text(ConnectionError("boom")) == "boom"


# ── issue #4: one exclusive COM port, many alb readers ───────────────


class _ExclusiveSerialAgent:
    """Fake agent whose COM port behaves like a real one: exactly ONE open at a
    time. A second concurrent open raises, just as Windows does with
    "Access is denied" — so any forwarder that opens per local connection fails
    this test the way the real bench did."""

    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self.open_calls = 0
        self.closed_channels = 0
        self.live = 0

    async def open_data_channel(
        self, *, ctype: Any, role: Any, params: dict[str, Any],
        timeout: float,  # noqa: ASYNC109 — name fixed by the ChannelOpener protocol
    ) -> DataChannel:
        self.open_calls += 1
        if self.live:
            raise ConnectionError(f"cannot open {params.get('com')}: Access is denied")
        self.live += 1
        reader, writer = await asyncio.open_connection(self._host, self._port)
        return _CountedChannel(reader, writer, self)


class _CountedChannel(_PipeChannel):
    def __init__(self, reader, writer, agent: _ExclusiveSerialAgent) -> None:
        super().__init__(reader, writer)
        self._agent = agent

    async def aclose(self) -> None:
        self._agent.live -= 1
        self._agent.closed_channels += 1
        await super().aclose()


async def _wait_refs(fwd: SerialForwarder, want: int, budget_s: float = 5.0) -> None:
    """Wait until `want` local connections have attached to the shared session."""
    deadline = asyncio.get_running_loop().time() + budget_s
    while fwd.session_refs != want:
        assert asyncio.get_running_loop().time() < deadline, (
            f"session_refs stuck at {fwd.session_refs}, wanted {want}"
        )
        await asyncio.sleep(0.01)


async def test_serial_readers_share_one_channel_and_all_see_the_stream():
    """The core of issue #4: `serial capture` + `serial shell` at the same time.

    Both local connections must attach to ONE agent channel, and every byte the
    board emits must reach BOTH — a UART is a broadcast medium.
    """
    server, dev_port = await _start_echo_server()
    agent = _ExclusiveSerialAgent("127.0.0.1", dev_port)
    fwd = SerialForwarder(lambda: agent, com="COM_X", baud=115200, port=0)
    await fwd.attach()
    try:
        r1, w1 = await asyncio.open_connection("127.0.0.1", fwd.port)
        r2, w2 = await asyncio.open_connection("127.0.0.1", fwd.port)
        await _wait_refs(fwd, 2)
        assert agent.open_calls == 1  # NOT one per connection

        # what reader 1 types is echoed by the board — both readers see it
        w1.write(b"boot: hello\n")
        await w1.drain()
        assert await asyncio.wait_for(r1.readexactly(12), timeout=5) == b"boot: hello\n"
        assert await asyncio.wait_for(r2.readexactly(12), timeout=5) == b"boot: hello\n"

        # and TX from the second reader reaches the board just the same
        w2.write(b"reboot\n")
        await w2.drain()
        assert await asyncio.wait_for(r1.readexactly(7), timeout=5) == b"reboot\n"
        assert await asyncio.wait_for(r2.readexactly(7), timeout=5) == b"reboot\n"

        for w in (w1, w2):
            w.close()
            with contextlib.suppress(Exception):
                await w.wait_closed()
    finally:
        await fwd.detach()
        server.close()
        with contextlib.suppress(Exception):
            await server.wait_closed()


async def test_serial_channel_survives_until_the_last_reader_leaves():
    """A short `serial shell` finishing must NOT tear the link out from under a
    long-running `serial capture` — refcount, not last-writer-wins."""
    server, dev_port = await _start_echo_server()
    agent = _ExclusiveSerialAgent("127.0.0.1", dev_port)
    fwd = SerialForwarder(lambda: agent, com="COM_X", baud=115200, port=0)
    await fwd.attach()
    try:
        r_cap, w_cap = await asyncio.open_connection("127.0.0.1", fwd.port)
        _r_sh, w_sh = await asyncio.open_connection("127.0.0.1", fwd.port)
        await _wait_refs(fwd, 2)

        # the short-lived reader leaves
        w_sh.close()
        with contextlib.suppress(Exception):
            await w_sh.wait_closed()
        await _wait_refs(fwd, 1)
        assert agent.closed_channels == 0  # channel still open for the capture

        w_cap.write(b"still here\n")
        await w_cap.drain()
        assert await asyncio.wait_for(r_cap.readexactly(11), timeout=5) == b"still here\n"

        w_cap.close()
        with contextlib.suppress(Exception):
            await w_cap.wait_closed()
        await _wait_refs(fwd, 0)
        assert agent.closed_channels == 1  # last one out closed the COM
        assert agent.open_calls == 1
    finally:
        await fwd.detach()
        server.close()
        with contextlib.suppress(Exception):
            await server.wait_closed()


async def test_serial_reopens_after_everyone_left():
    """Refcount back to zero must not wedge the forwarder — the next command
    opens a fresh channel."""
    server, dev_port = await _start_echo_server()
    agent = _ExclusiveSerialAgent("127.0.0.1", dev_port)
    fwd = SerialForwarder(lambda: agent, com="COM_X", baud=115200, port=0)
    await fwd.attach()
    try:
        for _ in range(2):
            reader, writer = await asyncio.open_connection("127.0.0.1", fwd.port)
            await _wait_refs(fwd, 1)
            writer.write(b"ping")
            await writer.drain()
            assert await asyncio.wait_for(reader.readexactly(4), timeout=5) == b"ping"
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            await _wait_refs(fwd, 0)
        assert agent.open_calls == 2
        assert agent.closed_channels == 2
    finally:
        await fwd.detach()
        server.close()
        with contextlib.suppress(Exception):
            await server.wait_closed()


async def test_serial_session_end_closes_every_local_connection():
    """When the agent's channel dies (agent restart / COM unplugged) every
    attached reader must see EOF, not hang forever on a dead link."""
    server, dev_port = await _start_echo_server()
    agent = _ExclusiveSerialAgent("127.0.0.1", dev_port)
    fwd = SerialForwarder(lambda: agent, com="COM_X", baud=115200, port=0)
    await fwd.attach()
    try:
        r1, w1 = await asyncio.open_connection("127.0.0.1", fwd.port)
        r2, w2 = await asyncio.open_connection("127.0.0.1", fwd.port)
        await _wait_refs(fwd, 2)

        # the board side goes away
        server.close()
        with contextlib.suppress(Exception):
            await server.wait_closed()
        await fwd._session._channel.aclose()  # type: ignore[union-attr]

        assert await asyncio.wait_for(r1.read(), timeout=5) == b""
        assert await asyncio.wait_for(r2.read(), timeout=5) == b""
        for w in (w1, w2):
            w.close()
            with contextlib.suppress(Exception):
                await w.wait_closed()
    finally:
        await fwd.detach()


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


async def test_serial_open_retries_while_the_agent_releases_the_port():
    """ADR-052 GATEWAY bounded retry: a COM handle that is still being released
    must not surface as a hard failure to the next command."""
    server, dev_port = await _start_echo_server()
    agent = _ExclusiveSerialAgent("127.0.0.1", dev_port)
    refuse = {"n": 1}
    real_open = agent.open_data_channel

    async def flaky(**kw):
        if refuse["n"]:
            refuse["n"] -= 1
            agent.open_calls += 1
            raise ChannelOpenError("cannot open COM_X: Access is denied")
        return await real_open(**kw)

    agent.open_data_channel = flaky  # type: ignore[method-assign]
    fwd = SerialForwarder(lambda: agent, com="COM_X", baud=115200, port=0)
    await fwd.attach()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", fwd.port)
        writer.write(b"ping")
        await writer.drain()
        assert await asyncio.wait_for(reader.readexactly(4), timeout=5) == b"ping"
        assert agent.open_calls == 2  # one refusal, then the retry succeeded
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
    finally:
        await fwd.detach()
        server.close()
        with contextlib.suppress(Exception):
            await server.wait_closed()


async def test_serial_open_gives_up_when_another_program_owns_the_port():
    """A port genuinely held by something else must still fail — bounded, not
    endless, and with the agent's own message preserved."""
    agent = _ExclusiveSerialAgent("127.0.0.1", 1)

    async def always_refuse(**_kw):
        agent.open_calls += 1
        raise ChannelOpenError("cannot open COM_X: Access is denied")

    agent.open_data_channel = always_refuse  # type: ignore[method-assign]
    fwd = SerialForwarder(lambda: agent, com="COM_X", baud=115200, port=0)
    await fwd.attach()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", fwd.port)
        assert await asyncio.wait_for(reader.read(), timeout=5) == b""
        assert agent.open_calls == 3  # 1 + len(_SERIAL_OPEN_BACKOFF_S)
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
    finally:
        await fwd.detach()
