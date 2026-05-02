"""Tests for WS /logcat/stream (DEBT-022 PR-D)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from alb.api.server import create_app
from alb.transport.base import ShellResult, Transport


class _FakeAdbStreamTransport(Transport):
    """Yields a fixed sequence of logcat lines then ends."""

    name = "adb"

    def __init__(
        self, chunks: list[bytes] | None = None, last_kwargs: dict | None = None
    ) -> None:
        self.chunks = chunks if chunks is not None else [
            b"01-01 00:00:00.000  1234  5678 I MyApp   : starting up\n",
            b"01-01 00:00:00.123  1234  5678 W MyApp   : low memory\n",
            b"01-01 00:00:00.456  1234  5678 E MyApp   : crash\n",
        ]
        self.last_kwargs: dict = last_kwargs if last_kwargs is not None else {}

    async def shell(self, cmd: str, *, timeout: int = 30) -> ShellResult:
        return ShellResult(ok=True)

    async def stream_read(self, source: str, **kwargs: Any):  # noqa: ANN001
        if source != "logcat":
            return
        self.last_kwargs.clear()
        self.last_kwargs.update(kwargs)
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
        "alb.api.logcat_stream_route.build_transport",
        lambda **kwargs: _FakeAdbStreamTransport(),
    )
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_stream_sends_ready_then_lines_then_closed(client) -> None:
    with client.websocket_connect("/logcat/stream") as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        assert ready["transport"] == "adb"
        for _ in range(3):
            data = ws.receive_bytes()
            assert b"MyApp" in data
        closed = ws.receive_json()
        assert closed["type"] == "closed"


def test_stream_passes_filter_to_stream_read(monkeypatch, tmp_path) -> None:
    captured_kwargs: dict = {}

    def _build(**_):
        return _FakeAdbStreamTransport(last_kwargs=captured_kwargs)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("alb.api.logcat_stream_route.build_transport", _build)
    app = create_app()
    with TestClient(app) as c:
        with c.websocket_connect("/logcat/stream") as ws:
            ws.send_json({"filter": "*:E"})
            ws.receive_json()  # ready
            for _ in range(3):
                ws.receive_bytes()
    assert captured_kwargs.get("filter") == "*:E"


def test_stream_tags_shortcut_builds_filter(monkeypatch, tmp_path) -> None:
    captured_kwargs: dict = {}

    def _build(**_):
        return _FakeAdbStreamTransport(last_kwargs=captured_kwargs)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("alb.api.logcat_stream_route.build_transport", _build)
    app = create_app()
    with TestClient(app) as c:
        with c.websocket_connect("/logcat/stream") as ws:
            ws.send_json({"tags": ["MyApp", "OtherTag"]})
            ws.receive_json()
            for _ in range(3):
                ws.receive_bytes()
    assert captured_kwargs.get("filter") == "MyApp:V OtherTag:V *:S"


def test_stream_init_failure_closes_with_error(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    def _boom(**_: Any):
        raise RuntimeError("adb server unreachable")

    monkeypatch.setattr("alb.api.logcat_stream_route.build_transport", _boom)
    app = create_app()
    with TestClient(app) as c:
        with c.websocket_connect("/logcat/stream") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "closed"
            assert msg["reason"] == "init_failed"
            assert "RuntimeError" in msg["error"]


def test_stream_unsupported_transport_closes(monkeypatch, tmp_path) -> None:
    """Transport without stream_read → reason='unsupported_source'."""

    class _NoStreamTransport:
        name = "ssh"

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "alb.api.logcat_stream_route.build_transport",
        lambda **_: _NoStreamTransport(),
    )
    app = create_app()
    with TestClient(app) as c:
        with c.websocket_connect("/logcat/stream") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "closed"
            assert msg["reason"] == "unsupported_source"


def test_stream_endpoint_listed_in_schema(client) -> None:
    body = client.get("/api/version").json()
    paths = [w["path"] for w in body["ws"]]
    assert "/logcat/stream" in paths


@pytest.mark.parametrize(
    "spec",
    [
        "*:Q",  # bad level
        "MyApp",  # missing :level
        "MyApp:VV",  # bad level
        "MyApp:V *:bad",  # one good one bad
        "tag with space:V",  # tag has space → splits into bad tokens
        "$evil:V",  # disallowed shell metachar in tag
    ],
)
def test_bad_filter_spec_rejected_with_close_frame(monkeypatch, tmp_path, spec) -> None:
    """Server must validate filter spec and reject before spawning logcat."""
    built = {"called": False}

    def _build(**_):
        built["called"] = True
        return _FakeAdbStreamTransport()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("alb.api.logcat_stream_route.build_transport", _build)
    app = create_app()
    with TestClient(app) as c:
        with c.websocket_connect("/logcat/stream") as ws:
            ws.send_json({"filter": spec})
            msg = ws.receive_json()
            assert msg["type"] == "closed"
            assert msg["reason"] == "bad_filter"
            assert "expected" in msg["error"].lower()
    assert built["called"] is False


def test_good_filter_specs_pass(monkeypatch, tmp_path) -> None:
    """Valid filter specs must reach the transport."""
    captured_kwargs: dict = {}

    def _build(**_):
        return _FakeAdbStreamTransport(last_kwargs=captured_kwargs)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("alb.api.logcat_stream_route.build_transport", _build)
    app = create_app()
    with TestClient(app) as c:
        # Multi-token, mixed case, dotted tag, asterisk.
        with c.websocket_connect("/logcat/stream") as ws:
            ws.send_json({"filter": "MyApp.Net:v Other-Tag:I *:S"})
            ws.receive_json()
            for _ in range(3):
                ws.receive_bytes()
    assert captured_kwargs.get("filter") == "MyApp.Net:v Other-Tag:I *:S"


def test_empty_filter_passes(monkeypatch, tmp_path) -> None:
    captured_kwargs: dict = {}

    def _build(**_):
        return _FakeAdbStreamTransport(last_kwargs=captured_kwargs)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("alb.api.logcat_stream_route.build_transport", _build)
    app = create_app()
    with TestClient(app) as c:
        # Whitespace-only filter is treated as no filter.
        with c.websocket_connect("/logcat/stream") as ws:
            ws.send_json({"filter": "   "})
            ws.receive_json()
            for _ in range(3):
                ws.receive_bytes()
    # Empty string falls through truthy check → not forwarded as kwarg.
    assert "filter" not in captured_kwargs
