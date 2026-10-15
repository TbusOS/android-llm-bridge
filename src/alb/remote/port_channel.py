"""A local listener that turns each TCP connection into a device-port stream.

## Why a listener, and why one adb connection per accepted connection

``adb forward`` cannot do this job on a remote bench (see
:mod:`alb.transport.adb_socket`), but the thing people want is still "a
local port that *is* the device's port": gdb's ``target remote``, a browser,
``curl`` — none of them speak adb, all of them speak TCP. So this module
listens on a local port and, for every connection it accepts, opens a fresh
adb connection, walks it to ``tcp:<device_port>``, and pumps bytes both ways.

One adb connection per client is not laziness. An adb stream maps to exactly
one socket on the device, and the programs on both ends assume that: gdb
expects its own gdbserver connection, an HTTP client expects each request's
connection to be a separate server-side accept. Multiplexing would need a
framing both ends agree on, and neither does.

## Half-close

TCP lets one side say "I have nothing more to send" while still reading; a
client that shuts its write side still expects the server's remaining bytes
(gdb does this on detach, ``curl`` after sending a request body). So when one
direction sees EOF, this side shuts *only the write side* of the other
socket and keeps pumping the opposite direction until it ends too. Closing
both at the first EOF would cut off exactly the bytes the client is waiting
for.

## Exposure

The listener binds loopback unless told otherwise, and a non-loopback bind
is refused without ``allow_remote``. A device port is usually a debug
server with no authentication; putting it on a network interface should
be a decision, not a default.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import itertools
from collections.abc import Callable

from alb.transport.adb_socket import (
    CONNECT_TIMEOUT_S,
    AdbRefused,
    AdbSocket,
    AdbSocketError,
    DeviceTarget,
    TcpEndpoint,
    open_device_stream,
    resolve_target,
)

__all__ = [
    "CHUNK",
    "BindRefused",
    "EventSink",
    "LocalPortBusy",
    "PortChannel",
    "PortChannelError",
    "check_bind",
    "is_loopback",
    "pump",
]

# 64 KiB per read: large enough that a gdb memory dump or an HTTP body moves
# in few syscalls, small enough that a chatty peer does not sit behind a
# half-filled buffer. Same figure the hub's forwarder uses.
CHUNK = 64 * 1024

EventSink = Callable[[str], None]


class PortChannelError(Exception):
    """Base class for refusals raised before the listener is up."""


class BindRefused(PortChannelError):
    """A non-loopback bind address without ``allow_remote``."""


class LocalPortBusy(PortChannelError):
    """An explicitly requested local port is already taken."""


def is_loopback(host: str) -> bool:
    """``localhost`` and any loopback address, v4 or v6."""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def check_bind(host: str, *, allow_remote: bool) -> None:
    """Refuse to expose the device port beyond this machine unless asked to."""
    if allow_remote or is_loopback(host):
        return
    raise BindRefused(
        f"refusing to listen on {host!r}: that exposes the device port to the network; "
        "bind 127.0.0.1, or pass --allow-remote if that is what you want"
    )


def _half_close(writer: asyncio.StreamWriter) -> None:
    """Tell the peer no more bytes are coming, without closing our read side."""
    if writer.is_closing() or not writer.can_write_eof():
        return
    with contextlib.suppress(OSError):
        writer.write_eof()


async def _one_way(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> int:
    """Copy until ``src`` ends, then half-close ``dst``. Returns bytes moved."""
    moved = 0
    try:
        while True:
            data = await src.read(CHUNK)
            if not data:
                break
            dst.write(data)
            await dst.drain()
            moved += len(data)
    except (OSError, asyncio.IncompleteReadError):
        # A reset peer is an abrupt EOF: nothing more will come from src,
        # and the other direction finds out on its own socket.
        pass
    finally:
        _half_close(dst)
    return moved


async def pump(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    device_reader: asyncio.StreamReader,
    device_writer: asyncio.StreamWriter,
) -> tuple[int, int]:
    """Pump both directions until both have ended.

    Returns ``(bytes to device, bytes from device)``. Neither direction is
    cut short by the other finishing — that is the half-close contract.
    """
    return await asyncio.gather(
        _one_way(client_reader, device_writer),
        _one_way(device_reader, client_writer),
    )


def _peer_text(writer: asyncio.StreamWriter) -> str:
    peer = writer.get_extra_info("peername")
    if isinstance(peer, tuple) and len(peer) >= 2:
        return f"{peer[0]}:{peer[1]}"
    return str(peer)


class PortChannel:
    """Listen on ``bind:local_port`` and bridge each connection to ``tcp:<device_port>``.

    ``local_port=None`` means "the device's port number if it is free here,
    otherwise an ephemeral one": the same number on both sides is what people
    type by habit, but refusing to start because something else holds it
    would help nobody. The port actually bound is :attr:`bound_port`, and
    the caller prints it.
    """

    def __init__(
        self,
        *,
        endpoint: TcpEndpoint,
        device_port: int,
        serial: str | None = None,
        bind: str = "127.0.0.1",
        local_port: int | None = None,
        allow_remote: bool = False,
        on_event: EventSink | None = None,
        timeout_s: float = CONNECT_TIMEOUT_S,
    ) -> None:
        self.endpoint = endpoint
        self.device_port = device_port
        self.serial = serial
        self.bind = bind
        self.local_port = local_port
        self.allow_remote = allow_remote
        self._emit: EventSink = on_event or (lambda _line: None)
        self._timeout = timeout_s

        self.target: DeviceTarget | None = None
        self._server: asyncio.Server | None = None
        self._bound_port: int | None = None
        self._ids = itertools.count(1)
        self._conns: set[asyncio.Task[None]] = set()

        # Totals, not per-connection records: a listener left up for a day
        # must not grow with every connection it served.
        self.connections = 0
        self.refused = 0
        self.bytes_to_device = 0
        self.bytes_from_device = 0

    # ── lifecycle ──────────────────────────────────────────────────────

    @property
    def bound_port(self) -> int:
        if self._bound_port is None:
            raise RuntimeError("PortChannel is not started")
        return self._bound_port

    @property
    def moved_from_device_port(self) -> bool:
        """True when the local port is not the device port — worth a word
        in the announcement, since the habit is to type the same number."""
        return self.local_port is None and self.bound_port != self.device_port

    async def start(self) -> int:
        """Check the bind, resolve the device, bind the listener. Returns the port.

        Order matters: the bind check costs nothing and can refuse without
        touching the adb server; resolving the device talks to the server and
        refuses ambiguity before any client could connect; only then is a
        port taken.
        """
        check_bind(self.bind, allow_remote=self.allow_remote)
        self.target = await resolve_target(self.endpoint, self.serial, timeout_s=self._timeout)
        self._server = await self._bind_listener()
        self._bound_port = int(self._server.sockets[0].getsockname()[1])
        return self._bound_port

    async def _bind_listener(self) -> asyncio.Server:
        if self.local_port is not None:
            try:
                return await asyncio.start_server(self._on_client, self.bind, self.local_port)
            except OSError as e:
                raise LocalPortBusy(
                    f"cannot listen on {self.bind}:{self.local_port}: {e.strerror or e}; "
                    "pick another with --local, or omit it to take a free port"
                ) from e
        try:
            return await asyncio.start_server(self._on_client, self.bind, self.device_port)
        except OSError:
            # Taken (or privileged): fall back to whatever the OS hands out.
            return await asyncio.start_server(self._on_client, self.bind, 0)

    async def serve_forever(self) -> None:
        if self._server is None:
            raise RuntimeError("PortChannel is not started")
        await self._server.serve_forever()

    async def close(self) -> None:
        """Stop accepting and drop every connection in flight.

        Order matters: from Python 3.12 ``Server.wait_closed()`` waits for
        every accepted connection to finish, so it comes after the
        connections are cancelled — the other way round, Ctrl-C would hang
        until the debugger on the other end hung up by itself.
        """
        server, self._server = self._server, None
        if server is not None:
            server.close()
        for task in list(self._conns):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._conns.clear()
        if server is not None:
            with contextlib.suppress(Exception):
                await server.wait_closed()

    # ── per connection ─────────────────────────────────────────────────

    async def _on_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._conns.add(task)
        try:
            await self._bridge(reader, writer)
        finally:
            if task is not None:
                self._conns.discard(task)

    async def _bridge(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        conn_id = next(self._ids)
        self.connections += 1
        self._emit(f"[{conn_id}] open {_peer_text(writer)} -> tcp:{self.device_port}")
        device: AdbSocket | None = None
        try:
            assert self.target is not None, "start() resolves the target first"
            try:
                device = await open_device_stream(
                    self.endpoint, self.target, self.device_port, timeout_s=self._timeout
                )
            except AdbRefused as e:
                # The device said no — nothing listening there, most likely.
                # The client gets a clean close; the listener keeps serving.
                self.refused += 1
                self._emit(f"[{conn_id}] refused by device: {e.message or '(no message)'}")
                return
            except AdbSocketError as e:
                self.refused += 1
                self._emit(f"[{conn_id}] adb error: {e}")
                return
            to_device, from_device = await pump(reader, writer, device.reader, device.writer)
            self.bytes_to_device += to_device
            self.bytes_from_device += from_device
            self._emit(
                f"[{conn_id}] closed · {to_device} B to device · {from_device} B from device"
            )
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            if device is not None:
                await device.close()
