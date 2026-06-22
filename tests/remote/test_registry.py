"""AgentRegistry + dial-back correlation tests (ADR-050/051)."""

from __future__ import annotations

from typing import Any

from alb.remote.protocol import ADB_TARGET, ChannelRole, ChannelType
from alb.remote.registry import (
    AgentConnection,
    AgentRegistry,
    get_agent_registry,
    reset_agent_registry,
)


class _DummyChannel:
    async def recv(self) -> bytes:
        return b""

    async def send(self, data: bytes) -> None:
        return None

    async def aclose(self) -> None:
        return None


def _conn(reg: AgentRegistry, agent_id: str, send=None) -> AgentConnection:
    async def _noop(_m: dict[str, Any]) -> None:
        return None

    return AgentConnection(
        agent_id=agent_id,
        name=agent_id,
        version=1,
        caps=[],
        send_control=send or _noop,
        registry=reg,
    )


async def test_register_returns_increasing_epoch_per_agent():
    reg = AgentRegistry()
    e1 = await reg.register(_conn(reg, "a"))
    e2 = await reg.register(_conn(reg, "a"))  # same id reconnect
    assert e2 > e1
    assert reg.agent_count == 1


async def test_unregister_compare_and_clear():
    reg = AgentRegistry()
    c1 = _conn(reg, "a")
    e1 = await reg.register(c1)
    # a newer connection replaces it
    await reg.register(_conn(reg, "a"))
    # stale teardown with the OLD epoch must NOT evict the new connection
    removed = await reg.unregister("a", e1)
    assert removed is False
    assert reg.agent_count == 1


async def test_unregister_current_epoch_removes():
    reg = AgentRegistry()
    e = await reg.register(_conn(reg, "a"))
    assert await reg.unregister("a", e) is True
    assert reg.agent_count == 0
    assert reg.get("a") is None


async def test_pending_resolves_future():
    reg = AgentRegistry()
    fut = reg.register_pending("cid1", "sec1")
    assert reg.pending_count == 1
    ch = _DummyChannel()
    assert reg.resolve_pending("cid1", ch, "sec1") is True
    assert fut.done() and fut.result() is ch
    reg.discard_pending("cid1")
    assert reg.pending_count == 0


async def test_resolve_unknown_cid_is_false():
    reg = AgentRegistry()
    assert reg.resolve_pending("nope", _DummyChannel(), "sec") is False


async def test_resolve_wrong_csecret_is_false():
    """DEBT-084: a dial-back with the right cid but the wrong per-channel secret
    must NOT resolve the channel, and must leave the future open for the real
    dial-back."""
    reg = AgentRegistry()
    fut = reg.register_pending("cid1", "right")
    assert reg.resolve_pending("cid1", _DummyChannel(), "wrong") is False
    assert not fut.done()
    # the legitimate dial-back (correct secret) still resolves it
    ch = _DummyChannel()
    assert reg.resolve_pending("cid1", ch, "right") is True
    assert fut.result() is ch


async def test_resolve_missing_csecret_is_false():
    reg = AgentRegistry()
    fut = reg.register_pending("cid1", "right")
    assert reg.resolve_pending("cid1", _DummyChannel(), None) is False
    assert reg.resolve_pending("cid1", _DummyChannel(), "") is False
    assert not fut.done()


async def test_resolve_non_ascii_csecret_rejects_without_raising():
    """A non-ASCII presented secret must reject cleanly, NOT raise TypeError out
    of resolve_pending (hmac.compare_digest forbids non-ASCII str; we compare as
    bytes). The future stays open for the legitimate dial-back."""
    reg = AgentRegistry()
    fut = reg.register_pending("cid1", "right")
    assert reg.resolve_pending("cid1", _DummyChannel(), "café") is False
    assert reg.resolve_pending("cid1", _DummyChannel(), "日本語") is False
    assert not fut.done()
    # the real secret still resolves
    ch = _DummyChannel()
    assert reg.resolve_pending("cid1", ch, "right") is True
    assert fut.result() is ch


async def test_open_data_channel_registers_cid_before_signaling():
    """Checklist #1: the cid placeholder MUST be registered before
    open_channel is sent, so a dial-back that races the await still finds it.
    We simulate an instant dial-back inside send_control."""
    reg = AgentRegistry()
    seen: dict[str, Any] = {}

    async def send_control(m: dict[str, Any]) -> None:
        cid = m["cid"]
        # at this point the pending future MUST already exist, and the frame
        # MUST carry the per-channel secret the dial-back has to present
        assert m["csecret"]
        seen["resolved"] = reg.resolve_pending(cid, _DummyChannel(), m["csecret"])

    conn = _conn(reg, "a", send=send_control)
    dc = await conn.open_data_channel(
        ctype=ChannelType.TCP,
        role=ChannelRole.DAEMON,
        params={"target": ADB_TARGET},
        timeout=2.0,
    )
    assert isinstance(dc, _DummyChannel)
    assert seen["resolved"] is True
    assert reg.pending_count == 0  # discarded in finally


def test_singleton_reset():
    r1 = get_agent_registry()
    reset_agent_registry()
    r2 = get_agent_registry()
    assert r1 is not r2


# ── device enumeration cache (P4-deferred → enumeration increment) ────


async def test_request_device_list_adb_only_without_serial_cap():
    reg = AgentRegistry()
    sent: list[str] = []

    async def send(m: dict[str, Any]) -> None:
        sent.append(m["verb"])

    conn = AgentConnection(
        agent_id="a",
        name="a",
        version=1,
        caps=["adb"],
        send_control=send,
        registry=reg,
    )
    await conn.request_device_list()
    assert "list_adb" in sent
    assert "list_com" not in sent  # no serial cap → don't ask for COM ports


async def test_request_device_list_includes_com_with_serial_cap():
    reg = AgentRegistry()
    sent: list[str] = []

    async def send(m: dict[str, Any]) -> None:
        sent.append(m["verb"])

    conn = AgentConnection(
        agent_id="a",
        name="a",
        version=1,
        caps=["adb", "serial"],
        send_control=send,
        registry=reg,
    )
    await conn.request_device_list()
    assert sent.count("list_adb") == 1
    assert sent.count("list_com") == 1


async def test_list_agents_includes_device_cache():
    reg = AgentRegistry()
    conn = _conn(reg, "a")
    conn.adb_devices = ["serial-1"]
    conn.com_ports = [{"port": "COM3", "desc": "USB serial"}]
    await reg.register(conn)
    a = reg.list_agents()[0]
    assert a["adb_devices"] == ["serial-1"]
    assert a["com_ports"] == [{"port": "COM3", "desc": "USB serial"}]
