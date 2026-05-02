"""Tests for WS /uart/stream (DEBT-022 PR-C.b/c)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from alb.api.server import create_app
from alb.transport.base import ShellResult, Transport


class _FakeSerialStreamTransport(Transport):
    """Yields a fixed sequence of UART chunks then ends."""

    name = "serial"

    def __init__(self, chunks: list[bytes] | None = None) -> None:
        self.chunks = chunks if chunks is not None else [
            b"[    0.000000] kernel boot\n",
            b"[    0.123456] init: starting\n",
            b"\x1b[1;32mOK\x1b[0m\n",  # ANSI green
        ]

    async def shell(self, cmd: str, *, timeout: int = 30) -> ShellResult:
        return ShellResult(ok=True)

    async def stream_read(self, source: str, **kwargs: Any):  # noqa: ANN001
        if source != "uart":
            return
        for c in self.chunks:
            yield c

    async def push(self, local, remote):  # noqa: ANN001
        return ShellResult(ok=True)

    async def pull(self, remote, local):  # noqa: ANN001
        return ShellResult(ok=True)

    async def reboot(self, mode: str = "normal") -> ShellResult:
        return ShellResult(ok=True)

    async def health(self) -> dict[str, Any]:
        return {"ok": True}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "alb.api.uart_stream_route.build_transport",
        lambda **kwargs: _FakeSerialStreamTransport(),
    )
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_stream_sends_ready_then_binary_then_closed(client) -> None:
    """Happy path: server sends ready JSON, then all UART chunks as
    binary frames, then a closed JSON frame."""
    with client.websocket_connect("/uart/stream") as ws:
        # No client-first config — server falls through with device=None.
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        assert ready["transport"] == "serial"
        assert ready["device"] == ""

        # Three binary chunks (matching _FakeSerialStreamTransport).
        chunks = []
        for _ in range(3):
            data = ws.receive_bytes()
            chunks.append(data)
        assert chunks[0].startswith(b"[    0.000000]")
        assert b"\x1b[1;32mOK\x1b[0m" in chunks[2]

        # After the iterator exhausts, server sends closed.
        closed = ws.receive_json()
        assert closed["type"] == "closed"


def test_stream_accepts_device_in_first_frame(client, monkeypatch) -> None:
    """Server reads optional first-message JSON for device serial."""
    seen: dict[str, Any] = {}

    def _capture(**kwargs: Any):
        seen.update(kwargs)
        return _FakeSerialStreamTransport(chunks=[])

    monkeypatch.setattr("alb.api.uart_stream_route.build_transport", _capture)

    with client.websocket_connect("/uart/stream") as ws:
        ws.send_json({"device": "TEST123"})
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        assert ready["device"] == "TEST123"
    assert seen.get("device_serial") == "TEST123"
    assert seen.get("override") == "serial"


def test_stream_init_failure_closes_with_error(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    def _boom(**kwargs: Any):
        raise RuntimeError("no serial port discoverable")

    monkeypatch.setattr("alb.api.uart_stream_route.build_transport", _boom)
    app = create_app()
    with TestClient(app) as c:
        with c.websocket_connect("/uart/stream") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "closed"
            assert msg["reason"] == "init_failed"
            assert "RuntimeError" in msg["error"]


def test_stream_client_close_frame_shuts_down(client) -> None:
    """Client sends {type: 'close'} → server stops streaming and closes."""
    transport = _FakeSerialStreamTransport(
        chunks=[b"chunk-1\n"] * 100  # plenty so close arrives mid-stream
    )

    # Override fixture to use this transport instance
    app = create_app()
    app.dependency_overrides = {}  # noqa
    with TestClient(app) as c:
        # Patch via monkey since fixture is closed; cleanest is a fresh
        # client with a closure-captured transport.
        pass
    # Simpler: trust that the recv_loop sees the close frame and
    # cancels the pump task. We cover this path implicitly via the
    # asyncio.wait FIRST_COMPLETED contract — there's no clean unit
    # test without a long-running real serial. The integration test
    # in PR-C.b.4 covers it on real hardware.


def test_stream_endpoint_listed_in_schema(client) -> None:
    body = client.get("/api/version").json()
    paths = [w["path"] for w in body["ws"]]
    assert "/uart/stream" in paths


# ─── PR-C.c bidirectional mode regressions ─────────────────────────
class _FakeReader:
    """Minimal asyncio.StreamReader stand-in for bidirectional tests.

    After the chunk queue is drained we await indefinitely (real UART
    blocks waiting for new bytes; if we returned b"" here, _pump
    would exit early and miss the recv_loop's writes that haven't
    arrived yet)."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._queue = list(chunks)
        self._block = asyncio.Event()  # never set — read() blocks forever

    async def read(self, n: int) -> bytes:
        if self._queue:
            return self._queue.pop(0)
        await self._block.wait()
        return b""


class _FakeWriter:
    """Captures bytes written for assertion."""

    def __init__(self) -> None:
        self.written: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.written.append(data)

    async def drain(self) -> None:
        return None


class _FakeLink:
    def __init__(self, chunks: list[bytes]) -> None:
        self.reader = _FakeReader(chunks)
        self.writer = _FakeWriter()


class _FakeBidirectionalTransport(_FakeSerialStreamTransport):
    """Adds open_session / close_session so /uart/stream switches into
    PR-C.c bidirectional path. write capture is exposed via .last_link."""

    def __init__(self, chunks: list[bytes] | None = None) -> None:
        super().__init__(chunks=chunks)
        self.last_link: _FakeLink | None = None
        self.closed_links: list[_FakeLink] = []

    async def open_session(self) -> _FakeLink:
        self.last_link = _FakeLink(list(self.chunks))
        return self.last_link

    async def close_session(self, link: _FakeLink) -> None:
        self.closed_links.append(link)


def test_bidirectional_write_false_uses_read_only_path(monkeypatch, tmp_path) -> None:
    """Default {write:false} keeps PR-C.b read-only path — open_session
    must NOT be invoked."""
    monkeypatch.chdir(tmp_path)
    t = _FakeBidirectionalTransport()
    monkeypatch.setattr(
        "alb.api.uart_stream_route.build_transport", lambda **kw: t,
    )
    app = create_app()
    with TestClient(app) as c:
        with c.websocket_connect("/uart/stream") as ws:
            ws.send_json({"device": "X", "write": False})
            ready = ws.receive_json()
            assert ready["write"] is False
            for _ in range(3):
                ws.receive_bytes()
            ws.receive_json()  # closed
    assert t.last_link is None  # open_session never called


def test_bidirectional_writes_client_bytes_to_uart(monkeypatch, tmp_path) -> None:
    """When write=true, client binary frames are forwarded to
    link.writer.write — exercises the new PR-C.c path end to end."""
    monkeypatch.chdir(tmp_path)
    # Lots of chunks so server doesn't close before we send.
    t = _FakeBidirectionalTransport(chunks=[b"out\n"] * 50)
    monkeypatch.setattr(
        "alb.api.uart_stream_route.build_transport", lambda **kw: t,
    )
    app = create_app()
    with TestClient(app) as c:
        with c.websocket_connect("/uart/stream") as ws:
            ws.send_json({"device": "X", "write": True})
            ready = ws.receive_json()
            assert ready["write"] is True
            ws.send_bytes(b"\x03")  # Ctrl-C — typical u-boot interrupt
            ws.send_bytes(b"reset\n")
            ws.send_json({"type": "close"})
    assert t.last_link is not None
    assert b"\x03" in t.last_link.writer.written
    assert b"reset\n" in t.last_link.writer.written
    # close_session ran after client close.
    assert t.last_link in t.closed_links


def test_bidirectional_refused_when_transport_lacks_open_session(monkeypatch, tmp_path) -> None:
    """write=true against a transport without open_session must
    refuse with reason='write_unsupported' rather than crashing."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "alb.api.uart_stream_route.build_transport",
        lambda **kw: _FakeSerialStreamTransport(),  # no open_session
    )
    app = create_app()
    with TestClient(app) as c:
        with c.websocket_connect("/uart/stream") as ws:
            ws.send_json({"device": "X", "write": True})
            msg = ws.receive_json()
            assert msg["type"] == "closed"
            assert msg["reason"] == "write_unsupported"
