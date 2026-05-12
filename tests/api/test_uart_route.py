"""Tests for /uart/* endpoints (DEBT-022 PR-C.a)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from alb.api.server import create_app
from alb.capabilities.logging import DmesgSummary
from alb.infra.result import fail, ok
from alb.transport.base import ShellResult, Transport


class _FakeSerialTransport(Transport):
    """Minimal serial transport stub — `capture_uart` only needs `name` +
    `stream_read('uart')`; we never actually iterate the stream because
    these tests monkey-patch capture_uart itself."""

    name = "serial"

    async def shell(self, cmd: str, *, timeout: int = 30) -> ShellResult:
        return ShellResult(ok=True)

    async def stream_read(self, source: str, **kwargs: Any):  # noqa: ANN001
        if False:
            yield b""

    async def push(self, local, remote):  # noqa: ANN001
        return ShellResult(ok=True)

    async def pull(self, remote, local):  # noqa: ANN001
        return ShellResult(ok=True)

    async def reboot(self, mode: str = "normal") -> ShellResult:
        return ShellResult(ok=True)

    async def health(self) -> dict[str, Any]:
        return {"ok": True}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Point ALB_WORKSPACE at a tmp dir + chdir there. The route resolves
    the logs dir via workspace_root() which honours ALB_WORKSPACE."""
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def client(workspace, monkeypatch):
    monkeypatch.setattr(
        "alb.api.uart_route.build_transport",
        lambda **kwargs: _FakeSerialTransport(),
    )
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_capture_runs_and_returns_artifact_meta(client, workspace, monkeypatch) -> None:
    """Happy path: route forwards to capture_uart + reports artifact name + size."""
    artifact = workspace / "logs" / "2026-05-01T01-00-00-uart.log"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("sample uart line 1\nsample uart line 2\n")

    async def fake_capture(transport, *, duration: int, device, output=None):
        assert transport.name == "serial"
        return ok(data=DmesgSummary(lines=2, errors=0, duration_captured_ms=5000), artifacts=[artifact])

    monkeypatch.setattr("alb.api.uart_route.capture_uart", fake_capture)

    r = client.post("/uart/capture", params={"duration": 5})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["duration"] == 5
    assert body["lines"] == 2
    assert body["errors"] == 0
    assert body["filename"] == "2026-05-01T01-00-00-uart.log"
    assert body["path"].endswith("2026-05-01T01-00-00-uart.log")


def test_capture_rejects_invalid_duration(client) -> None:
    r = client.post("/uart/capture", params={"duration": 0})
    assert r.status_code == 422  # FastAPI Query ge=1 validation
    r = client.post("/uart/capture", params={"duration": 99999})
    assert r.status_code == 422


def test_capture_build_transport_failure_returns_inline(workspace, monkeypatch) -> None:
    def _boom(**kwargs: Any):
        raise RuntimeError("no serial transport configured")

    monkeypatch.setattr("alb.api.uart_route.build_transport", _boom)
    app = create_app()
    with TestClient(app) as c:
        r = c.post("/uart/capture", params={"duration": 5})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "RuntimeError" in body["error"]


def test_capture_capability_failure_returns_inline(client, monkeypatch) -> None:
    async def fake_capture(transport, *, duration, device, output=None):
        return fail(
            code="TRANSPORT_NOT_SUPPORTED",
            message="capture_uart requires serial transport, got adb",
            category="transport",
        )

    monkeypatch.setattr("alb.api.uart_route.capture_uart", fake_capture)
    r = client.post("/uart/capture", params={"duration": 5})
    body = r.json()
    assert body["ok"] is False
    assert "requires serial transport" in body["error"]


def test_list_captures_empty_when_no_logs_dir(client) -> None:
    r = client.get("/uart/captures")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["captures"] == []


def test_list_captures_returns_newest_first(client, workspace) -> None:
    logs = workspace / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    old = logs / "2026-05-01T00-00-00-uart.log"
    new = logs / "2026-05-01T01-00-00-uart.log"
    old.write_text("old\n")
    time.sleep(0.01)  # ensure mtime ordering
    new.write_text("new\n")

    body = client.get("/uart/captures").json()
    assert body["ok"] is True
    assert [c["name"] for c in body["captures"]] == [new.name, old.name]
    assert body["captures"][0]["size_bytes"] == len("new\n")


def test_list_captures_skips_unrelated_files(client, workspace) -> None:
    logs = workspace / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "2026-05-01T00-00-00-uart.log").write_text("uart\n")
    (logs / "2026-05-01T00-00-00-logcat.txt").write_text("logcat\n")
    (logs / "random.txt").write_text("random\n")

    body = client.get("/uart/captures").json()
    names = [c["name"] for c in body["captures"]]
    assert names == ["2026-05-01T00-00-00-uart.log"]


def test_read_capture_returns_text(client, workspace) -> None:
    logs = workspace / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    f = logs / "2026-05-01T01-00-00-uart.log"
    f.write_text("hello uart\nstack trace line\n")

    body = client.get(f"/uart/captures/{f.name}").json()
    assert body["ok"] is True
    assert body["name"] == f.name
    assert body["text"] == "hello uart\nstack trace line\n"
    assert body["size_bytes"] == len(body["text"])


def test_read_capture_404_when_missing(client) -> None:
    r = client.get("/uart/captures/2099-01-01T00-00-00-uart.log")
    assert r.status_code == 404


def test_read_capture_rejects_path_traversal(client) -> None:
    r = client.get("/uart/captures/..%2Fescape-uart.log")
    # FastAPI normalises the path before route matching, but our gate
    # also catches ".." literally.
    assert r.status_code in (400, 404)


def test_list_captures_runs_in_worker_thread_l033(
    monkeypatch, client, workspace
) -> None:
    """L-033: list_captures must delegate FS scan to asyncio.to_thread.

    Locks the part 141 sweep against future inlining of sync FS in the
    async endpoint.
    """
    seen: list[str] = []
    import asyncio as _asyncio

    real_to_thread = _asyncio.to_thread

    async def tracking_to_thread(fn, /, *args, **kwargs):
        seen.append(getattr(fn, "__name__", repr(fn)))
        return await real_to_thread(fn, *args, **kwargs)

    monkeypatch.setattr("alb.api.uart_route.asyncio.to_thread", tracking_to_thread)
    (workspace / "logs").mkdir()
    r = client.get("/uart/captures")
    assert r.status_code == 200
    assert "_list_captures_in_thread" in seen


def test_device_query_param_path_traversal_400(client, workspace) -> None:
    """L-035 root-layer reject: `?device=../etc` must 400, not silently
    list captures from outside the device dir.

    Mirrors the screenshots route fix in part 138 — `_logs_dir(device)`
    used to build `<root>/devices/../etc/logs` then `resolve_under`
    flattened the escape via `base.resolve()`.
    """
    escaped = workspace / "etc" / "logs"
    escaped.mkdir(parents=True)
    (escaped / "2026-05-12T00-00-00-uart.log").write_text("leaked\n")

    for device in ("..%2Fetc", "../etc"):
        r = client.get(f"/uart/captures?device={device}")
        assert r.status_code in (400, 404), (
            f"device={device!r}: expected 400/404, got {r.status_code} "
            f"body={r.text}"
        )
        if r.status_code == 200:
            assert "leaked" not in r.text


def test_read_capture_rejects_non_uart_filename(client, workspace) -> None:
    logs = workspace / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "secret.txt").write_text("nope\n")

    r = client.get("/uart/captures/secret.txt")
    assert r.status_code == 400


def test_read_capture_handles_binary_noise(client, workspace) -> None:
    """UART buffers can contain non-utf8 bytes — must not 500."""
    logs = workspace / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    f = logs / "2026-05-01T01-00-00-uart.log"
    f.write_bytes(b"valid line\n\xff\xfe\xfd\n")

    body = client.get(f"/uart/captures/{f.name}").json()
    assert body["ok"] is True
    assert "valid line" in body["text"]


def test_endpoints_listed_in_schema(client) -> None:
    body = client.get("/api/version").json()
    paths = [(e["method"], e["path"]) for e in body["rest"]]
    assert ("POST", "/uart/capture") in paths
    assert ("GET", "/uart/captures") in paths
    assert ("GET", "/uart/captures/{name}") in paths
    assert ("DELETE", "/uart/captures/{name}") in paths


def test_delete_capture_removes_file(client, workspace) -> None:
    """Happy path: DELETE removes the file from disk + ok=true."""
    f = workspace / "logs" / "2026-05-01T01-00-00-uart.log"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("sample uart line\n")
    assert f.exists()

    r = client.delete(f"/uart/captures/{f.name}")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["name"] == f.name
    assert not f.exists()


def test_delete_capture_404_when_missing(client) -> None:
    r = client.delete("/uart/captures/never-existed-uart.log")
    assert r.status_code == 404


def test_delete_capture_rejects_path_traversal(client) -> None:
    r = client.delete("/uart/captures/..%2Fpasswd-uart.log")
    assert r.status_code in (400, 404)


def test_delete_capture_rejects_non_uart_filename(client, workspace) -> None:
    """Same shape as read: the gate refuses anything not *-uart.log."""
    intruder = workspace / "logs" / "secrets.txt"
    intruder.parent.mkdir(parents=True, exist_ok=True)
    intruder.write_text("hello")

    r = client.delete("/uart/captures/secrets.txt")
    assert r.status_code == 400
    # File must still exist — bad name shouldn't have touched it.
    assert intruder.exists()


def test_read_capture_rejects_symlink(client, workspace) -> None:
    """Code-review 2026-05-07 HIGH (parallel to screenshots): symlink
    placed in logs/ used to be followed by read_text. Reject outright."""
    target = workspace / "secret.txt"
    target.write_text("not a uart log")
    link_dir = workspace / "logs"
    link_dir.mkdir(parents=True, exist_ok=True)
    link = link_dir / "leak-uart.log"
    link.symlink_to(target)

    r = client.get("/uart/captures/leak-uart.log")
    assert r.status_code == 400
    assert "symlink" in r.json()["detail"].lower()


def test_delete_capture_rejects_symlink(client, workspace) -> None:
    """Same protection on DELETE — even though unlink only removes the
    link itself, refuse to act on an entry the operator clearly didn't
    create via the capture flow."""
    target = workspace / "real.txt"
    target.write_text("untouched")
    link_dir = workspace / "logs"
    link_dir.mkdir(parents=True, exist_ok=True)
    link = link_dir / "evil-uart.log"
    link.symlink_to(target)

    r = client.delete("/uart/captures/evil-uart.log")
    assert r.status_code == 400
    # Both the link and the target must remain untouched.
    assert link.exists()
    assert target.exists()
