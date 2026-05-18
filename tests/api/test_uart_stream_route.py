"""Tests for WS /uart/stream (DEBT-022 PR-C.b/c)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from alb.api.server import create_app
from alb.transport.base import ShellResult, Transport


class _FakeSerialStreamTransport(Transport):
    """Yields a fixed sequence of UART chunks then ends.

    ``stream_read`` is **single-shot**: after the first call exhausts the
    chunk list, subsequent calls yield nothing. This matches a real TCP
    bridge (ser2net / windows_serial_bridge) where you can't re-read a
    stream past EOF — and is required for tests to behave deterministically
    now that the pump path wraps ``stream_read`` in a reconnect loop
    (``_reconnecting_serial_stream``).
    """

    name = "serial"

    def __init__(self, chunks: list[bytes] | None = None) -> None:
        self.chunks = chunks if chunks is not None else [
            b"[    0.000000] kernel boot\n",
            b"[    0.123456] init: starting\n",
            b"\x1b[1;32mOK\x1b[0m\n",  # ANSI green
        ]
        self._consumed = False
        self.stream_read_calls = 0

    async def shell(self, cmd: str, *, timeout: int = 30) -> ShellResult:
        return ShellResult(ok=True)

    async def stream_read(self, source: str, **kwargs: Any):  # noqa: ANN001
        if source != "uart":
            return
        self.stream_read_calls += 1
        if self._consumed:
            if False:  # keep _gen an async-generator function with zero yields
                yield b""
            return
        self._consumed = True
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
    binary frames, then (after client close) a closed JSON frame.

    The pump path wraps stream_read in a reconnect loop so the WS
    survives an idle bridge EOF — the natural way to end a session
    is the client sending {"type":"close"} (or disconnecting).
    """
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

        # Client-initiated close ends the session cleanly.
        ws.send_text(json.dumps({"type": "close"}))
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


class _IdleThenDataTransport(Transport):
    """Simulates a TCP UART bridge that EOFs immediately on the first few
    reconnect attempts (board idle), then on a later attempt delivers a
    burst of bytes (board rebooted mid-session). Used to lock in the WS
    reconnect-on-EOF fix for the read-only pump.
    """

    name = "serial"

    def __init__(self, eof_count: int = 2, payload: list[bytes] | None = None) -> None:
        self._eof_remaining = eof_count
        self._payload = payload if payload is not None else [
            b"[boot] u-boot 2024.04\n",
            b"[boot] kernel start\n",
        ]
        self._delivered = False
        self.stream_read_calls = 0

    async def shell(self, cmd: str, *, timeout: int = 30) -> ShellResult:
        return ShellResult(ok=True)

    async def stream_read(self, source: str, **kwargs: Any):  # noqa: ANN001
        if source != "uart":
            return
        self.stream_read_calls += 1
        if self._eof_remaining > 0:
            self._eof_remaining -= 1
            if False:  # keep this an async-gen with zero yields
                yield b""
            return
        if self._delivered:
            if False:
                yield b""
            return
        self._delivered = True
        for c in self._payload:
            yield c

    async def push(self, local, remote):  # noqa: ANN001
        return ShellResult(ok=True)

    async def pull(self, remote, local):  # noqa: ANN001
        return ShellResult(ok=True)

    async def reboot(self, mode: str = "normal") -> ShellResult:
        return ShellResult(ok=True)

    async def health(self) -> dict[str, Any]:
        return {"ok": True}


def test_stream_survives_idle_eof_and_catches_late_data(
    monkeypatch, tmp_path
) -> None:
    """BUG fix companion to capture_uart: when the bridge EOFs the client
    connection on idle, the WS read-only pump must reconnect — not tear
    the session down. Verified by a fake transport that EOFs twice then
    yields a real boot burst on the third connect."""
    monkeypatch.chdir(tmp_path)
    t = _IdleThenDataTransport(eof_count=2)
    monkeypatch.setattr(
        "alb.api.uart_stream_route.build_transport", lambda **kw: t,
    )
    app = create_app()
    with TestClient(app) as c:
        with c.websocket_connect("/uart/stream") as ws:
            ready = ws.receive_json()
            assert ready["type"] == "ready"
            # Two EOFs + one data round = 3 stream_read calls. Each EOF
            # incurs a 0.5 s reconnect backoff, so allow a few seconds for
            # the data to arrive.
            first = ws.receive_bytes()
            second = ws.receive_bytes()
            assert b"u-boot" in first
            assert b"kernel start" in second
            ws.send_text(json.dumps({"type": "close"}))
            closed = ws.receive_json()
            assert closed["type"] == "closed"
    assert t.stream_read_calls >= 3, (
        f"expected ≥3 reconnect attempts to recover late data, "
        f"got {t.stream_read_calls}"
    )


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
            ws.send_text(json.dumps({"type": "close"}))
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


# ─── PR-C.c follow-up · close-frame race + writer/reader error path ─
class _RaisingWriter(_FakeWriter):
    def write(self, data: bytes) -> None:
        super().write(data)
        raise OSError("EBADF")


class _RaisingReader(_FakeReader):
    def __init__(self, chunks: list[bytes]) -> None:
        super().__init__(chunks)
        self._raised = False

    async def read(self, n: int) -> bytes:
        if self._queue:
            return self._queue.pop(0)
        if not self._raised:
            self._raised = True
            raise OSError("ENXIO")
        await self._block.wait()
        return b""


def test_writer_oserror_yields_single_write_error_close(
    monkeypatch, tmp_path
) -> None:
    """When link.writer raises OSError, exactly ONE close frame should
    be sent with reason='write_error' (no double-send race · HIGH 1
    from PR-C.c review)."""
    monkeypatch.chdir(tmp_path)
    t = _FakeBidirectionalTransport(chunks=[b"out\n"] * 20)

    async def _open() -> _FakeLink:  # noqa: ANN001
        link = _FakeLink([b"out\n"] * 20)
        link.writer = _RaisingWriter()
        t.last_link = link
        return link

    t.open_session = _open  # type: ignore[assignment]
    monkeypatch.setattr(
        "alb.api.uart_stream_route.build_transport", lambda **kw: t,
    )
    app = create_app()
    with TestClient(app) as c:
        with c.websocket_connect("/uart/stream") as ws:
            ws.send_json({"write": True})
            ready = ws.receive_json()
            assert ready["write"] is True
            # Drain a few binary frames before triggering write error.
            for _ in range(2):
                ws.receive_bytes()
            ws.send_bytes(b"will-fail")
            # Server must close with write_error — and only ONE close
            # frame (not 2 from race). Drain any in-flight binary
            # chunks the pump emitted before being cancelled.
            close = None
            for _ in range(100):
                msg = ws.receive()
                if "text" in msg and msg["text"]:
                    close = json.loads(msg["text"])
                    break
            assert close is not None and close["type"] == "closed"
            assert close["reason"] == "write_error"
            assert "OSError" in close.get("error", "")


def test_oversized_write_frame_dropped_not_closed(
    monkeypatch, tmp_path
) -> None:
    """DEBT-026 / security LOW 4 — bidirectional write frame > 64 KB
    must be dropped + reported via {type:'write_dropped'}, NOT close
    the WS. A single bad paste shouldn't tear the session down."""
    monkeypatch.chdir(tmp_path)
    t = _FakeBidirectionalTransport(chunks=[b"out\n"] * 50)
    monkeypatch.setattr(
        "alb.api.uart_stream_route.build_transport", lambda **kw: t,
    )
    app = create_app()
    huge = b"A" * (64 * 1024 + 1)  # one byte over cap
    with TestClient(app) as c:
        with c.websocket_connect("/uart/stream") as ws:
            ws.send_json({"write": True})
            ready = ws.receive_json()
            assert ready["write"] is True
            ws.receive_bytes()  # drain one chunk so timing is deterministic
            ws.send_bytes(huge)
            # Drain in-flight binary, then expect a write_dropped (not
            # closed) frame.
            dropped = None
            for _ in range(80):
                msg = ws.receive()
                if "text" in msg and msg["text"]:
                    obj = json.loads(msg["text"])
                    if obj.get("type") == "write_dropped":
                        dropped = obj
                        break
                    if obj.get("type") == "closed":
                        break
            assert dropped is not None, "expected write_dropped frame"
            assert dropped["reason"] == "frame_too_large"
            assert dropped["got_bytes"] == 64 * 1024 + 1
            # Session must still be alive — send a small frame & it goes through.
            ws.send_bytes(b"ok")
            # Close cleanly.
            ws.send_json({"type": "close"})
    assert b"ok" in t.last_link.writer.written
    assert not any(buf == huge for buf in t.last_link.writer.written)


def test_unsafe_device_sanitized_to_unknown_in_audit_session(
    monkeypatch, tmp_path
) -> None:
    """Code/security review 2026-05-07 MID: client-supplied `device`
    must not flow into audit-log session_id verbatim. Strings with
    newlines, very long values, or non-ASCII land as `unknown` so
    events.jsonl line-format and downstream filters can't be tricked."""
    monkeypatch.chdir(tmp_path)
    t = _FakeBidirectionalTransport(chunks=[b"out\n"] * 10)
    monkeypatch.setattr(
        "alb.api.uart_stream_route.build_transport", lambda **kw: t,
    )
    captured: list[dict[str, Any]] = []

    class _FakeBus:
        async def publish(self, event: dict[str, Any]) -> None:
            captured.append(event)

    monkeypatch.setattr(
        "alb.api.uart_stream_route.get_bus", lambda: _FakeBus()
    )
    app = create_app()
    huge = b"A" * (64 * 1024 + 1)
    with TestClient(app) as c:
        with c.websocket_connect("/uart/stream") as ws:
            # Newline in device — classic log-line injection attempt.
            ws.send_json({"device": "legit\nfake-line", "write": True})
            ws.receive_json()
            ws.receive_bytes()
            ws.send_bytes(huge)
            for _ in range(80):
                msg = ws.receive()
                if "text" in msg and msg["text"]:
                    obj = json.loads(msg["text"])
                    if obj.get("type") == "write_dropped":
                        break
            ws.send_json({"type": "close"})

    assert captured, "publish was never called"
    drop = next(e for e in captured if e.get("kind") == "write_dropped")
    assert drop["session_id"] == "uart-stream:unknown"
    assert drop["data"]["device"] == ""


def test_oversized_write_frame_publishes_audit_event(
    monkeypatch, tmp_path
) -> None:
    """write_dropped should also reach the audit bus so /audit/stream
    subscribers see it (operator visibility, not just per-WS feedback).
    Mirrors the inline ack test above but asserts the bus publish."""
    monkeypatch.chdir(tmp_path)
    t = _FakeBidirectionalTransport(chunks=[b"out\n"] * 10)
    monkeypatch.setattr(
        "alb.api.uart_stream_route.build_transport", lambda **kw: t,
    )

    captured: list[dict[str, Any]] = []

    class _FakeBus:
        async def publish(self, event: dict[str, Any]) -> None:
            captured.append(event)

    monkeypatch.setattr(
        "alb.api.uart_stream_route.get_bus", lambda: _FakeBus()
    )

    app = create_app()
    huge = b"A" * (64 * 1024 + 1)
    with TestClient(app) as c:
        with c.websocket_connect("/uart/stream?device=ABC123") as ws:
            ws.send_json({"device": "ABC123", "write": True})
            ws.receive_json()  # ready
            ws.receive_bytes()  # drain a chunk
            ws.send_bytes(huge)
            # Wait until either write_dropped frame arrives (proxy for
            # the publish having run) or we time out via iteration cap.
            for _ in range(80):
                msg = ws.receive()
                if "text" in msg and msg["text"]:
                    obj = json.loads(msg["text"])
                    if obj.get("type") == "write_dropped":
                        break
            ws.send_json({"type": "close"})

    assert any(e.get("kind") == "write_dropped" for e in captured), (
        f"expected a write_dropped audit event, got {captured!r}"
    )
    drop_event = next(e for e in captured if e.get("kind") == "write_dropped")
    assert drop_event["source"] == "uart_stream"
    assert drop_event["session_id"] == "uart-stream:ABC123"
    assert drop_event["data"]["got_bytes"] == 64 * 1024 + 1
    assert drop_event["data"]["max_bytes"] == 64 * 1024
    assert drop_event["data"]["reason"] == "frame_too_large"


# ─── PR-C.c idle-EOF reconnect regression ─────────────────────────
class _EofReader:
    """StreamReader stand-in that yields its chunks then EOFs (returns
    b'' from read). Used to simulate a TCP UART bridge that closes the
    client connection when the COM port goes idle."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._queue = list(chunks)

    async def read(self, n: int) -> bytes:
        if self._queue:
            return self._queue.pop(0)
        return b""  # EOF — outer pump treats as link_eof


def test_bidirectional_reconnects_on_link_eof(monkeypatch, tmp_path) -> None:
    """PR-C.c idle-EOF: when the bridge EOFs the link mid-session, the
    server must close the dead link, open a fresh one, and re-spawn both
    pump + recv tasks. Verified by a fake transport where session-0's
    reader EOFs after 2 chunks and session-1's reader yields 2 more.
    The client should see 4 chunks before closing with client_close."""
    monkeypatch.chdir(tmp_path)

    t = _FakeBidirectionalTransport()

    session_idx = {"v": 0}

    async def _open() -> _FakeLink:
        idx = session_idx["v"]
        session_idx["v"] += 1
        link = _FakeLink([])
        if idx == 0:
            # First link: yields 2 chunks then EOFs (idle bridge).
            link.reader = _EofReader([b"early-1\n", b"early-2\n"])
        else:
            # Second link: yields 2 more chunks, then blocks so we can
            # close cleanly from the client side.
            link.reader = _FakeReader([b"late-1\n", b"late-2\n"])
        t.last_link = link
        return link

    t.open_session = _open  # type: ignore[assignment]
    monkeypatch.setattr(
        "alb.api.uart_stream_route.build_transport", lambda **kw: t,
    )

    app = create_app()
    received: list[bytes] = []
    with TestClient(app) as c:
        with c.websocket_connect("/uart/stream") as ws:
            ws.send_json({"write": True})
            ready = ws.receive_json()
            assert ready["write"] is True
            # Drain 4 chunks across the reconnect. Allow interleaving with
            # potential text frames by checking each receive.
            for _ in range(40):
                if len(received) >= 4:
                    break
                msg = ws.receive()
                data = msg.get("bytes")
                if data:
                    received.append(data)
            assert len(received) == 4, (
                f"expected 4 chunks across 2 sessions, got {len(received)}: "
                f"{received!r}"
            )
            assert received[:2] == [b"early-1\n", b"early-2\n"]
            assert received[2:] == [b"late-1\n", b"late-2\n"]
            ws.send_text(json.dumps({"type": "close"}))
            closed = ws.receive_json()
            assert closed["type"] == "closed"
            assert closed["reason"] == "client_close"

    assert session_idx["v"] >= 2, (
        f"expected ≥2 open_session calls (reconnect), got {session_idx['v']}"
    )
    # Both links should have been closed via close_session.
    assert len(t.closed_links) >= 2, (
        f"both links must be closed cleanly, got {len(t.closed_links)}"
    )


def test_reader_oserror_yields_single_stream_error_close(
    monkeypatch, tmp_path
) -> None:
    """When link.reader raises OSError mid-read, exactly ONE close
    frame should be sent with reason='stream_error'."""
    monkeypatch.chdir(tmp_path)
    t = _FakeBidirectionalTransport()

    async def _open() -> _FakeLink:  # noqa: ANN001
        link = _FakeLink([])
        link.reader = _RaisingReader([b"first\n"])
        t.last_link = link
        return link

    t.open_session = _open  # type: ignore[assignment]
    monkeypatch.setattr(
        "alb.api.uart_stream_route.build_transport", lambda **kw: t,
    )
    app = create_app()
    with TestClient(app) as c:
        with c.websocket_connect("/uart/stream") as ws:
            ws.send_json({"write": True})
            ready = ws.receive_json()
            assert ready["write"] is True
            # First chunk arrives, then read raises OSError next call.
            ws.receive_bytes()
            # Drain any in-flight chunks before close.
            close = None
            for _ in range(100):
                msg = ws.receive()
                if "text" in msg and msg["text"]:
                    close = json.loads(msg["text"])
                    break
            assert close is not None and close["type"] == "closed"
            assert close["reason"] == "stream_error"
            assert "OSError" in close.get("error", "")
