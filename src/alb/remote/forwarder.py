"""OS-level loopback forwarder for adb (ADR-051/052).

This is the keystone of the dial-home design: a REAL OS-level listener on
127.0.0.1:5037 owned by the alb-api process. Because it is an operating-system
socket (not an in-loop object), the separate alb-mcp / CLI processes reach the
device by plain connect(127.0.0.1:5037) — alb's transports and MCP tools are
unchanged.

Each accepted local connection opens ONE data channel to the agent and shuttles
bytes. ADR-052: the adb channel has DAEMON role — fail fast, NO retry. A reset
is a real error (USB reauth / device drop / adb server crash); retrying would
silently mask it (L-034).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import Callable

from alb.remote.protocol import ADB_TARGET, ChannelRole, ChannelType
from alb.remote.registry import ChannelOpener, DataChannel

_log = logging.getLogger(__name__)

DEFAULT_ADB_HOST = "127.0.0.1"
DEFAULT_ADB_PORT = 5037
DIAL_BACK_TIMEOUT_S = 10.0
_CHUNK = 65536


def forward_port() -> int:
    """The adb forwarder's bind port. Fixed 5037 by default (so MCP/CLI reach
    it unchanged); ALB_ADB_FORWARD_PORT overrides it (tests use 0)."""
    raw = os.environ.get("ALB_ADB_FORWARD_PORT")
    if raw is None:
        return DEFAULT_ADB_PORT
    try:
        return int(raw)
    except ValueError:
        _log.warning("invalid ALB_ADB_FORWARD_PORT=%r; using %d", raw, DEFAULT_ADB_PORT)
        return DEFAULT_ADB_PORT


class AdbForwarder:
    """Bridge local TCP (127.0.0.1:5037) <-> the agent's adb server.

    ADR-051: a process-level singleton owned by the alb-api lifespan, NOT a
    per-connection object. It resolves the active agent lazily per local
    connection via `get_agent`, so a reconnecting agent never re-binds the OS
    port (no EADDRINUSE race)."""

    def __init__(
        self,
        get_agent: Callable[[], ChannelOpener | None],
        *,
        host: str = DEFAULT_ADB_HOST,
        port: int = DEFAULT_ADB_PORT,
    ) -> None:
        self._get_agent = get_agent
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

    async def attach(self) -> None:
        """Start the OS-level listener. Idempotent."""
        if self._server is not None:
            return
        self._server = await asyncio.start_server(self._on_local_conn, self._host, self._port)

    async def detach(self) -> None:
        """Close the listener + cancel all in-flight connection tasks. Safe
        on every teardown path (checklist #4) so the port never leaks."""
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
            _log.debug("adb connection with no agent connected; closing")
            return
        # DAEMON role (ADR-052): a SINGLE attempt. On any failure, close.
        # Deliberately NO retry / reconnect loop here.
        try:
            channel = await agent.open_data_channel(
                ctype=ChannelType.TCP,
                role=ChannelRole.DAEMON,
                params={"target": ADB_TARGET},
                timeout=DIAL_BACK_TIMEOUT_S,
            )
        except Exception as e:
            _log.warning("adb channel open failed (no retry): %s", e)
            return
        await self._shuttle(reader, writer, channel)

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


_FORWARDER: AdbForwarder | None = None


def get_adb_forwarder() -> AdbForwarder:
    """Process-wide adb forwarder, registry-backed, lazily created.

    Routes to the current agent via the registry (so it is NOT bound to one
    agent connection). Bind the OS listener with `await attach()` — idempotent,
    so a reconnecting / second agent never re-binds the port."""
    global _FORWARDER
    if _FORWARDER is None:
        from alb.remote.registry import get_agent_registry

        _FORWARDER = AdbForwarder(get_agent_registry().current_agent, port=forward_port())
    return _FORWARDER


async def shutdown_adb_forwarder() -> None:
    """Detach + drop the singleton (alb-api lifespan shutdown)."""
    global _FORWARDER
    if _FORWARDER is not None:
        await _FORWARDER.detach()
    _FORWARDER = None


def reset_adb_forwarder() -> None:
    """Sync best-effort reset for tests — closes the listener (skips the async
    wait_closed) and drops the singleton so the next test starts clean."""
    global _FORWARDER
    f = _FORWARDER
    _FORWARDER = None
    if f is not None and f._server is not None:
        f._server.close()
