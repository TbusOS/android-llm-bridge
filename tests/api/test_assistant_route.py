"""POST /api/assistant/ondevice/probe -- through a fake adb server.

The fake speaks the real adb server protocol (4-hex length framing,
OKAY/FAIL, `host:transport:<serial>`, then `tcp:<port>`), and behind a
`tcp:<port>` it plays an HTTP server. So the whole path under test is the one
`alb port` uses: nothing in the route is stubbed except which adb server it
talks to.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from alb.api import assistant_route
from alb.api.server import create_app

SERIAL = "0123456789ABCDEF"

Handler = Callable[[str], bytes]  # request path -> full raw HTTP response


def _http(status: int, body: object, *, chunked: bool = False) -> bytes:
    raw = json.dumps(body).encode()
    if chunked:
        mid = len(raw) // 2
        payload = b"".join(
            f"{len(p):x}\r\n".encode() + p + b"\r\n" for p in (raw[:mid], raw[mid:])
        ) + b"0\r\n\r\n"
        return f"HTTP/1.1 {status} X\r\nTransfer-Encoding: chunked\r\n\r\n".encode() + payload
    head = f"HTTP/1.0 {status} X\r\nContent-Type: application/json\r\nContent-Length: {len(raw)}\r\n\r\n"
    return head.encode() + raw


OPENAI = {"object": "list", "data": [{"id": "gguf-model-q4", "object": "model"}]}
OLLAMA = {"models": [{"name": "model-a:7b"}, {"name": "model-b:3b"}]}


class _FakeAdbServer:
    def __init__(self, ports: dict[int, Handler], devices: str = f"{SERIAL}\tdevice\n") -> None:
        self.ports = ports
        self.devices = devices
        self.requests: list[tuple[int, str]] = []
        self.loop = asyncio.new_event_loop()
        self.port = 0
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        server = self.loop.run_until_complete(
            asyncio.start_server(self._on_client, "127.0.0.1", 0)
        )
        self.port = server.sockets[0].getsockname()[1]
        self._ready.set()
        self.loop.run_forever()
        server.close()

    async def _read_req(self, reader: asyncio.StreamReader) -> str:
        n = int((await reader.readexactly(4)).decode(), 16)
        return (await reader.readexactly(n)).decode()

    async def _on_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            req = await self._read_req(reader)
            if req == "host:devices":
                writer.write(b"OKAY" + f"{len(self.devices):04x}".encode() + self.devices.encode())
                return
            writer.write(b"OKAY")  # host:transport...
            req = await self._read_req(reader)
            port = int(req.removeprefix("tcp:"))
            handler = self.ports.get(port)
            if handler is None:
                msg = b"connection refused"
                writer.write(b"FAIL" + f"{len(msg):04x}".encode() + msg)
                return
            writer.write(b"OKAY")
            await writer.drain()
            head = await reader.readuntil(b"\r\n\r\n")
            path = head.split(b" ", 2)[1].decode()
            self.requests.append((port, path))
            writer.write(handler(path))
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            await writer.drain()
            writer.close()

    def start(self) -> None:
        self._thread.start()
        assert self._ready.wait(5)

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(5)


@pytest.fixture
def adb_server(monkeypatch) -> Iterator[Callable[..., _FakeAdbServer]]:
    started: list[_FakeAdbServer] = []

    def make(ports: dict[int, Handler], **kw) -> _FakeAdbServer:
        srv = _FakeAdbServer(ports, **kw)
        srv.start()
        started.append(srv)
        monkeypatch.setattr(assistant_route, "_config_socket", lambda: f"tcp:127.0.0.1:{srv.port}")
        return srv

    yield make
    for s in started:
        s.stop()


def _probe(body: dict | None = None, device: str | None = SERIAL):
    q = f"?device={device}" if device else ""
    with TestClient(create_app()) as c:
        return c.post(f"/api/assistant/ondevice/probe{q}", json=body or {})


def test_openai_server_on_one_port_ollama_on_another_nothing_on_the_third(adb_server) -> None:
    srv = adb_server(
        {
            8080: lambda p: _http(200, OPENAI) if p == "/v1/models" else _http(404, {"error": "no"}),
            11434: lambda p: _http(200, OLLAMA if p == "/api/tags" else OPENAI, chunked=True),
        }
    )
    body = _probe().json()
    assert body["ok"] is True
    by_port = {r["port"]: r for r in body["data"]}
    assert [r["port"] for r in body["data"]] == [8080, 8000, 11434]
    assert by_port[8080] == {
        "port": 8080, "ok": True, "kind": "openai", "models": ["gguf-model-q4"], "error": None
    }
    # Ollama answers both paths; the Ollama list wins, so the page does not
    # treat an Ollama server as a plain OpenAI one.
    assert by_port[11434] == {
        "port": 11434, "ok": True, "kind": "ollama", "models": ["model-a:7b", "model-b:3b"], "error": None
    }
    assert by_port[8000]["ok"] is False and by_port[8000]["kind"] is None
    assert "nothing listening" in by_port[8000]["error"]
    assert (8080, "/v1/models") in srv.requests and (8080, "/api/tags") in srv.requests


def test_ports_come_from_the_body_and_repeats_are_dropped(adb_server) -> None:
    srv = adb_server({9090: lambda p: _http(200, OPENAI)})
    data = _probe({"ports": [9090, 9090]}).json()["data"]
    assert [r["port"] for r in data] == [9090]
    assert [p for port, p in srv.requests if port == 9090].count("/v1/models") == 1


def test_open_port_that_is_not_a_model_server(adb_server) -> None:
    adb_server({8080: lambda p: _http(404, {"error": "not found"})})
    r = _probe({"ports": [8080]}).json()["data"][0]
    assert r["ok"] is False and r["kind"] is None
    assert "HTTP 404" in r["error"]


def test_open_port_that_does_not_speak_http(adb_server) -> None:
    adb_server({8080: lambda p: b"\x00\x01binary banner"})
    r = _probe({"ports": [8080]}).json()["data"][0]
    assert r["ok"] is False
    assert "not an HTTP response" in r["error"]


def test_a_reply_that_is_not_a_model_list(adb_server) -> None:
    adb_server({8080: lambda p: _http(200, {"hello": "world"})})
    r = _probe({"ports": [8080]}).json()["data"][0]
    assert r["ok"] is False and "not a model list" in r["error"]


def test_model_list_is_capped(adb_server) -> None:
    many = {"data": [{"id": f"m{i}"} for i in range(200)]}
    adb_server({8080: lambda p: _http(200, many)})
    r = _probe({"ports": [8080]}).json()["data"][0]
    assert len(r["models"]) == 50


def test_unreachable_adb_server_is_reported_per_port(monkeypatch) -> None:
    monkeypatch.setattr(assistant_route, "_config_socket", lambda: "tcp:127.0.0.1:1")
    body = _probe({"ports": [8080]}).json()
    # With a serial given nothing is asked of the server up front, so the
    # failure shows up on the port, not as a request error.
    assert body["ok"] is True
    assert body["data"][0]["ok"] is False
    assert "adb server" in body["data"][0]["error"]


def test_ambiguous_device_is_an_envelope_error(adb_server) -> None:
    adb_server({}, devices="AAA\tdevice\nBBB\tdevice\n")
    body = _probe({"ports": [8080]}, device=None).json()
    assert body["ok"] is False and body["error"]["code"] == "PORT_DEVICE_AMBIGUOUS"


def test_the_serial_is_passed_to_the_adb_server(adb_server) -> None:
    seen: list[str] = []
    srv = adb_server({8080: lambda p: _http(200, OPENAI)})
    original = srv._read_req

    async def spy(reader):
        req = await original(reader)
        seen.append(req)
        return req

    srv._read_req = spy  # type: ignore[method-assign]
    _probe({"ports": [8080]})
    assert f"host:transport:{SERIAL}" in seen


@pytest.mark.parametrize("body", [{"ports": [0]}, {"ports": [70000]}, {"ports": []}, {"ports": list(range(1, 12))}, {"ports": ["x"]}])
def test_bad_ports_are_rejected(body) -> None:
    assert _probe(body).status_code in (400, 422)


def test_unsafe_device_is_400_and_not_echoed() -> None:
    r = _probe({"ports": [8080]}, device="../x")
    assert r.status_code == 400 and "../x" not in r.text
