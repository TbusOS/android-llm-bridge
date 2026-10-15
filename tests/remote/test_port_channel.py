"""The local listener (alb.remote.port_channel): client -> listener -> adb -> device port.

Everything in-process against the fake adb server from
tests/transport/test_adb_socket.py; nothing real is contacted.

What these lock:
- a full round trip through the listener (client bytes echoed by the "device")
- a device refusal closes that client and the listener serves the next one
- half-close: a client that shuts its write side still gets the reply
- non-loopback bind refused without the flag, before any adb contact
- local port choice: the device's port number if free, else ephemeral;
  an explicit busy port is an error, not a silent move
- two devices refuse the listener before it binds
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterator

import pytest

from alb.remote.port_channel import (
    BindRefused,
    LocalPortBusy,
    PortChannel,
    check_bind,
    is_loopback,
)
from alb.transport.adb_socket import AdbServerUnreachable, AmbiguousDevice, TcpEndpoint
from tests.transport.test_adb_socket import FakeAdbServer, free_port

DEVICE_PORT = 5039


@pytest.fixture
async def fake() -> Iterator[FakeAdbServer]:
    srv = FakeAdbServer(ports={DEVICE_PORT: "echo"})
    await srv.start()
    yield srv
    await srv.stop()


class _Channel:
    """A started PortChannel with its events captured; closed on exit."""

    def __init__(self, fake: FakeAdbServer, **kw: object) -> None:
        self.events: list[str] = []
        self._changed = asyncio.Event()
        kw.setdefault("device_port", DEVICE_PORT)
        kw.setdefault("local_port", 0)  # ephemeral unless a test says otherwise
        self.channel = PortChannel(
            endpoint=fake.endpoint,
            on_event=self._record,
            **kw,  # type: ignore[arg-type]
        )

    def _record(self, line: str) -> None:
        self.events.append(line)
        self._changed.set()

    async def __aenter__(self) -> _Channel:
        await self.channel.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.channel.close()

    async def connect(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        return await asyncio.open_connection("127.0.0.1", self.channel.bound_port)

    async def wait_events(self, n: int, timeout_s: float = 2.0) -> list[str]:
        async with asyncio.timeout(timeout_s):
            while len(self.events) < n:
                self._changed.clear()
                await self._changed.wait()
        return self.events


async def _close(writer: asyncio.StreamWriter) -> None:
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()


# ── round trip ───────────────────────────────────────────────────────────


async def test_round_trip_client_to_listener_to_adb_to_echo(fake):
    async with _Channel(fake) as c:
        reader, writer = await c.connect()
        writer.write(b"ping from gdb")
        await writer.drain()
        assert await reader.readexactly(13) == b"ping from gdb"
        await _close(writer)
        events = await c.wait_events(2)
    assert events[0].startswith("[1] open 127.0.0.1:") and events[0].endswith("-> tcp:5039")
    assert events[1] == "[1] closed · 13 B to device · 13 B from device"
    assert fake.requests == ["host:devices", "host:transport-any", "tcp:5039"]
    assert (c.channel.connections, c.channel.refused) == (1, 0)
    assert (c.channel.bytes_to_device, c.channel.bytes_from_device) == (13, 13)


async def test_each_client_gets_its_own_device_stream(fake):
    """Two clients at once, two adb connections — gdb and an HTTP client
    both assume one TCP connection is one server-side socket."""
    async with _Channel(fake) as c:

        async def talk(payload: bytes) -> bytes:
            reader, writer = await c.connect()
            writer.write(payload)
            await writer.drain()
            got = await reader.readexactly(len(payload))
            await _close(writer)
            return got

        a, b = await asyncio.gather(talk(b"A" * 1000), talk(b"B" * 2000))
        assert (a, b) == (b"A" * 1000, b"B" * 2000)
        await c.wait_events(4)
    assert fake.port_connections == 2
    assert fake.requests.count("tcp:5039") == 2


async def test_large_transfer_moves_in_both_directions(fake):
    blob = bytes(range(256)) * 2048  # 512 KiB, several CHUNKs
    async with _Channel(fake) as c:
        reader, writer = await c.connect()

        async def send() -> None:
            writer.write(blob)
            await writer.drain()
            writer.write_eof()

        got, _ = await asyncio.gather(reader.read(-1), send())
        assert got == blob
        await _close(writer)
        events = await c.wait_events(2)
    assert events[1] == f"[1] closed · {len(blob)} B to device · {len(blob)} B from device"


# ── device refusal ───────────────────────────────────────────────────────


async def test_device_refusal_closes_the_client_and_the_listener_lives_on(fake):
    async with _Channel(fake, device_port=5040) as c:  # nothing listens on 5040
        reader, writer = await c.connect()
        assert await reader.read() == b"", "client must see a clean close, not a hang"
        await _close(writer)
        events = await c.wait_events(2)
        assert events[1] == "[1] refused by device: closed"
        assert c.channel.refused == 1

        fake.ports[5040] = "echo"  # the device started its server
        reader, writer = await c.connect()
        writer.write(b"now")
        await writer.drain()
        assert await reader.readexactly(3) == b"now"
        await _close(writer)
        events = await c.wait_events(4)
    assert events[3] == "[2] closed · 3 B to device · 3 B from device"


async def test_adb_server_gone_mid_run_closes_the_client_and_the_listener_lives_on(fake):
    async with _Channel(fake) as c:
        port = fake.port
        await fake.stop()
        reader, writer = await c.connect()
        assert await reader.read() == b""
        await _close(writer)
        events = await c.wait_events(2)
        assert events[1].startswith("[1] adb error: no adb server at tcp:127.0.0.1:")

        await fake.start(port)  # the hub came back on the same port
        reader, writer = await c.connect()
        writer.write(b"back")
        await writer.drain()
        assert await reader.readexactly(4) == b"back"
        await _close(writer)
        await c.wait_events(4)


# ── half-close ───────────────────────────────────────────────────────────


async def test_client_half_close_still_receives_the_reply(fake):
    """The fake answers only after it sees EOF from the client: the bytes
    arrive only if (a) the client's shutdown was forwarded to the device
    side as EOF and (b) the device -> client direction stayed open."""
    fake.ports[DEVICE_PORT] = "echo-after-eof"
    async with _Channel(fake) as c:
        reader, writer = await c.connect()
        writer.write(b"request body")
        await writer.drain()
        writer.write_eof()
        assert await reader.read() == b"request body"
        await _close(writer)
        events = await c.wait_events(2)
    assert events[1] == "[1] closed · 12 B to device · 12 B from device"


async def test_device_close_reaches_a_client_that_never_wrote(fake):
    fake.ports[DEVICE_PORT] = b"banner from device\n"
    async with _Channel(fake) as c:
        reader, writer = await c.connect()
        assert await reader.read() == b"banner from device\n"
        await _close(writer)
        events = await c.wait_events(2)
    assert events[1] == "[1] closed · 0 B to device · 19 B from device"


# ── bind policy ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "[::1]", "127.5.5.5"])
def test_loopback_hosts(host):
    assert is_loopback(host)
    check_bind(host, allow_remote=False)


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "10.1.2.3", "192.168.1.1", "example.invalid"])
def test_non_loopback_hosts_need_the_flag(host):
    assert not is_loopback(host)
    with pytest.raises(BindRefused) as ei:
        check_bind(host, allow_remote=False)
    assert "--allow-remote" in str(ei.value)
    check_bind(host, allow_remote=True)  # the explicit choice is honoured


async def test_non_loopback_bind_is_refused_before_touching_adb(fake):
    ch = PortChannel(endpoint=fake.endpoint, device_port=DEVICE_PORT, bind="0.0.0.0")
    with pytest.raises(BindRefused):
        await ch.start()
    assert fake.requests == [], "refused locally; the adb server was never asked"
    with pytest.raises(RuntimeError):
        _ = ch.bound_port


# ── local port choice ────────────────────────────────────────────────────


async def test_default_local_port_is_the_device_port_when_free(fake):
    port = free_port()
    async with _Channel(fake, device_port=port, local_port=None) as c:
        assert c.channel.bound_port == port
        assert not c.channel.moved_from_device_port


async def test_default_local_port_moves_when_the_device_port_is_taken(fake):
    taken = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    port = taken.sockets[0].getsockname()[1]
    try:
        async with _Channel(fake, device_port=port, local_port=None) as c:
            assert c.channel.bound_port != port
            assert c.channel.moved_from_device_port
    finally:
        taken.close()
        await taken.wait_closed()


async def test_explicit_busy_local_port_is_an_error_not_a_silent_move(fake):
    taken = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    port = taken.sockets[0].getsockname()[1]
    try:
        ch = PortChannel(endpoint=fake.endpoint, device_port=DEVICE_PORT, local_port=port)
        with pytest.raises(LocalPortBusy) as ei:
            await ch.start()
        assert f"127.0.0.1:{port}" in str(ei.value)
    finally:
        taken.close()
        await taken.wait_closed()


# ── device selection at start ────────────────────────────────────────────


async def test_two_devices_refuse_the_listener_before_it_binds():
    srv = FakeAdbServer(devices=(("SERIAL1", "device"), ("SERIAL2", "device")))
    await srv.start()
    try:
        ch = PortChannel(endpoint=srv.endpoint, device_port=DEVICE_PORT, local_port=0)
        with pytest.raises(AmbiguousDevice):
            await ch.start()
        with pytest.raises(RuntimeError):
            _ = ch.bound_port
        assert srv.requests == ["host:devices"]
    finally:
        await srv.stop()


async def test_serial_selects_among_two_devices():
    srv = FakeAdbServer(
        devices=(("SERIAL1", "device"), ("SERIAL2", "device")), ports={DEVICE_PORT: "echo"}
    )
    await srv.start()
    try:
        async with _Channel(srv, serial="SERIAL2") as c:
            assert c.channel.target is not None and c.channel.target.serial == "SERIAL2"
            reader, writer = await c.connect()
            writer.write(b"x")
            await writer.drain()
            assert await reader.readexactly(1) == b"x"
            await _close(writer)
            await c.wait_events(2)
        assert srv.requests == ["host:transport:SERIAL2", "tcp:5039"]
    finally:
        await srv.stop()


async def test_no_adb_server_refuses_the_listener():
    ch = PortChannel(endpoint=TcpEndpoint("127.0.0.1", free_port()), device_port=DEVICE_PORT)
    with pytest.raises(AdbServerUnreachable):
        await ch.start()


# ── shutdown ─────────────────────────────────────────────────────────────


async def test_close_drops_a_connection_in_flight(fake):
    c = _Channel(fake)
    await c.channel.start()
    reader, writer = await c.connect()
    writer.write(b"idle")
    await writer.drain()
    assert await reader.readexactly(4) == b"idle"
    await c.channel.close()
    assert await reader.read() == b"", "an open client must be dropped on close"
    await _close(writer)
    with pytest.raises(OSError):
        await asyncio.open_connection("127.0.0.1", c.channel.bound_port)
