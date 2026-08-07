"""/agent/connect + /agent/channel handshake & auth tests (ADR-050/051).

Uses Starlette's in-process TestClient WebSocket (no real network, no
`websockets` dependency). The adb forwarder is pointed at an ephemeral port
(ALB_ADB_FORWARD_PORT=0) so binding 5037 never clashes during tests.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from alb.api.agent_route import _WsDataChannel
from alb.api.server import create_app
from alb.remote import protocol as p


class _FakeWs:
    def __init__(self, frames: list[dict]) -> None:
        self._frames = list(frames)
        self.sent: list[bytes] = []

    async def receive(self) -> dict:
        if self._frames:
            return self._frames.pop(0)
        return {"type": "websocket.disconnect"}

    async def send_bytes(self, data: bytes) -> None:
        self.sent.append(data)


async def test_wsdatachannel_recv_send_close():
    ws = _FakeWs([{"bytes": b"abc"}, {"text": "ctrl"}])
    ch = _WsDataChannel(ws)
    assert await ch.recv() == b"abc"  # binary frame passes through
    assert await ch.recv() == b""  # a text frame on a byte channel = EOF
    assert await ch.recv() == b""  # disconnect = EOF
    await ch.send(b"out")
    assert ws.sent == [b"out"]
    waiter = asyncio.create_task(ch.wait_closed())
    await ch.aclose()
    await asyncio.wait_for(waiter, timeout=1.0)  # aclose unblocks wait_closed


@pytest.fixture(autouse=True)
def _ephemeral_forward_port(monkeypatch):
    monkeypatch.setenv("ALB_ADB_FORWARD_PORT", "0")


def _hello(token: str | None) -> str:
    return p.encode_control(
        p.hello(agent_id="agent-1", name="t", version=1, caps=["adb"], token=token)
    )


def test_hello_gets_hello_ok_when_no_token_configured(monkeypatch):
    monkeypatch.delenv("ALB_AGENT_TOKEN", raising=False)
    with TestClient(create_app()) as c:
        with c.websocket_connect("/agent/connect") as ws:
            ws.send_text(_hello(None))
            reply = p.decode_control(ws.receive_text())
            assert reply["verb"] == p.Verb.HELLO_OK.value


def test_correct_token_accepted(monkeypatch):
    monkeypatch.setenv("ALB_AGENT_TOKEN", "s3cret")
    with TestClient(create_app()) as c:
        with c.websocket_connect("/agent/connect") as ws:
            ws.send_text(_hello("s3cret"))
            reply = p.decode_control(ws.receive_text())
            assert reply["verb"] == p.Verb.HELLO_OK.value


def test_bad_token_rejected(monkeypatch):
    monkeypatch.setenv("ALB_AGENT_TOKEN", "s3cret")
    with TestClient(create_app()) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/agent/connect") as ws:
                ws.send_text(_hello("wrong"))
                ws.receive_text()


def test_first_frame_must_be_hello(monkeypatch):
    monkeypatch.delenv("ALB_AGENT_TOKEN", raising=False)
    with TestClient(create_app()) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/agent/connect") as ws:
                ws.send_text(p.encode_control(p.heartbeat()))  # not hello
                ws.receive_text()


def test_channel_unknown_cid_rejected(monkeypatch):
    monkeypatch.delenv("ALB_AGENT_TOKEN", raising=False)
    with TestClient(create_app()) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/agent/channel?cid=does-not-exist") as ws:
                ws.receive_bytes()


def test_channel_requires_cid(monkeypatch):
    monkeypatch.delenv("ALB_AGENT_TOKEN", raising=False)
    with TestClient(create_app()) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/agent/channel") as ws:
                ws.receive_bytes()


def test_channel_forwards_csecret_to_resolve(monkeypatch):
    """DEBT-084: the data-WS route reads ?csecret= and passes it to
    resolve_pending, which authenticates the dial-back against the channel's
    minted secret. We capture the args and reject so the WS closes."""
    monkeypatch.delenv("ALB_AGENT_TOKEN", raising=False)
    from alb.remote.registry import AgentRegistry

    captured: dict = {}

    def fake_resolve(self, cid, channel, csecret):
        captured["cid"] = cid
        captured["csecret"] = csecret
        return False  # reject → route closes the WS → WebSocketDisconnect

    monkeypatch.setattr(AgentRegistry, "resolve_pending", fake_resolve)
    with TestClient(create_app()) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/agent/channel?cid=abc&csecret=xyz") as ws:
                ws.receive_bytes()
    assert captured == {"cid": "abc", "csecret": "xyz"}


def _capture_resolve(monkeypatch) -> dict:
    from alb.remote.registry import AgentRegistry

    captured: dict = {}

    def fake_resolve(self, cid, channel, csecret):
        captured["cid"] = cid
        captured["csecret"] = csecret
        return False  # reject → route closes the WS → WebSocketDisconnect

    monkeypatch.setattr(AgentRegistry, "resolve_pending", fake_resolve)
    return captured


def test_channel_reads_csecret_from_header(monkeypatch):
    """ADR-055: the dial-back secret rides a header so it never lands in the
    access log's request line."""
    monkeypatch.delenv("ALB_AGENT_TOKEN", raising=False)
    captured = _capture_resolve(monkeypatch)
    with TestClient(create_app()) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect(
                "/agent/channel?cid=abc", headers={p.CSECRET_HEADER: "xyz"}
            ) as ws:
                ws.receive_bytes()
    assert captured == {"cid": "abc", "csecret": "xyz"}


def test_channel_token_from_header_accepted(monkeypatch):
    monkeypatch.setenv("ALB_AGENT_TOKEN", "s3cret")
    captured = _capture_resolve(monkeypatch)
    with TestClient(create_app()) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect(
                "/agent/channel?cid=abc",
                headers={p.TOKEN_HEADER: "s3cret", p.CSECRET_HEADER: "xyz"},
            ) as ws:
                ws.receive_bytes()
    # got past the token gate — a rejected token never reaches resolve_pending
    assert captured == {"cid": "abc", "csecret": "xyz"}


def test_channel_bad_token_header_rejected(monkeypatch):
    monkeypatch.setenv("ALB_AGENT_TOKEN", "s3cret")
    captured = _capture_resolve(monkeypatch)
    with TestClient(create_app()) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect(
                "/agent/channel?cid=abc", headers={p.TOKEN_HEADER: "wrong"}
            ) as ws:
                ws.receive_bytes()
    assert captured == {}  # closed before the registry was consulted


def test_channel_query_form_still_accepted(monkeypatch):
    """Back-compat that is load-bearing: the agent lives on an operator's
    machine and is redeployed by hand, so a hub upgrade must not lock out a
    working agent that still sends credentials in the query string."""
    monkeypatch.setenv("ALB_AGENT_TOKEN", "s3cret")
    captured = _capture_resolve(monkeypatch)
    with TestClient(create_app()) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/agent/channel?cid=abc&token=s3cret&csecret=xyz") as ws:
                ws.receive_bytes()
    assert captured == {"cid": "abc", "csecret": "xyz"}


def test_header_form_ignores_stale_query_creds(monkeypatch):
    """A dial-back that presents headers is a new-style agent; a query param
    must not be able to override what it sent."""
    monkeypatch.delenv("ALB_AGENT_TOKEN", raising=False)
    captured = _capture_resolve(monkeypatch)
    with TestClient(create_app()) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect(
                "/agent/channel?cid=abc&csecret=from-query",
                headers={p.CSECRET_HEADER: "from-header"},
            ) as ws:
                ws.receive_bytes()
    assert captured["csecret"] == "from-header"


def test_agent_survives_forwarder_bind_failure(monkeypatch):
    """ADR-051 hub-side robustness: if the adb forwarder can't bind its port
    (e.g. a local adb server already holds 5037), the agent session must STILL
    complete the handshake (hello_ok) and register — not get torn down before
    hello_ok and trapped in a reconnect loop."""
    monkeypatch.delenv("ALB_AGENT_TOKEN", raising=False)
    from alb.remote.registry import get_agent_registry

    class _BoomForwarder:
        async def attach(self):
            raise OSError("[Errno 98] address already in use")

    monkeypatch.setattr("alb.api.agent_route.get_adb_forwarder", lambda: _BoomForwarder())

    with TestClient(create_app()) as c:
        with c.websocket_connect("/agent/connect") as ws:
            ws.send_text(_hello(None))
            # handshake still succeeds despite the bind failure
            assert p.decode_control(ws.receive_text())["verb"] == p.Verb.HELLO_OK.value
            assert get_agent_registry().agent_count == 1
            # ...and the failure stays observable: the forwarder reports unbound
            from alb.remote.forwarder import forwarder_status

            assert forwarder_status()["adb"]["bound"] is False


def test_heartbeat_timeout_tears_down(monkeypatch):
    """After hello, an agent that never sends a heartbeat is dropped once
    HEARTBEAT_TIMEOUT_S elapses — and the registry is cleaned up (finally)."""
    monkeypatch.delenv("ALB_AGENT_TOKEN", raising=False)
    monkeypatch.setattr("alb.api.agent_route.HEARTBEAT_TIMEOUT_S", 0.3)
    from alb.remote.registry import get_agent_registry

    with TestClient(create_app()) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/agent/connect") as ws:
                ws.send_text(_hello(None))
                assert p.decode_control(ws.receive_text())["verb"] == p.Verb.HELLO_OK.value
                # drain any control frames the hub sends (e.g. the list_adb
                # device-list request) until it drops us on the heartbeat timeout
                while True:
                    ws.receive_text()
        # teardown ran in the finally → no lingering agent
        assert get_agent_registry().agent_count == 0


# ── GET /agent/status (P2 Connection Center backend) ─────────────────


def test_agent_status_empty(monkeypatch):
    monkeypatch.delenv("ALB_AGENT_TOKEN", raising=False)
    with TestClient(create_app()) as c:
        body = c.get("/agent/status").json()
    assert body["agents"] == []
    assert body["forwarders"]["adb"]["bound"] is False
    assert "serial" in body["forwarders"]
    assert body["forwarders"]["serial"]["configured"] is False


def test_agent_status_shows_connected_agent(monkeypatch):
    monkeypatch.delenv("ALB_AGENT_TOKEN", raising=False)
    with TestClient(create_app()) as c:
        with c.websocket_connect("/agent/connect") as ws:
            ws.send_text(_hello(None))
            assert p.decode_control(ws.receive_text())["verb"] == p.Verb.HELLO_OK.value
            body = c.get("/agent/status").json()
    assert len(body["agents"]) == 1
    assert body["agents"][0]["agent_id"] == "agent-1"
    assert body["agents"][0]["current"] is True
    assert body["forwarders"]["adb"]["bound"] is True


def test_agent_status_serial_configured(monkeypatch):
    monkeypatch.delenv("ALB_AGENT_TOKEN", raising=False)
    monkeypatch.setenv("ALB_AGENT_SERIAL_COM", "COM5")
    monkeypatch.setenv("ALB_AGENT_SERIAL_BAUD", "115200")
    with TestClient(create_app()) as c:
        body = c.get("/agent/status").json()
    assert body["forwarders"]["serial"]["configured"] is True
    assert body["forwarders"]["serial"]["com"] == "COM5"
    assert body["forwarders"]["serial"]["baud"] == 115200


# ── device enumeration (_apply_agent_frame + /agent/status) ───────────


def test_apply_agent_frame_updates_device_cache():
    from alb.api.agent_route import _apply_agent_frame
    from alb.remote.registry import AgentConnection, AgentRegistry

    reg = AgentRegistry()

    async def _send(_m: dict) -> None:
        return None

    conn = AgentConnection(
        agent_id="a",
        name="a",
        version=1,
        caps=["adb", "serial"],
        send_control=_send,
        registry=reg,
    )
    _apply_agent_frame(conn, {"verb": "adb_list", "devices": ["s1", "s2"]})
    assert conn.adb_devices == ["s1", "s2"]
    _apply_agent_frame(conn, {"verb": "com_list", "ports": [{"port": "COM3"}]})
    assert conn.com_ports == [{"port": "COM3"}]
    # an advisory verb leaves the cache untouched
    _apply_agent_frame(conn, {"verb": "heartbeat"})
    assert conn.adb_devices == ["s1", "s2"]


def test_agent_status_exposes_device_keys(monkeypatch):
    monkeypatch.delenv("ALB_AGENT_TOKEN", raising=False)
    with TestClient(create_app()) as c:
        with c.websocket_connect("/agent/connect") as ws:
            ws.send_text(_hello(None))
            assert p.decode_control(ws.receive_text())["verb"] == p.Verb.HELLO_OK.value
            body = c.get("/agent/status").json()
    agent = body["agents"][0]
    assert "adb_devices" in agent
    assert "com_ports" in agent


# ── POST /api/agent/adb/restart ────────────────────────────────────────


def test_adb_restart_sends_verb_to_current_agent(monkeypatch):
    monkeypatch.delenv("ALB_AGENT_TOKEN", raising=False)
    with TestClient(create_app()) as c:
        with c.websocket_connect("/agent/connect") as ws:
            ws.send_text(_hello(None))
            assert p.decode_control(ws.receive_text())["verb"] == p.Verb.HELLO_OK.value
            # register() fires a device refresh — drain it (caps=["adb"] → list_adb only)
            assert p.decode_control(ws.receive_text())["verb"] == p.Verb.LIST_ADB.value
            body = c.post("/api/agent/adb/restart").json()
            assert body["ok"] is True
            assert body["agent_id"] == "agent-1"
            # the agent receives the restart request on the signaling WS
            assert p.decode_control(ws.receive_text())["verb"] == p.Verb.RESTART_ADB.value


def test_adb_restart_409_when_no_agent(monkeypatch):
    monkeypatch.delenv("ALB_AGENT_TOKEN", raising=False)
    with TestClient(create_app()) as c:
        r = c.post("/api/agent/adb/restart")
    assert r.status_code == 409


def test_adb_restart_kill_conflicts_flag_in_frame(monkeypatch):
    monkeypatch.delenv("ALB_AGENT_TOKEN", raising=False)
    with TestClient(create_app()) as c:
        with c.websocket_connect("/agent/connect") as ws:
            ws.send_text(_hello(None))
            assert p.decode_control(ws.receive_text())["verb"] == p.Verb.HELLO_OK.value
            assert p.decode_control(ws.receive_text())["verb"] == p.Verb.LIST_ADB.value
            body = c.post("/api/agent/adb/restart?kill_conflicts=true").json()
            assert body["kill_conflicts"] is True
            frame = p.decode_control(ws.receive_text())
            assert frame["verb"] == p.Verb.RESTART_ADB.value
            assert frame["kill_conflicts"] is True


def test_apply_agent_frame_stores_and_clears_adb_conflicts():
    from alb.api.agent_route import _apply_agent_frame
    from alb.remote.registry import AgentConnection, AgentRegistry

    async def _send(_m: dict) -> None:
        return None

    conn = AgentConnection(
        agent_id="a",
        name="a",
        version=1,
        caps=["adb"],
        send_control=_send,
        registry=AgentRegistry(),
    )
    _apply_agent_frame(conn, {"verb": "adb_list", "devices": [], "conflicts": ["x_adb pid=9"]})
    assert conn.adb_conflicts == ["x_adb pid=9"]
    # a frame from an agent that predates the field clears the stale suspects
    _apply_agent_frame(conn, {"verb": "adb_list", "devices": ["s1"]})
    assert conn.adb_conflicts == []


def test_channel_error_frame_is_dispatched_to_fail_pending(monkeypatch):
    """issue #4: `channel_error` must wake the waiting forwarder immediately
    with the agent's reason, instead of leaving it to time out on a dial-back
    that will never come. The session must survive the frame."""
    monkeypatch.delenv("ALB_AGENT_TOKEN", raising=False)
    from alb.remote.registry import AgentRegistry

    seen: list[tuple[str, str]] = []

    def _spy(self, cid: str, reason: str) -> bool:
        seen.append((cid, reason))
        return True

    monkeypatch.setattr(AgentRegistry, "fail_pending", _spy)

    with TestClient(create_app()) as c:
        with c.websocket_connect("/agent/connect") as ws:
            ws.send_text(_hello(None))
            assert p.decode_control(ws.receive_text())["verb"] == p.Verb.HELLO_OK.value
            assert p.decode_control(ws.receive_text())["verb"] == p.Verb.LIST_ADB.value

            ws.send_text(
                p.encode_control(
                    p.channel_error(cid="cid-x", reason="cannot open COM4: Access is denied")
                )
            )
            # the route runs on the server's own loop — poll until it lands
            for _ in range(200):
                if seen:
                    break
                time.sleep(0.01)
            assert seen == [("cid-x", "cannot open COM4: Access is denied")]
            # an unopenable channel is not a session-level failure
            assert len(c.get("/agent/status").json()["agents"]) == 1
