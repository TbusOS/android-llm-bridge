"""OS-level loopback forwarders (ADR-051/052).

The keystone of the dial-home design: REAL OS-level listeners owned by the
alb-api process — 127.0.0.1:5037 for adb, 127.0.0.1:9001 for serial. Because
they are operating-system sockets (not in-loop objects), the separate alb-mcp /
CLI processes reach the device by plain connect() — alb's transports and MCP
tools are unchanged.

ADR-052 channel roles decide how a local connection maps onto agent channels:
  - adb    = DAEMON  — proxies the adb server (a listen-socket daemon), which
             is happy to serve many clients. ONE channel per local connection.
             Fail fast, NO retry; a reset is a real error (USB reauth / device
             drop / server crash). L-034.
  - serial = GATEWAY — proxies the agent's COM port, which the OS opens
             EXCLUSIVELY. All local connections therefore SHARE one channel
             (`_SerialSession`), refcounted: the first opens it, the last
             closes it. See the class docstring for why fan-out is the only
             correct model for a UART.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import Callable
from typing import Any

from alb.remote.protocol import ADB_TARGET, CAP_FASTBOOT, ChannelRole, ChannelType
from alb.remote.registry import ChannelOpener, ChannelOpenError, DataChannel

_log = logging.getLogger(__name__)

DEFAULT_ADB_HOST = "127.0.0.1"
DEFAULT_ADB_PORT = 5037
DEFAULT_SERIAL_HOST = "127.0.0.1"
DEFAULT_SERIAL_PORT = 9001  # matches infra.config.SerialConfig.default_tcp_port
DEFAULT_BAUD = 115200
DIAL_BACK_TIMEOUT_S = 10.0
_CHUNK = 65536

# RX chunks buffered per local connection before we consider it wedged.
# Chunks are whatever one agent-side serial read produced (sub-KB at 115200),
# so this is ~100 KB of slack — a localhost socket that has not drained that
# much has stopped reading, and stalling the shared pump for it would starve
# every OTHER reader (the capture that must not miss boot bytes).
_SUB_QUEUE_MAX = 256

# Inter-attempt waits when the agent reports it could not open the serial port.
# Covers the hand-off gap while the agent releases the COM handle from the
# session that just ended; a port held by another program still fails, ~0.5s later.
_SERIAL_OPEN_BACKOFF_S: tuple[float, ...] = (0.15, 0.35)


def _err_text(exc: BaseException) -> str:
    """Human-readable one-liner for a channel-open failure.

    ``str(asyncio.TimeoutError())`` is the empty string, which used to make
    the "channel open failed" log line end in a bare colon and say nothing.
    Fall back to the exception class name whenever the message is empty.
    """
    msg = str(exc).strip()
    if isinstance(exc, TimeoutError) and not msg:
        return f"agent did not dial back within {DIAL_BACK_TIMEOUT_S:.0f}s"
    return msg or type(exc).__name__


def _env_int(var: str, default: int) -> int:
    raw = os.environ.get(var)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        _log.warning("invalid %s=%r; using %d", var, raw, default)
        return default


def serial_com() -> str | None:
    """The COM port the serial forwarder should ask the agent to open, or None
    if serial is not configured on this hub (ALB_AGENT_SERIAL_COM)."""
    return os.environ.get("ALB_AGENT_SERIAL_COM") or None


def serial_baud() -> int:
    return _env_int("ALB_AGENT_SERIAL_BAUD", DEFAULT_BAUD)


def serial_configured() -> bool:
    return serial_com() is not None


class ChannelForwarder:
    """A process-level singleton OS listener (ADR-051) that bridges each local
    connection to the agent over one data channel. NOT a per-connection object:
    it resolves the active agent lazily via `get_agent`, so a reconnecting agent
    never re-binds the port (no EADDRINUSE race)."""

    def __init__(
        self,
        get_agent: Callable[[], ChannelOpener | None],
        *,
        channel_type: ChannelType,
        role: ChannelRole,
        params: dict[str, object],
        host: str,
        port: int,
    ) -> None:
        self._get_agent = get_agent
        self._channel_type = channel_type
        self._role = role
        self._params = params
        self._host = host
        self._port = port
        self._server: asyncio.Server | None = None
        self._conns: set[asyncio.Task[None]] = set()

    @property
    def port(self) -> int:
        """The bound port (useful when constructed with port=0 for tests)."""
        if self._server is not None and self._server.sockets:
            return int(self._server.sockets[0].getsockname()[1])
        return self._port

    @property
    def is_bound(self) -> bool:
        """True once the OS listener is attached."""
        return self._server is not None

    async def attach(self) -> None:
        """Start the OS-level listener. Idempotent."""
        if self._server is not None:
            return
        self._server = await asyncio.start_server(self._on_local_conn, self._host, self._port)

    async def detach(self) -> None:
        """Close the listener + cancel all in-flight connection tasks. Safe
        on every teardown path so the port never leaks."""
        server, self._server = self._server, None
        if server is not None:
            server.close()
            with contextlib.suppress(Exception):
                await server.wait_closed()
        tasks = list(self._conns)
        self._conns.clear()
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t

    async def _on_local_conn(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._conns.add(task)
        try:
            await self._handle(reader, writer)
        finally:
            if task is not None:
                self._conns.discard(task)
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        agent = self._get_agent()
        if agent is None:
            # listener is up but no agent is connected — fail fast.
            _log.debug("%s connection with no agent connected; closing", self._channel_type.value)
            return
        # A SINGLE attempt (ADR-052). The adb (DAEMON) path must never retry;
        # the serial (GATEWAY) path's bounded retry lives in the alb-side
        # SerialTransport, not here. So neither role retries in the forwarder.
        try:
            channel = await agent.open_data_channel(
                ctype=self._channel_type,
                role=self._role,
                params=self._params,
                timeout=DIAL_BACK_TIMEOUT_S,
            )
        except Exception as e:
            _log.warning(
                "%s channel open failed (no retry): %s",
                self._channel_type.value,
                _err_text(e),
            )
            return
        # Log the SUCCESS too, not only the failure. Until 2026-08-10 this path
        # was silent on both ends, so "the adb tunnel is broken" could not be
        # confirmed or refuted from any log — absence of errors was read as
        # absence of traffic, and a working tunnel was worked around for a day.
        # A channel open is a rare, operator-meaningful event; one INFO line
        # each is what makes the log answer the question that gets asked of it.
        _log.info(
            "%s channel opened -> %s", self._channel_type.value, self._params.get("target", "")
        )
        try:
            await self._shuttle(reader, writer, channel)
        finally:
            _log.info("%s channel closed", self._channel_type.value)

    async def _shuttle(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        channel: DataChannel,
    ) -> None:
        async def local_to_channel() -> None:
            try:
                while True:
                    data = await reader.read(_CHUNK)
                    if not data:
                        return
                    await channel.send(data)
            except Exception:
                return
            # CancelledError propagates; the outer wait cancels + suppresses it.

        async def channel_to_local() -> None:
            try:
                while True:
                    data = await channel.recv()
                    if not data:
                        return
                    writer.write(data)
                    await writer.drain()
            except Exception:
                return

        t1 = asyncio.create_task(local_to_channel())
        t2 = asyncio.create_task(channel_to_local())
        try:
            _done, pending = await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t
        finally:
            with contextlib.suppress(Exception):
                await channel.aclose()


class AdbForwarder(ChannelForwarder):
    """Bridge local TCP (127.0.0.1:5037) <-> the agent's adb server (DAEMON)."""

    def __init__(
        self,
        get_agent: Callable[[], ChannelOpener | None],
        *,
        host: str = DEFAULT_ADB_HOST,
        port: int = DEFAULT_ADB_PORT,
    ) -> None:
        super().__init__(
            get_agent,
            channel_type=ChannelType.TCP,
            role=ChannelRole.DAEMON,
            params={"target": ADB_TARGET},
            host=host,
            port=port,
        )


class _Subscriber:
    """One local connection attached to the shared serial session."""

    __slots__ = ("gone", "queue", "task", "writer")

    def __init__(self, writer: asyncio.StreamWriter) -> None:
        self.writer = writer
        self.queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=_SUB_QUEUE_MAX)
        self.task: asyncio.Task[None] | None = None
        self.gone = asyncio.Event()

    async def pump(self) -> None:
        """Drain our RX queue into the local socket. Per-subscriber so one slow
        reader can never block the shared fan-out."""
        try:
            while True:
                data = await self.queue.get()
                self.writer.write(data)
                await self.writer.drain()
        except Exception:
            return


class _SerialSession:
    """ONE agent-side serial channel, fanned out to N local connections.

    Why this exists (issue #4): the agent opens the COM port with
    ``serial.Serial(com, baud)``, and an OS serial port is EXCLUSIVE. Opening a
    channel per local connection therefore meant the second concurrent alb
    command — `serial shell` while `serial capture` runs, a second capture, the
    web UART console alongside either — silently got nothing: the agent's open
    failed, it never dialed back, and the hub closed the local socket after the
    dial-back timeout. `shell` then saw zero bytes and blamed the board
    (BOARD_UNREACHABLE); `send` reported "wrote N bytes" for bytes that went
    nowhere.

    A UART is physically a broadcast medium — every reader must see everything
    the board prints, and any reader may type — so sharing is not a workaround
    but the only faithful model. RX is copied to every subscriber; TX from all
    subscribers is merged into the one channel (serialized by a lock, since a
    half-written frame from one writer interleaved with another's would corrupt
    both).

    Lifetime is refcounted by the forwarder: the first local connection opens
    the channel, the last one to leave closes it.
    """

    def __init__(self, channel: DataChannel) -> None:
        self._channel = channel
        self._subs: set[_Subscriber] = set()
        self._send_lock = asyncio.Lock()
        self.refs = 0
        self.closed = asyncio.Event()
        self._rx = asyncio.create_task(self._fan_out())

    async def _fan_out(self) -> None:
        try:
            while True:
                data = await self._channel.recv()
                if not data:
                    return
                for sub in list(self._subs):
                    try:
                        sub.queue.put_nowait(data)
                    except asyncio.QueueFull:
                        _log.warning(
                            "serial subscriber not draining (%d chunks queued); "
                            "dropping it so the other readers keep flowing",
                            _SUB_QUEUE_MAX,
                        )
                        self._drop(sub)
        except Exception as e:
            _log.debug("serial session RX ended: %s", _err_text(e))
        finally:
            self.closed.set()
            for sub in list(self._subs):
                self._drop(sub)

    def _drop(self, sub: _Subscriber) -> None:
        self._subs.discard(sub)
        sub.gone.set()
        if sub.task is not None:
            sub.task.cancel()

    async def serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Attach one local connection until it (or the session) ends."""
        if self.closed.is_set():
            return
        sub = _Subscriber(writer)
        sub.task = asyncio.create_task(sub.pump())
        self._subs.add(sub)
        if self.closed.is_set():
            # The session died while we were attaching. Nothing above yields, so
            # this cannot happen today — but a subscriber left in a dead set
            # would wait on `gone` forever, and that is not a failure mode worth
            # depending on statement order to avoid.
            self._drop(sub)
        tx = asyncio.create_task(self._local_to_channel(reader))
        gone = asyncio.create_task(sub.gone.wait())
        try:
            _done, pending = await asyncio.wait({tx, gone}, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t
        finally:
            self._drop(sub)
            if sub.task is not None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await sub.task

    async def _local_to_channel(self, reader: asyncio.StreamReader) -> None:
        try:
            while True:
                data = await reader.read(_CHUNK)
                if not data:
                    return
                async with self._send_lock:
                    await self._channel.send(data)
        except Exception:
            return

    async def aclose(self) -> None:
        self.closed.set()
        self._rx.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._rx
        for sub in list(self._subs):
            self._drop(sub)
        with contextlib.suppress(Exception):
            await self._channel.aclose()


class SerialForwarder(ChannelForwarder):
    """Bridge local TCP (127.0.0.1:9001) <-> the agent's COM port (GATEWAY).

    Unlike the adb forwarder, ALL local connections share one agent channel —
    see :class:`_SerialSession` for why an exclusive COM port leaves no other
    honest option.
    """

    def __init__(
        self,
        get_agent: Callable[[], ChannelOpener | None],
        *,
        com: str,
        baud: int,
        host: str = DEFAULT_SERIAL_HOST,
        port: int = DEFAULT_SERIAL_PORT,
    ) -> None:
        super().__init__(
            get_agent,
            channel_type=ChannelType.SERIAL,
            role=ChannelRole.GATEWAY,
            params={"com": com, "baud": baud},
            host=host,
            port=port,
        )
        self._session: _SerialSession | None = None
        self._session_lock = asyncio.Lock()

    @property
    def session_refs(self) -> int:
        """Local connections currently sharing the serial channel (0 = closed).
        Surfaced for /agent/status and tests."""
        return self._session.refs if self._session is not None else 0

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            session = await self._acquire()
        except Exception as e:
            _log.warning("serial channel open failed (no retry): %s", _err_text(e))
            return
        try:
            await session.serve(reader, writer)
        finally:
            await self._release(session)

    async def _acquire(self) -> _SerialSession:
        """Join the live session, or open the channel if we are the first.

        The lock is deliberately held across ``open_data_channel``: a second
        connection arriving mid-open must WAIT and then reuse the result, not
        race into a second COM open that the agent would have to refuse.
        """
        async with self._session_lock:
            session = self._session
            if session is not None and not session.closed.is_set():
                session.refs += 1
                return session
            agent = self._get_agent()
            if agent is None:
                raise ConnectionError("no agent connected")
            channel = await self._open_channel_with_retry(agent)
            session = _SerialSession(channel)
            session.refs = 1
            self._session = session
            return session

    async def _open_channel_with_retry(self, agent: ChannelOpener) -> DataChannel:
        """Bounded retry on "the agent could not open the port" — the GATEWAY
        half of ADR-052, which permits it precisely because an exclusive
        gateway can be *momentarily* busy.

        The window we are covering: the previous session just closed, and the
        agent has not finished releasing its COM handle. Retrying turns a
        spurious "Access is denied" into a ~0.5 s hiccup. A port held by some
        OTHER program still fails — just half a second later, with the agent's
        own message intact.
        """
        last: Exception | None = None
        for attempt in range(len(_SERIAL_OPEN_BACKOFF_S) + 1):
            try:
                return await agent.open_data_channel(
                    ctype=self._channel_type,
                    role=self._role,
                    params=self._params,
                    timeout=DIAL_BACK_TIMEOUT_S,
                )
            except ChannelOpenError as e:
                last = e
                if attempt < len(_SERIAL_OPEN_BACKOFF_S):
                    await asyncio.sleep(_SERIAL_OPEN_BACKOFF_S[attempt])
        assert last is not None  # the loop only exits here after a failure
        raise last

    async def _release(self, session: _SerialSession) -> None:
        async with self._session_lock:
            session.refs -= 1
            if session.refs > 0:
                return
            if self._session is session:
                self._session = None
            # Close INSIDE the lock: a connection arriving right now must not
            # start a fresh COM open while the agent is still releasing this
            # one — that is the same exclusive-open race, just inverted.
            await session.aclose()

    async def detach(self) -> None:
        # Deliberately NOT under _session_lock: a shutdown must not queue behind
        # an in-flight open (up to DIAL_BACK_TIMEOUT_S). Instead, sweep twice —
        # once now, once after the connection tasks are cancelled — so a session
        # created by a racing _acquire cannot outlive the forwarder.
        await self._close_session()
        await super().detach()
        await self._close_session()

    async def _close_session(self) -> None:
        session, self._session = self._session, None
        if session is not None:
            await session.aclose()


_ADB_FORWARDER: ChannelForwarder | None = None
_SERIAL_FORWARDER: ChannelForwarder | None = None


def get_adb_forwarder() -> ChannelForwarder:
    """Process-wide adb forwarder, registry-backed, lazily created."""
    global _ADB_FORWARDER
    if _ADB_FORWARDER is None:
        from alb.remote.registry import get_agent_registry

        _ADB_FORWARDER = AdbForwarder(
            get_agent_registry().current_agent,
            port=_env_int("ALB_ADB_FORWARD_PORT", DEFAULT_ADB_PORT),
        )
    return _ADB_FORWARDER


def get_serial_forwarder() -> ChannelForwarder:
    """Process-wide serial forwarder, registry-backed, lazily created. Reads the
    target COM/baud from the hub env (ALB_AGENT_SERIAL_COM / _BAUD)."""
    global _SERIAL_FORWARDER
    if _SERIAL_FORWARDER is None:
        from alb.remote.registry import get_agent_registry

        _SERIAL_FORWARDER = SerialForwarder(
            get_agent_registry().current_agent,
            com=serial_com() or "",
            baud=serial_baud(),
            port=_env_int("ALB_SERIAL_FORWARD_PORT", DEFAULT_SERIAL_PORT),
        )
    return _SERIAL_FORWARDER


async def shutdown_forwarders() -> None:
    """Detach + drop both singletons (alb-api lifespan shutdown)."""
    global _ADB_FORWARDER, _SERIAL_FORWARDER
    for f in (_ADB_FORWARDER, _SERIAL_FORWARDER):
        if f is not None:
            await f.detach()
    _ADB_FORWARDER = None
    _SERIAL_FORWARDER = None


def reset_forwarders() -> None:
    """Sync best-effort reset for tests — closes the listeners (skips the async
    wait_closed) and drops the singletons so the next test starts clean."""
    global _ADB_FORWARDER, _SERIAL_FORWARDER
    for f in (_ADB_FORWARDER, _SERIAL_FORWARDER):
        if f is not None and f._server is not None:
            f._server.close()
    _ADB_FORWARDER = None
    _SERIAL_FORWARDER = None


def _fwd_view(f: ChannelForwarder | None, default_port: int, env_var: str) -> dict[str, Any]:
    if f is not None:
        return {"bound": f.is_bound, "port": f.port}
    return {"bound": False, "port": _env_int(env_var, default_port)}


def forwarder_status() -> dict[str, Any]:
    """Read-only snapshot of the forwarders for the web Connection Center.
    Reads the module singletons WITHOUT creating them (no side effects)."""
    return {
        "adb": _fwd_view(_ADB_FORWARDER, DEFAULT_ADB_PORT, "ALB_ADB_FORWARD_PORT"),
        "serial": {
            **_fwd_view(_SERIAL_FORWARDER, DEFAULT_SERIAL_PORT, "ALB_SERIAL_FORWARD_PORT"),
            "configured": serial_configured(),
            "com": serial_com(),
            "baud": serial_baud(),
            # how many alb readers/writers currently share the one COM channel
            # (capture + shell + web console can now coexist — issue #4)
            "readers": getattr(_SERIAL_FORWARDER, "session_refs", 0),
        },
        # Not a forwarder (ADR-056 — there is no port to bind), but it belongs
        # in the same snapshot: an operator asking "why won't my flash start"
        # needs `available` (is there a fastboot-capable agent at all) and
        # `busy` (is someone else mid-job) from the same place they already
        # look for adb and serial.
        "flash": _flash_view(),
    }


def _flash_view() -> dict[str, Any]:
    """Flash capability + job state, WITHOUT creating the service — same
    no-side-effects rule as the forwarder views, so polling never allocates.

    `available` is derived from the agent's advertised caps rather than from
    the service, because it is a fact about the bench, not about whether
    anything has used the service yet. Reporting False just because the
    singleton is still unborn made this endpoint contradict
    `/api/flash/status` (which does create it) — two answers to one question,
    and the wrong one is the one an operator reaches for first.

    `busy` still comes from the service, and its absence is a sound
    inference rather than a guess: with no service there is no lock, so no
    job can be running.
    """
    from alb.remote import flash as _flash
    from alb.remote.registry import get_agent_registry

    agent = get_agent_registry().current_agent()
    available = agent is not None and CAP_FASTBOOT in getattr(agent, "caps", [])
    service = _flash._SERVICE
    if service is None:
        return {"available": available, "busy": False, "job": ""}
    return {**service.status(), "available": available}
