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
(``nc`` once its input ends, a client that sends a request body and then
shuts down). adb cannot carry that: its protocol has no half-close, and the
adb server closes the WHOLE stream as soon as its socket reads EOF
(packages/modules/adb/sockets.cpp, the `is_eof` branch of the local socket's
read handler). Passing the client's EOF on — what this module did first —
cut off exactly the reply the client was waiting for (bench, 2026-10-16).

So the two directions are not treated alike:

* device → client: the device's EOF is passed on as a half-close. TCP
  clients understand it, and nothing more can come from the device.
* client → device: the client's EOF is NOT passed on. The device direction
  stays open, and the connection ends when the device closes — or once the
  device has been silent for ``HALF_CLOSE_GRACE_S`` after the client's EOF.

The grace exists because a FIN cannot tell "shut my write side" from "I am
gone": both send the same packet. A client that is really gone would
otherwise keep its device stream (and, for a one-client server such as
gdbserver, the device server itself) open until the device happens to close.
A reply that starts within the grace and keeps flowing gets through; a write
to a client that is gone fails at once and ends the connection.

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
import time
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

# How long the device may stay silent after the client's EOF before the
# connection is closed (see "Half-close" above). Replies to a half-closed
# request start within milliseconds; two seconds is generous for those and
# short enough that a gone client frees a one-client device server quickly.
HALF_CLOSE_GRACE_S = 2.0

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


class _Flow:
    """Bytes moved one way, and since when it has been waiting for more.

    ``waiting_since`` is set only while the copy is blocked reading its
    source; while it is writing — a client slow to read can hold that for
    seconds — it is None. Silence is what the grace counts, and a reply
    stuck behind a slow reader is not silence."""

    def __init__(self) -> None:
        self.moved = 0
        self.waiting_since: float | None = time.monotonic()


async def _one_way(
    src: asyncio.StreamReader, dst: asyncio.StreamWriter, flow: _Flow, *, pass_eof: bool
) -> None:
    """Copy until ``src`` ends; then half-close ``dst`` if ``pass_eof``."""
    try:
        while True:
            flow.waiting_since = time.monotonic()
            data = await src.read(CHUNK)
            flow.waiting_since = None
            if not data:
                break
            dst.write(data)
            await dst.drain()
            flow.moved += len(data)
    except (OSError, asyncio.IncompleteReadError):
        # A reset peer is an abrupt EOF: nothing more will come from src,
        # and the other direction finds out on its own socket.
        pass
    finally:
        if pass_eof:
            _half_close(dst)


async def pump(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    device_reader: asyncio.StreamReader,
    device_writer: asyncio.StreamWriter,
    *,
    grace_s: float = HALF_CLOSE_GRACE_S,
) -> tuple[int, int]:
    """Pump both directions; see "Half-close" in the module doc.

    Returns ``(bytes to device, bytes from device)``. Ends when the device
    side has ended, or when it has been silent for ``grace_s`` after the
    client's EOF. The client's EOF is never written to the adb stream.
    """
    up, down = _Flow(), _Flow()
    from_device = asyncio.create_task(_one_way(device_reader, client_writer, down, pass_eof=True))
    to_device = asyncio.create_task(_one_way(client_reader, device_writer, up, pass_eof=False))
    try:
        await asyncio.wait({to_device, from_device}, return_when=asyncio.FIRST_COMPLETED)
        if not to_device.done():
            # The device ended first: nothing can come back any more, and
            # the client has seen the half-close. Its own EOF ends the rest.
            await to_device
        else:
            # The client ended first. Keep the device direction while it is
            # still talking; silence is counted from the client's EOF at the
            # earliest, and not at all while a reply is being written out.
            eof_at = time.monotonic()
            while not from_device.done():
                since = down.waiting_since
                if since is None:
                    await asyncio.wait({from_device}, timeout=grace_s)
                    continue
                remaining = max(since, eof_at) + grace_s - time.monotonic()
                if remaining <= 0:
                    break
                await asyncio.wait({from_device}, timeout=remaining)
        # A copy that died of anything but a closed socket is a bug; say so
        # rather than report a normal close.
        for task in (to_device, from_device):
            if task.done() and not task.cancelled():
                task.result()
    finally:
        # Also the way out when the connection itself is being dropped.
        for task in (from_device, to_device):
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
    return up.moved, down.moved


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
        half_close_grace_s: float | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.device_port = device_port
        self.serial = serial
        self.bind = bind
        self.local_port = local_port
        self.allow_remote = allow_remote
        self._emit: EventSink = on_event or (lambda _line: None)
        self._timeout = timeout_s
        # Read at construction, not bound as a default, so the module value
        # is the one setting for every caller that does not pass its own.
        self._grace = HALF_CLOSE_GRACE_S if half_close_grace_s is None else half_close_grace_s

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
            to_device, from_device = await pump(
                reader, writer, device.reader, device.writer, grace_s=self._grace
            )
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
