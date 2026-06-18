"""OS-level loopback forwarders (ADR-051/052).

The keystone of the dial-home design: REAL OS-level listeners owned by the
alb-api process — 127.0.0.1:5037 for adb, 127.0.0.1:9001 for serial. Because
they are operating-system sockets (not in-loop objects), the separate alb-mcp /
CLI processes reach the device by plain connect() — alb's transports and MCP
tools are unchanged.

Each accepted local connection opens ONE data channel to the agent and shuttles
bytes. ADR-052 channel roles:
  - adb    = DAEMON  — proxies the adb server (a listen-socket daemon). Fail
             fast, NO retry; a reset is a real error (USB reauth / device drop /
             server crash). L-034.
  - serial = GATEWAY — proxies a per-connection exclusive bridge (the agent's
             COM port). The forwarder itself is still single-attempt; the
             bounded retry lives in alb's SerialTransport._open_tcp_with_retry,
             which reconnects to this listener.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import Callable
from typing import Any

from alb.remote.protocol import ADB_TARGET, ChannelRole, ChannelType
from alb.remote.registry import ChannelOpener, DataChannel

_log = logging.getLogger(__name__)

DEFAULT_ADB_HOST = "127.0.0.1"
DEFAULT_ADB_PORT = 5037
DEFAULT_SERIAL_HOST = "127.0.0.1"
DEFAULT_SERIAL_PORT = 9001  # matches infra.config.SerialConfig.default_tcp_port
DEFAULT_BAUD = 115200
DIAL_BACK_TIMEOUT_S = 10.0
_CHUNK = 65536


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
            _log.warning("%s channel open failed (no retry): %s", self._channel_type.value, e)
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


class SerialForwarder(ChannelForwarder):
    """Bridge local TCP (127.0.0.1:9001) <-> the agent's COM port (GATEWAY)."""

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
        },
    }
