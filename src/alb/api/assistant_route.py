"""Web API: /api/assistant/* -- the on-device model, if the board runs one.

    POST /api/assistant/ondevice/probe?device=<serial>
        Body: {"ports": [8080, 8000, 11434]}
        Ask each board-local TCP port whether a model server answers there.

How a device port is reached: the same way `alb port` does -- a stream to
`tcp:<port>` opened through the adb server's own protocol
(:mod:`alb.transport.adb_socket`), which works through alb's adb forwarder
exactly as `adb shell` does. No `adb forward`, no new transport. An HTTP/1.0
request is written on that stream and the reply read until the device closes
it (adb has no half-close, so the server hanging up is the end marker; the
request asks for `Connection: close` for that reason).

Per port two questions are asked:

    GET /v1/models   OpenAI-compatible servers (llama.cpp's server, vLLM, ...)
    GET /api/tags    Ollama

`kind` is "ollama" when /api/tags answers (Ollama serves /v1/models as well,
so the OpenAI-shaped answer alone would hide what it is), "openai" when only
/v1/models answers, null when neither does. One dead port never fails the
others: each result carries its own `ok` and `error`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from alb.infra.adb_endpoint import resolve_endpoint
from alb.infra.result import envelope_transport_init_error
from alb.infra.workspace import is_safe_device
from alb.transport.adb_socket import (
    AdbEndpointUnsupported,
    AdbRefused,
    AdbServerUnreachable,
    AdbSocketError,
    AmbiguousDevice,
    DeviceTarget,
    TcpEndpoint,
    open_device_stream,
    parse_endpoint,
    resolve_target,
)

router = APIRouter(prefix="/api/assistant", tags=["assistant"])

_CONNECT_TIMEOUT_S = 3.0
_READ_TIMEOUT_S = 4.0
# A model list is a few KB; a server that streams more than this is not
# answering the question that was asked.
_MAX_BODY = 512 * 1024
_MAX_MODELS = 50


class ProbeBody(BaseModel):
    ports: list[int] = Field(
        default_factory=lambda: [8080, 8000, 11434],
        min_length=1,
        max_length=8,
    )


def _config_socket() -> str | None:
    """``[transport.adb] server_socket`` from the active config, if set.
    Separate so tests can stand it in without loading a config."""
    from alb.transport.factory import active_settings

    return active_settings().config.adb.server_socket


def _dechunk(body: bytes) -> bytes:
    out = bytearray()
    rest = body
    while rest:
        size_line, sep, rest = rest.partition(b"\r\n")
        if not sep:
            break
        try:
            size = int(size_line.split(b";", 1)[0].strip() or b"0", 16)
        except ValueError:
            break
        if size == 0:
            break
        out += rest[:size]
        rest = rest[size + 2 :]
    return bytes(out)


def _parse_http(raw: bytes) -> tuple[int, bytes]:
    """``(status, body)`` of an HTTP response read to EOF; ValueError if it
    is not HTTP at all (the port is open but something else answers)."""
    head, sep, body = raw.partition(b"\r\n\r\n")
    if not sep or not head.startswith(b"HTTP/"):
        raise ValueError("not an HTTP response")
    lines = head.split(b"\r\n")
    try:
        status = int(lines[0].split()[1])
    except (IndexError, ValueError):
        raise ValueError("bad HTTP status line") from None
    if any(
        ln.lower().startswith(b"transfer-encoding:") and b"chunked" in ln.lower()
        for ln in lines[1:]
    ):
        body = _dechunk(body)
    return status, body


async def _http_get(
    endpoint: TcpEndpoint, target: DeviceTarget, port: int, path: str
) -> tuple[int, bytes]:
    sock = await open_device_stream(endpoint, target, port, timeout_s=_CONNECT_TIMEOUT_S)
    try:
        sock.writer.write(
            f"GET {path} HTTP/1.0\r\nHost: localhost\r\nAccept: application/json\r\n"
            "Connection: close\r\n\r\n".encode("ascii")
        )
        await sock.writer.drain()
        buf = bytearray()

        async def read_all() -> None:
            while len(buf) < _MAX_BODY:
                chunk = await sock.reader.read(65536)
                if not chunk:
                    return
                buf.extend(chunk)

        # Whatever arrived by the deadline is judged below; a half reply
        # fails to parse and is reported as such.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(read_all(), _READ_TIMEOUT_S)
        return _parse_http(bytes(buf))
    finally:
        await sock.close()


def _openai_models(body: bytes) -> list[str] | None:
    try:
        doc = json.loads(body)
    except ValueError:
        return None
    items = doc.get("data") if isinstance(doc, dict) else None
    if not isinstance(items, list):
        return None
    return [str(m["id"]) for m in items if isinstance(m, dict) and "id" in m][:_MAX_MODELS]


def _ollama_models(body: bytes) -> list[str] | None:
    try:
        doc = json.loads(body)
    except ValueError:
        return None
    items = doc.get("models") if isinstance(doc, dict) else None
    if not isinstance(items, list):
        return None
    names = [str(m.get("name") or m.get("model")) for m in items if isinstance(m, dict)]
    return [n for n in names if n and n != "None"][:_MAX_MODELS]


async def _probe_port(endpoint: TcpEndpoint, target: DeviceTarget, port: int) -> dict[str, Any]:
    result: dict[str, Any] = {"port": port, "ok": False, "kind": None, "models": [], "error": None}
    tags: list[str] | None = None
    openai: list[str] | None = None
    errors: list[str] = []
    for path in ("/api/tags", "/v1/models"):
        try:
            status, body = await _http_get(endpoint, target, port, path)
        except AdbRefused:
            # Nothing listens there: the second path would be refused too.
            result["error"] = "nothing listening on this device port"
            return result
        except AdbServerUnreachable:
            result["error"] = "the adb server could not be reached"
            return result
        except (AdbSocketError, OSError) as e:
            result["error"] = f"{type(e).__name__}"
            return result
        except ValueError as e:
            errors.append(f"{path}: {e}")
            continue
        if status != 200:
            errors.append(f"{path}: HTTP {status}")
            continue
        if path == "/api/tags":
            tags = _ollama_models(body)
        else:
            openai = _openai_models(body)
        if tags is None and openai is None:
            errors.append(f"{path}: reply is not a model list")
    if tags is not None:
        result.update(ok=True, kind="ollama", models=tags)
    elif openai is not None:
        result.update(ok=True, kind="openai", models=openai)
    else:
        result["error"] = "; ".join(errors) or "no model list"
    return result


@router.post("/ondevice/probe")
async def probe_ondevice(
    body: ProbeBody,
    device: str | None = Query(None, max_length=128),
) -> Any:
    if device and not is_safe_device(device):
        # L-051: no-echo on reject.
        raise HTTPException(status_code=400, detail="invalid device serial")
    if any(not 1 <= p <= 65535 for p in body.ports):
        raise HTTPException(status_code=400, detail="ports must be 1-65535")
    ports = list(dict.fromkeys(body.ports))  # keep order, drop repeats
    try:
        # resolve_endpoint may ask the hub over HTTP when the forwarder is not
        # bound in this process; from the event loop that would wait on
        # ourselves, so it runs on a worker thread.
        resolved = await asyncio.to_thread(resolve_endpoint, _config_socket())
        endpoint = parse_endpoint(resolved.socket)
        target = await resolve_target(endpoint, device, timeout_s=_CONNECT_TIMEOUT_S)
    except (AdbEndpointUnsupported, AdbSocketError, OSError) as e:
        # Could not even ask: an ordinary envelope, like a transport that
        # fails to build (REST envelope shape b).
        env = envelope_transport_init_error(e, device=device)
        if isinstance(e, AmbiguousDevice):
            env["error"]["code"] = "PORT_DEVICE_AMBIGUOUS"
        return env
    results = await asyncio.gather(*(_probe_port(endpoint, target, p) for p in ports))
    return {"ok": True, "data": list(results)}
