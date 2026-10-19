"""adb smart-socket client (alb.transport.adb_socket), against a fake server.

The fake speaks the real wire format and checks it: a request whose 4-hex
length prefix does not match its payload is answered FAIL, not tolerated. It
also stands in for the device: ``tcp:<port>`` is accepted only for ports it
was told to serve, and refused with a message otherwise — the "nothing
listening there" case that `alb port` must surface instead of crashing on.

`FakeAdbServer` / `FakeAdbThread` are shared with tests/remote/test_port_channel.py
and tests/cli/test_port_cli.py. No real device or network is ever contacted:
everything binds 127.0.0.1:0.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import threading
from collections.abc import Iterator

import pytest

from alb.transport import adb_socket as m
from alb.transport.adb_socket import (
    AdbEndpointUnsupported,
    AdbProtocolError,
    AdbRefused,
    AdbServerUnreachable,
    AmbiguousDevice,
    DeviceTarget,
    TcpEndpoint,
    encode_request,
    list_devices,
    open_device_stream,
    parse_devices,
    parse_endpoint,
    resolve_target,
)

# ── the fake ─────────────────────────────────────────────────────────────

PortMode = str | bytes  # "echo" | "echo-late" | "drip" | "flood" | bytes to send then close
FLOOD_BYTES = 24 * 1024 * 1024


def _fail(message: str) -> bytes:
    raw = message.encode()
    return b"FAIL" + f"{len(raw):04x}".encode() + raw


def _msg(text: str) -> bytes:
    raw = text.encode()
    return f"{len(raw):04x}".encode() + raw


class FakeAdbServer:
    """Just enough of the adb server + device for these tests.

    ``devices``: (serial, state) pairs for ``host:devices``.
    ``ports``: device port -> mode. A port not in it is refused with
    ``refuse_message`` (adb's wording when nothing listens is "closed").
    ``fail_bare``: answer FAIL and hang up with no length — some servers do.
    """

    def __init__(
        self,
        *,
        devices: tuple[tuple[str, str], ...] = (("SERIAL1", "device"),),
        ports: dict[int, PortMode] | None = None,
        refuse_message: str = "closed",
    ) -> None:
        self.devices = list(devices)
        self.ports: dict[int, PortMode] = dict(ports or {})
        self.refuse_message = refuse_message
        self.fail_bare = False
        self.requests: list[str] = []  # every payload, in arrival order
        self.frames: list[bytes] = []  # the raw bytes of each request
        self.bad_frames = 0
        self.port_connections = 0  # how many tcp:<port> streams were opened
        self.port = 0
        self._server: asyncio.Server | None = None

    @property
    def endpoint(self) -> TcpEndpoint:
        return TcpEndpoint("127.0.0.1", self.port)

    async def start(self, port: int = 0) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", port)
        self.port = int(self._server.sockets[0].getsockname()[1])

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()
            self._server = None

    async def _read_request(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> str | None:
        try:
            head = await reader.readexactly(4)
        except asyncio.IncompleteReadError:
            return None
        try:
            length = int(head.decode("ascii"), 16)
        except ValueError:
            self.bad_frames += 1
            writer.write(_fail("bad length prefix"))
            await writer.drain()
            return None
        try:
            payload = await reader.readexactly(length)
        except asyncio.IncompleteReadError:
            self.bad_frames += 1
            return None
        self.frames.append(head + payload)
        self.requests.append(payload.decode())
        return payload.decode()

    async def _refuse(self, writer: asyncio.StreamWriter, message: str) -> None:
        writer.write(b"FAIL" if self.fail_bare else _fail(message))
        await writer.drain()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            req = await self._read_request(reader, writer)
            if req is None:
                return
            if req == "host:devices":
                body = "".join(f"{s}\t{st}\n" for s, st in self.devices)
                writer.write(b"OKAY" + _msg(body))
                await writer.drain()
                return
            if not req.startswith("host:transport"):
                await self._refuse(writer, f"unknown host service: {req}")
                return
            serials = [s for s, _ in self.devices]
            if req == "host:transport-any":
                if len(self.devices) > 1:
                    await self._refuse(writer, "more than one device/emulator")
                    return
                if not self.devices:
                    await self._refuse(writer, "no devices/emulators found")
                    return
            else:
                serial = req.split(":", 2)[2]
                if serial not in serials:
                    await self._refuse(writer, f"device '{serial}' not found")
                    return
            writer.write(b"OKAY")
            await writer.drain()
            # Routed to the device now: the next request is a device service.
            svc = await self._read_request(reader, writer)
            if svc is None:
                return
            if not svc.startswith("tcp:"):
                await self._refuse(writer, f"unknown service: {svc}")
                return
            mode = self.ports.get(int(svc[4:]))
            if mode is None:
                await self._refuse(writer, self.refuse_message)
                return
            writer.write(b"OKAY")
            await writer.drain()
            self.port_connections += 1
            await self._serve_port(mode, reader, writer)
        except (OSError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    @staticmethod
    async def _serve_port(
        mode: PortMode, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        if isinstance(mode, bytes):
            writer.write(mode)
            await writer.drain()
            return
        if mode == "echo":
            while True:
                data = await reader.read(65536)
                if not data:
                    return
                writer.write(data)
                await writer.drain()
        if mode == "echo-late":
            # The device answers 100 ms after the request. The stream is the
            # adb server's, so an EOF on it tears the whole stream down at
            # once, whatever the device still had to say (adb has no
            # half-close: packages/modules/adb/sockets.cpp closes the socket
            # on EOF). A client EOF passed on to adb loses the answer, as it
            # did on the bench.
            data = await reader.read(65536)
            if not data:
                return
            try:
                more = await asyncio.wait_for(reader.read(65536), timeout=0.1)
            except TimeoutError:
                more = None
            if more == b"":
                return
            writer.write(data + (more or b""))
            await writer.drain()
            while True:
                data = await reader.read(65536)
                if not data:
                    return
                writer.write(data)
                await writer.drain()
        if mode == "drip":
            # After the request, five bytes 0.2 s apart — longer in total than
            # a short grace, never silent for as long as one.
            if not await reader.read(65536):
                return
            for _ in range(5):
                await asyncio.sleep(0.2)
                writer.write(b".")
                await writer.drain()
            while await reader.read(65536):
                pass
            return
        if mode == "flood":
            # After the request, FLOOD_BYTES at once — more than loopback
            # buffers hold, so a client that does not read stalls the writer.
            if not await reader.read(65536):
                return
            writer.write(b"f" * FLOOD_BYTES)
            await writer.drain()
            while await reader.read(65536):
                pass
            return
        raise AssertionError(f"unknown port mode {mode!r}")


class FakeAdbThread:
    """The fake on its own thread + loop, for CLI tests that call asyncio.run()."""

    def __init__(self, **kw: object) -> None:
        self.fake = FakeAdbServer(**kw)  # type: ignore[arg-type]
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> FakeAdbServer:
        ready = threading.Event()

        async def main() -> None:
            await self.fake.start()
            self._loop = asyncio.get_running_loop()
            self._stop = asyncio.Event()
            ready.set()
            await self._stop.wait()
            await self.fake.stop()

        self._thread = threading.Thread(target=lambda: asyncio.run(main()), daemon=True)
        self._thread.start()
        assert ready.wait(5), "fake adb server did not start"
        return self.fake

    def stop(self) -> None:
        if self._loop is not None and self._stop is not None:
            self._loop.call_soon_threadsafe(self._stop.set)
        if self._thread is not None:
            self._thread.join(5)


def free_port() -> int:
    """A port nothing listens on right now (bound and released)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
async def fake() -> Iterator[FakeAdbServer]:
    srv = FakeAdbServer(ports={5039: "echo"})
    await srv.start()
    yield srv
    await srv.stop()


# ── framing ──────────────────────────────────────────────────────────────


def test_request_is_four_hex_digits_then_payload():
    assert encode_request("host:version") == b"000chost:version"
    assert encode_request("") == b"0000"
    assert encode_request("tcp:5039") == b"0008tcp:5039"


def test_request_longer_than_the_prefix_can_say_is_refused():
    with pytest.raises(ValueError):
        encode_request("x" * 65536)


async def test_fake_sees_exact_frames(fake):
    await list_devices(fake.endpoint)
    assert fake.frames == [b"000chost:devices"]
    assert fake.bad_frames == 0


async def test_fake_rejects_a_malformed_length_prefix(fake):
    """Self-check of the fake: every other test here leans on it reading
    requests by their prefix, so a fake that shrugged at a bad one would
    prove nothing. (A *wrong but valid* prefix cannot be detected by any
    length-prefixed protocol — it shows up as the next request being
    garbage, which is what the reverse check on encode_request relies on.)"""
    reader, writer = await asyncio.open_connection("127.0.0.1", fake.port)
    writer.write(b"zzzzhost:devices")
    await writer.drain()
    reply = await reader.read(64)
    writer.close()
    assert reply.startswith(b"FAIL"), reply
    assert fake.bad_frames == 1
    assert fake.requests == []


# ── endpoint parsing ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "spec, expected",
    [
        ("tcp:127.0.0.1:15037", ("127.0.0.1", 15037)),
        ("tcp:localhost:5037", ("localhost", 5037)),
        ("tcp:5037", ("127.0.0.1", 5037)),
        ("tcp:[::1]:5037", ("::1", 5037)),
        (None, ("127.0.0.1", 5037)),
        ("", ("127.0.0.1", 5037)),
    ],
)
def test_tcp_endpoints_parse(spec, expected):
    ep = parse_endpoint(spec)
    assert (ep.host, ep.port) == expected


@pytest.mark.parametrize(
    "spec",
    ["unix:/tmp/adb.sock", "tcp:", "tcp:localhost:abc", "tcp:localhost:70000", "localhost:5037"],
)
def test_non_tcp_or_malformed_endpoints_are_refused_not_guessed(spec):
    with pytest.raises(AdbEndpointUnsupported):
        parse_endpoint(spec)


# ── replies ──────────────────────────────────────────────────────────────


async def test_fail_message_is_parsed(fake):
    target = DeviceTarget("SERIAL1")
    with pytest.raises(AdbRefused) as ei:
        await open_device_stream(fake.endpoint, target, 5040)
    assert ei.value.request == "tcp:5040"
    assert ei.value.message == "closed"
    assert "closed" in str(ei.value)


async def test_fail_with_empty_message_is_still_a_refusal(fake):
    fake.refuse_message = ""
    with pytest.raises(AdbRefused) as ei:
        await open_device_stream(fake.endpoint, DeviceTarget("SERIAL1"), 5040)
    assert ei.value.message == ""
    assert "(no message)" in str(ei.value)


async def test_fail_followed_by_hangup_is_still_a_refusal(fake):
    fake.fail_bare = True
    with pytest.raises(AdbRefused) as ei:
        await open_device_stream(fake.endpoint, DeviceTarget("SERIAL1"), 5040)
    assert ei.value.message == ""


async def test_a_server_that_does_not_speak_adb_is_a_protocol_error():
    async def http(reader, writer):
        await reader.read(64)
        writer.write(b"HTTP/1.0 400 Bad Request\r\n\r\n")
        await writer.drain()
        writer.close()

    srv = await asyncio.start_server(http, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    try:
        with pytest.raises(AdbProtocolError) as ei:
            await list_devices(TcpEndpoint("127.0.0.1", port))
        assert "HTTP" in str(ei.value)
    finally:
        srv.close()
        await srv.wait_closed()


async def test_hangup_mid_reply_is_a_protocol_error():
    async def half(reader, writer):
        await reader.read(64)
        writer.write(b"OKAY0010SERIAL")  # promises 16 bytes, sends 6
        await writer.drain()
        writer.close()

    srv = await asyncio.start_server(half, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    try:
        with pytest.raises(AdbProtocolError) as ei:
            await list_devices(TcpEndpoint("127.0.0.1", port))
        assert "mid-message" in str(ei.value)
    finally:
        srv.close()
        await srv.wait_closed()


async def test_nothing_listening_at_the_endpoint():
    with pytest.raises(AdbServerUnreachable) as ei:
        await list_devices(TcpEndpoint("127.0.0.1", free_port()))
    assert "no adb server at tcp:127.0.0.1:" in str(ei.value)


async def test_handshake_timeout_is_a_protocol_error_not_a_hang():
    async def mute(reader, writer):
        await reader.read(64)
        await asyncio.sleep(5)
        writer.close()

    srv = await asyncio.start_server(mute, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    try:
        with pytest.raises(AdbProtocolError) as ei:
            await list_devices(TcpEndpoint("127.0.0.1", port), timeout_s=0.1)
        assert "did not answer" in str(ei.value)
    finally:
        srv.close()
        await srv.wait_closed()


# ── device selection ─────────────────────────────────────────────────────


def test_parse_devices_counts_every_state():
    text = "SERIAL1\tdevice\nSERIAL2\toffline\n\nSERIAL3\tunauthorized\n"
    devs = parse_devices(text)
    assert [(d.serial, d.state) for d in devs] == [
        ("SERIAL1", "device"),
        ("SERIAL2", "offline"),
        ("SERIAL3", "unauthorized"),
    ]
    assert parse_devices("") == ()


async def test_without_serial_the_list_is_consulted_then_transport_any(fake):
    target = await resolve_target(fake.endpoint, None)
    stream = await open_device_stream(fake.endpoint, target, 5039)
    await stream.close()
    assert fake.requests == ["host:devices", "host:transport-any", "tcp:5039"]
    assert target.serial is None
    assert "SERIAL1" in target.described


async def test_with_serial_nothing_is_listed_and_transport_names_it(fake):
    target = await resolve_target(fake.endpoint, "SERIAL1")
    stream = await open_device_stream(fake.endpoint, target, 5039)
    await stream.close()
    assert fake.requests == ["host:transport:SERIAL1", "tcp:5039"]


async def test_two_devices_refuse_before_any_transport_request():
    srv = FakeAdbServer(devices=(("SERIAL1", "device"), ("SERIAL2", "offline")))
    await srv.start()
    try:
        with pytest.raises(AmbiguousDevice) as ei:
            await resolve_target(srv.endpoint, None)
        assert [d.serial for d in ei.value.devices] == ["SERIAL1", "SERIAL2"]
        assert "SERIAL1 (device)" in str(ei.value) and "SERIAL2 (offline)" in str(ei.value)
        assert srv.requests == ["host:devices"], "must not have tried transport-any"
    finally:
        await srv.stop()


async def test_two_devices_with_a_serial_is_not_ambiguous():
    srv = FakeAdbServer(
        devices=(("SERIAL1", "device"), ("SERIAL2", "device")), ports={5039: "echo"}
    )
    await srv.start()
    try:
        target = await resolve_target(srv.endpoint, "SERIAL2")
        stream = await open_device_stream(srv.endpoint, target, 5039)
        await stream.close()
        assert srv.requests == ["host:transport:SERIAL2", "tcp:5039"]
    finally:
        await srv.stop()


async def test_zero_devices_resolves_but_each_stream_gets_adbs_reason():
    srv = FakeAdbServer(devices=())
    await srv.start()
    try:
        target = await resolve_target(srv.endpoint, None)
        assert "none listed" in target.described
        with pytest.raises(AdbRefused) as ei:
            await open_device_stream(srv.endpoint, target, 5039)
        assert ei.value.request == "host:transport-any"
        assert "no devices" in ei.value.message
    finally:
        await srv.stop()


async def test_unknown_serial_is_adbs_own_refusal(fake):
    with pytest.raises(AdbRefused) as ei:
        await open_device_stream(fake.endpoint, DeviceTarget("NOPE"), 5039)
    assert ei.value.request == "host:transport:NOPE"
    assert "not found" in ei.value.message


# ── the stream ───────────────────────────────────────────────────────────


async def test_after_okay_the_connection_is_the_device_ports_bytes(fake):
    stream = await open_device_stream(fake.endpoint, DeviceTarget("SERIAL1"), 5039)
    try:
        stream.writer.write(b"hello device")
        await stream.writer.drain()
        assert await stream.reader.readexactly(12) == b"hello device"
    finally:
        await stream.close()
    assert fake.port_connections == 1


async def test_refused_stream_leaves_no_open_connection(fake):
    with pytest.raises(AdbRefused):
        await open_device_stream(fake.endpoint, DeviceTarget("SERIAL1"), 5040)
    assert fake.port_connections == 0


def test_module_default_endpoint_is_adbs_default():
    assert (m.DEFAULT_HOST, m.DEFAULT_PORT) == ("127.0.0.1", 5037)
