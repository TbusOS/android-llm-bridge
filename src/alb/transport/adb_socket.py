"""A minimal asyncio client for the adb server's smart-socket protocol.

## Why talk to the adb server directly instead of running ``adb forward``

``adb forward tcp:X tcp:Y`` asks the adb *server* to listen on port X. That
is the right tool when the server runs on this machine. alb's remote bench
setup breaks that assumption: the adb server runs on the host the board is
plugged into, and alb only forwards *connections to* that server down to a
local port (ADR-050). A forward opened through it listens on the bench host,
where nothing on this side can reach it.

The protocol itself already has what is needed. A client connects to the
server, binds the connection to one device (``host:transport:<serial>``),
then asks for the device-side service ``tcp:<port>``: adbd on the device
connects to ``localhost:<port>`` and from then on the connection is a raw
byte stream to that port (AOSP ``packages/modules/adb/SERVICES.TXT``). Every
step is a request on the connection we already hold, so it works through any
forwarder that carries plain adb traffic, with no change on the bench side.

Only the handful of services needed for that are implemented here. The
``adb`` binary remains the tool for everything else.

## Wire format (implemented exactly)

- request: 4 ASCII hex digits of the payload length, then the payload
  (``000chost:version``)
- reply: ``OKAY``, or ``FAIL`` followed by a length-prefixed message
- ``host:devices`` answers ``OKAY`` and then a length-prefixed listing
- after ``host:transport…`` is ``OKAY``, the same connection is routed to
  that device and the next request (``tcp:<port>``) gets its own ``OKAY`` /
  ``FAIL``; after that ``OKAY`` the connection *is* the device port's stream
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass

__all__ = [
    "CONNECT_TIMEOUT_S",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "AdbDevice",
    "AdbEndpointUnsupported",
    "AdbProtocolError",
    "AdbRefused",
    "AdbServerUnreachable",
    "AdbSocket",
    "AdbSocketError",
    "AmbiguousDevice",
    "DeviceTarget",
    "TcpEndpoint",
    "encode_request",
    "list_devices",
    "open_device_stream",
    "parse_devices",
    "parse_endpoint",
    "read_message",
    "read_reply",
    "resolve_target",
]

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5037  # adb's own default, the same one the resolver falls back to

# Covers connecting to the server and each handshake reply. Generous on
# purpose: through alb's forwarder every new connection costs a channel open
# across the bench link, and ``tcp:<port>`` waits for adbd to connect on the
# device. A pumped stream is never subject to this — it lives as long as the
# peers want it to.
CONNECT_TIMEOUT_S = 10.0

_STATUS_LEN = 4
_OKAY = b"OKAY"
_FAIL = b"FAIL"


# ── errors ─────────────────────────────────────────────────────────────


class AdbSocketError(Exception):
    """Base class: anything that stops a request from getting its answer."""


class AdbEndpointUnsupported(AdbSocketError):
    """The resolved endpoint is not a TCP socket this client can dial."""


class AdbServerUnreachable(AdbSocketError):
    """No adb server answered at the endpoint (refused / timed out)."""

    def __init__(self, endpoint: TcpEndpoint, cause: BaseException) -> None:
        self.endpoint = endpoint
        reason = str(cause) or cause.__class__.__name__
        super().__init__(f"no adb server at {endpoint.described}: {reason}")


class AdbProtocolError(AdbSocketError):
    """The server's reply did not follow the protocol, or it hung up mid-reply.

    Kept apart from :class:`AdbRefused`: a FAIL is the server *talking* and
    carries the device's reason; this is the server not talking adb at all,
    which almost always means the endpoint is some other program's port.
    """


class AdbRefused(AdbSocketError):
    """The server answered ``FAIL``. ``message`` is its text, possibly empty."""

    def __init__(self, request: str, message: str) -> None:
        self.request = request
        self.message = message
        super().__init__(f"{request}: {message or '(no message)'}")


class AmbiguousDevice(AdbSocketError):
    """More than one device is attached and no serial was given.

    adb itself would refuse ``host:transport-any`` here, but with a message
    that names no device. Refusing before the handshake lets the caller show
    the list — picking one silently is the failure this guards against.
    """

    def __init__(self, devices: tuple[AdbDevice, ...]) -> None:
        self.devices = devices
        listed = ", ".join(f"{d.serial} ({d.state})" for d in devices)
        super().__init__(f"{len(devices)} devices attached, --serial is required: {listed}")


# ── endpoint ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TcpEndpoint:
    """Where the adb server listens, as a dial-able host + port."""

    host: str
    port: int

    @property
    def described(self) -> str:
        return f"tcp:{self.host}:{self.port}"


def parse_endpoint(spec: str | None) -> TcpEndpoint:
    """Turn an ``ADB_SERVER_SOCKET``-style spec into a :class:`TcpEndpoint`.

    Accepts what adb accepts over TCP: ``tcp:<port>``, ``tcp:<host>:<port>``
    (an IPv6 host in brackets). ``None`` / ``""`` is adb's default. A
    ``unix:<path>`` spec is refused rather than guessed around: asyncio can
    open one, but nothing in alb produces that form today and a silent
    fallback to 5037 would be the exact "wrong server, zero devices" failure
    the resolver exists to prevent.
    """
    if not spec:
        return TcpEndpoint(DEFAULT_HOST, DEFAULT_PORT)
    kind, _, rest = spec.partition(":")
    if kind != "tcp":
        raise AdbEndpointUnsupported(
            f"adb server endpoint {spec!r} is not a tcp: socket; "
            "alb port can only reach the adb server over TCP"
        )
    host, _, port_text = rest.rpartition(":")
    if not port_text.isdigit() or not 0 < int(port_text) < 65536:
        raise AdbEndpointUnsupported(f"adb server endpoint {spec!r} has no usable port")
    return TcpEndpoint(host.strip("[]") or DEFAULT_HOST, int(port_text))


# ── framing ────────────────────────────────────────────────────────────


def encode_request(payload: str) -> bytes:
    """``<4 hex digits of length><payload>`` — the only request shape adb has."""
    raw = payload.encode()
    if len(raw) > 0xFFFF:
        raise ValueError(f"adb request too long ({len(raw)} bytes; limit 65535)")
    return f"{len(raw):04x}".encode() + raw


async def read_message(reader: asyncio.StreamReader) -> bytes:
    """A length-prefixed message: 4 hex digits, then that many bytes."""
    try:
        head = await reader.readexactly(_STATUS_LEN)
    except asyncio.IncompleteReadError as e:
        raise AdbProtocolError("adb server closed the connection before a message length") from e
    try:
        length = int(head.decode("ascii"), 16)
    except ValueError as e:
        raise AdbProtocolError(f"adb server sent a bad message length {head!r}") from e
    try:
        return await reader.readexactly(length)
    except asyncio.IncompleteReadError as e:
        raise AdbProtocolError(
            f"adb server closed the connection mid-message ({len(e.partial)}/{length} bytes)"
        ) from e


async def read_reply(reader: asyncio.StreamReader, request: str) -> None:
    """Consume the ``OKAY`` / ``FAIL`` status for ``request``.

    Returns on ``OKAY``; raises :class:`AdbRefused` with the server's text on
    ``FAIL``; anything else is :class:`AdbProtocolError`.
    """
    try:
        status = await reader.readexactly(_STATUS_LEN)
    except asyncio.IncompleteReadError as e:
        raise AdbProtocolError(
            f"adb server closed the connection without answering {request!r}"
        ) from e
    if status == _OKAY:
        return
    if status == _FAIL:
        # Some servers drop the connection right after FAIL with no length
        # at all; the refusal still stands, so report it with an empty text.
        try:
            message = (await read_message(reader)).decode(errors="replace")
        except AdbProtocolError:
            message = ""
        raise AdbRefused(request, message)
    raise AdbProtocolError(
        f"adb server answered {request!r} with {status!r} instead of OKAY/FAIL "
        "(is that endpoint really an adb server?)"
    )


# ── devices ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AdbDevice:
    serial: str
    state: str  # device | offline | unauthorized | ...


def parse_devices(text: str) -> tuple[AdbDevice, ...]:
    """``host:devices`` output: one ``<serial>\\t<state>`` per line.

    Every line counts, whatever its state: adb's ``transport-any`` counts
    them too, so an offline second board makes the target ambiguous.
    """
    found: list[AdbDevice] = []
    for line in text.splitlines():
        serial, _, state = line.strip().partition("\t")
        if serial:
            found.append(AdbDevice(serial, state.strip() or "unknown"))
    return tuple(found)


@dataclass(frozen=True)
class DeviceTarget:
    """Which device a stream is opened to.

    ``serial=None`` means ``host:transport-any``; ``listed`` is what the
    server reported at resolution time (empty when a serial was given, since
    nothing was asked).
    """

    serial: str | None
    listed: tuple[AdbDevice, ...] = ()

    @property
    def transport_request(self) -> str:
        return f"host:transport:{self.serial}" if self.serial else "host:transport-any"

    @property
    def described(self) -> str:
        if self.serial:
            return f"{self.serial} (--serial)"
        if len(self.listed) == 1:
            return f"{self.listed[0].serial} (the only one listed, {self.listed[0].state})"
        return "none listed yet (connections will be refused until a device appears)"


# ── the connection ─────────────────────────────────────────────────────


class AdbSocket:
    """One connection to the adb server.

    Holds the reader / writer pair rather than wrapping them: after
    ``tcp:<port>`` is accepted the pair *is* the device stream, and the
    caller pumps it with plain asyncio streams.
    """

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        endpoint: TcpEndpoint,
        *,
        timeout_s: float = CONNECT_TIMEOUT_S,
    ) -> None:
        self.reader = reader
        self.writer = writer
        self.endpoint = endpoint
        self._timeout = timeout_s

    @classmethod
    async def connect(
        cls, endpoint: TcpEndpoint, *, timeout_s: float = CONNECT_TIMEOUT_S
    ) -> AdbSocket:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(endpoint.host, endpoint.port), timeout_s
            )
        except (OSError, TimeoutError) as e:
            raise AdbServerUnreachable(endpoint, e) from e
        return cls(reader, writer, endpoint, timeout_s=timeout_s)

    async def request(self, payload: str) -> None:
        """Send one request and wait for its ``OKAY`` (``FAIL`` raises)."""
        self.writer.write(encode_request(payload))
        try:
            await self.writer.drain()
            await asyncio.wait_for(read_reply(self.reader, payload), self._timeout)
        except TimeoutError as e:
            raise AdbProtocolError(
                f"adb server did not answer {payload!r} within {self._timeout:g}s"
            ) from e
        except OSError as e:
            raise AdbProtocolError(
                f"connection to the adb server failed during {payload!r}: {e}"
            ) from e

    async def query(self, payload: str) -> bytes:
        """A ``host:`` service that answers ``OKAY`` + one message."""
        await self.request(payload)
        try:
            return await asyncio.wait_for(read_message(self.reader), self._timeout)
        except TimeoutError as e:
            raise AdbProtocolError(
                f"adb server did not finish answering {payload!r} within {self._timeout:g}s"
            ) from e

    async def close(self) -> None:
        self.writer.close()
        with contextlib.suppress(Exception):
            await self.writer.wait_closed()


async def list_devices(
    endpoint: TcpEndpoint, *, timeout_s: float = CONNECT_TIMEOUT_S
) -> tuple[AdbDevice, ...]:
    """``host:devices`` on its own connection (the server closes it after)."""
    sock = await AdbSocket.connect(endpoint, timeout_s=timeout_s)
    try:
        raw = await sock.query("host:devices")
    finally:
        await sock.close()
    return parse_devices(raw.decode(errors="replace"))


async def resolve_target(
    endpoint: TcpEndpoint, serial: str | None, *, timeout_s: float = CONNECT_TIMEOUT_S
) -> DeviceTarget:
    """Decide which device to open streams to.

    With a serial, nothing is asked — adb's own ``FAIL`` names a serial that
    is not attached. Without one, the device list is consulted first and two
    or more devices refuse the request (:class:`AmbiguousDevice`). Zero
    devices is not refused here: a listener may legitimately be set up before
    the board finishes rebooting, and each connection reports adb's own "no
    devices" text until it does.
    """
    if serial:
        return DeviceTarget(serial)
    devices = await list_devices(endpoint, timeout_s=timeout_s)
    if len(devices) > 1:
        raise AmbiguousDevice(devices)
    return DeviceTarget(None, devices)


async def open_device_stream(
    endpoint: TcpEndpoint,
    target: DeviceTarget,
    device_port: int,
    *,
    timeout_s: float = CONNECT_TIMEOUT_S,
) -> AdbSocket:
    """Connect, bind to the device, ask for ``tcp:<device_port>``.

    On return the socket's reader / writer are the raw stream to that port.
    ``AdbRefused`` carries the device's reason (nothing listening, usually);
    the connection is closed before any error propagates.
    """
    sock = await AdbSocket.connect(endpoint, timeout_s=timeout_s)
    try:
        await sock.request(target.transport_request)
        await sock.request(f"tcp:{device_port}")
    except BaseException:
        await sock.close()
        raise
    return sock
