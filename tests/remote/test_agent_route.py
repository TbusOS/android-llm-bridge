"""/agent/connect + /agent/channel handshake & auth tests (ADR-050/051).

Uses Starlette's in-process TestClient WebSocket (no real network, no
`websockets` dependency). The adb forwarder is pointed at an ephemeral port
(ALB_ADB_FORWARD_PORT=0) so binding 5037 never clashes during tests.
"""

from __future__ import annotations

import asyncio

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
                ws.receive_text()  # blocks until server drops us on timeout
        # teardown ran in the finally → no lingering agent
        assert get_agent_registry().agent_count == 0
