"""Connected-agent registry + per-channel dial-back correlation (ADR-050/051).

The hub holds one AgentConnection per connected agent. When a forwarder needs
a data channel it asks the AgentConnection to open one: the connection
registers a pending future keyed by a fresh cid (with a per-channel secret —
DEBT-084) BEFORE sending `open_channel` on the signaling WS (so a fast dial-back
can never miss the cid — checklist #1), then awaits the dialed-back DataChannel.
The dial-back must present the secret, which only the owning agent received.

Registration uses a per-agent epoch so that a reconnect of the same agent_id
does NOT let a stale connection's teardown clobber the new one (checklist #4,
compare-and-clear).
"""

from __future__ import annotations

import asyncio
import hmac
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Protocol

from alb.remote import protocol
from alb.remote.protocol import ChannelRole, ChannelType


class DataChannel(Protocol):
    """A bidirectional byte channel (one dialed-back data connection)."""

    async def recv(self) -> bytes:  # b"" == EOF
        ...

    async def send(self, data: bytes) -> None: ...

    async def aclose(self) -> None: ...


class ChannelOpener(Protocol):
    """What a forwarder needs from an agent: open one data channel."""

    async def open_data_channel(
        self,
        *,
        ctype: ChannelType,
        role: ChannelRole,
        params: dict[str, Any],
        timeout: float,
    ) -> DataChannel: ...


SendControl = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class _Pending:
    """A pending data-channel dial-back: the future the forwarder awaits, plus
    the per-channel secret (DEBT-084) the dial-back must present to resolve it."""

    fut: asyncio.Future[DataChannel]
    csecret: str


class AgentConnection:
    """One connected agent. Owns the signaling-WS send callable + metadata.
    Pending-channel futures live in the registry (keyed by the global cid)."""

    def __init__(
        self,
        *,
        agent_id: str,
        name: str,
        version: int,
        caps: list[str],
        send_control: SendControl,
        registry: AgentRegistry,
    ) -> None:
        self.agent_id = agent_id
        self.name = name
        self.version = version
        self.caps = caps
        self._send_control = send_control
        self._registry = registry
        self.epoch = 0
        # set by the route once the forwarder is attached (for teardown)
        self.forwarder: Any = None
        # last-reported device enumeration (updated by the signaling recv loop
        # from adb_list / com_list replies). Surfaced via GET /agent/status.
        self.adb_devices: list[str] = []
        # adb-flavoured foreign processes the agent saw while its device list
        # was empty — the exclusive-USB-interface takeover signature.
        self.adb_conflicts: list[str] = []
        self.com_ports: list[dict[str, Any]] = []

    async def request_device_list(self) -> None:
        """Fire-and-forget: ask the agent to (re)report its devices. The replies
        (adb_list / com_list) land on the signaling WS and update the cache. The
        web Connection Center polls /agent/status, so the next poll reflects it."""
        with suppress(Exception):
            await self._send_control(protocol.list_adb())
            if "serial" in self.caps:
                await self._send_control(protocol.list_com())

    async def request_adb_restart(self, *, kill_conflicts: bool = False) -> None:
        """Fire-and-forget: ask the agent to restart ITS local adb server and
        re-report devices (the adb_list reply updates the cache; poll
        /agent/status for the result). Unsticks a wedged host-side adb
        enumeration without anyone touching the agent machine. kill_conflicts
        additionally clears adb-flavoured foreign processes holding the
        exclusive USB interface (the agent decides what matches)."""
        with suppress(Exception):
            await self._send_control(protocol.restart_adb(kill_conflicts=kill_conflicts))

    async def open_data_channel(
        self,
        *,
        ctype: ChannelType,
        role: ChannelRole,
        params: dict[str, Any],
        timeout: float,
    ) -> DataChannel:
        """Open one data channel and return the dialed-back DataChannel.

        Order is load-bearing (checklist #1): register the cid placeholder
        FIRST, send `open_channel` SECOND, so the agent's dial-back to
        /agent/channel?cid=<cid> always finds a waiting future."""
        cid = protocol.new_cid()
        csecret = protocol.new_csecret()
        fut = self._registry.register_pending(cid, csecret)
        try:
            await self._send_control(
                protocol.open_channel(
                    cid=cid, csecret=csecret, ctype=ctype, role=role, params=params
                )
            )
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._registry.discard_pending(cid)


class AgentRegistry:
    """Process-wide registry of connected agents + pending data channels.

    Runs entirely on the asyncio event loop. The `_lock` guards the agent
    map across the register/unregister critical sections; the pending map is
    touched only synchronously (no awaits between read and write) so it needs
    no lock.
    """

    def __init__(self) -> None:
        self._agents: dict[str, AgentConnection] = {}
        self._epoch: dict[str, int] = {}
        self._pending: dict[str, _Pending] = {}
        self._lock = asyncio.Lock()

    async def register(self, conn: AgentConnection) -> int:
        """Register (or replace) an agent. Returns this connection's epoch;
        teardown must pass it back to unregister() so a stale connection
        can't evict a newer one."""
        async with self._lock:
            epoch = self._epoch.get(conn.agent_id, 0) + 1
            self._epoch[conn.agent_id] = epoch
            conn.epoch = epoch
            self._agents[conn.agent_id] = conn
            return epoch

    async def unregister(self, agent_id: str, epoch: int) -> bool:
        """Compare-and-clear: only remove the agent if `epoch` is still the
        current one (else a reconnect already replaced it). Returns True if
        this call actually removed the entry."""
        async with self._lock:
            if self._epoch.get(agent_id) == epoch and agent_id in self._agents:
                del self._agents[agent_id]
                return True
            return False

    def get(self, agent_id: str) -> AgentConnection | None:
        return self._agents.get(agent_id)

    def current_agent(self) -> AgentConnection | None:
        """The agent to route device traffic to. P0 is single-agent, so this
        returns the most-recently-registered agent (or None). Multi-agent
        addressing — picking the agent by device — is DEBT-083 / P4."""
        if not self._agents:
            return None
        return max(self._agents.values(), key=lambda c: c.epoch)

    def list_agents(self) -> list[dict[str, Any]]:
        return [
            {
                "agent_id": c.agent_id,
                "name": c.name,
                "version": c.version,
                "caps": c.caps,
                "adb_devices": c.adb_devices,
                "adb_conflicts": c.adb_conflicts,
                "com_ports": c.com_ports,
            }
            for c in self._agents.values()
        ]

    async def request_device_refresh(self) -> None:
        """Fire-and-forget a device-list request to every connected agent."""
        for c in list(self._agents.values()):
            await c.request_device_list()

    @property
    def agent_count(self) -> int:
        return len(self._agents)

    # ── pending data-channel correlation (cid + per-channel secret) ──

    def register_pending(self, cid: str, csecret: str) -> asyncio.Future[DataChannel]:
        fut: asyncio.Future[DataChannel] = asyncio.get_running_loop().create_future()
        self._pending[cid] = _Pending(fut, csecret)
        return fut

    def resolve_pending(self, cid: str, channel: DataChannel, csecret: str | None) -> bool:
        """Bind a dialed-back DataChannel to its pending cid. Returns False if
        the cid is unknown/expired OR the presented per-channel secret does not
        match the one minted at open_channel (DEBT-084 — constant-time compare).
        The caller closes the data WS on False."""
        pending = self._pending.get(cid)
        if pending is None or pending.fut.done():
            return False
        if not csecret:
            return False
        # Compare as BYTES: hmac.compare_digest rejects non-ASCII str inputs with
        # TypeError, and csecret is attacker-controllable (dial-back query param).
        # Bytes have no such restriction, so a malformed secret reject cleanly
        # instead of throwing past the caller's ws.close(1008). utf-8 with
        # errors="replace" can never raise on the presented side.
        stored = pending.csecret.encode("utf-8")
        presented = csecret.encode("utf-8", "replace")
        if not hmac.compare_digest(stored, presented):
            return False
        pending.fut.set_result(channel)
        return True

    def discard_pending(self, cid: str) -> None:
        self._pending.pop(cid, None)

    @property
    def pending_count(self) -> int:
        return len(self._pending)


_REGISTRY: AgentRegistry | None = None


def get_agent_registry() -> AgentRegistry:
    """Return the process-wide AgentRegistry, lazily created."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = AgentRegistry()
    return _REGISTRY


def reset_agent_registry() -> None:
    """Drop the singleton — tests start with a fresh registry."""
    global _REGISTRY
    _REGISTRY = None
