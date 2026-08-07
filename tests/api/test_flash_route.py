"""/api/flash/* — the HTTP surface of a fastboot job (ADR-056).

The property under test is the streaming contract: progress arrives as it
happens and the LAST line is always the verdict. A caller that read only the
final line must still learn everything it needs; a caller that reads
line-by-line must see movement while a partition is being written.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from alb.api.server import create_app
from alb.remote.flash import FlashEvent, FlashResult


@pytest.fixture(autouse=True)
def _ephemeral_forward_port(monkeypatch):
    monkeypatch.setenv("ALB_ADB_FORWARD_PORT", "0")


class _FakeService:
    def __init__(self, result: FlashResult, events: list[FlashEvent] | None = None) -> None:
        self.result = result
        self.events = events or []
        self.calls: list[tuple] = []

    def status(self):
        return {"available": True, "busy": False, "job": ""}

    async def devices(self, *, on_event=None):
        return self._run(("devices",), on_event)

    async def reboot(self, target="", *, on_event=None):
        return self._run(("reboot", target), on_event)

    async def flash(self, partition, image, *, on_event=None):
        return self._run(("flash", partition, str(image)), on_event)

    def _run(self, call, on_event):
        self.calls.append(call)
        for ev in self.events:
            if on_event:
                on_event(ev)
        return self.result


def _install(monkeypatch, service):
    import alb.api.flash_route as route

    monkeypatch.setattr(route, "get_flash_service", lambda: service)
    return service


def _lines(resp) -> list[dict]:
    return [json.loads(x) for x in resp.text.splitlines() if x.strip()]


def test_status_reports_the_service_view(monkeypatch):
    _install(monkeypatch, _FakeService(FlashResult(ok=True)))
    with TestClient(create_app()) as c:
        body = c.get("/api/flash/status").json()
    assert body["ok"] is True
    assert body["available"] is True
    assert body["busy"] is False


def test_devices_streams_ndjson_ending_in_done(monkeypatch):
    svc = _install(monkeypatch, _FakeService(FlashResult(ok=True, rc=0, stdout="serial\tfastboot")))
    with TestClient(create_app()) as c:
        resp = c.post("/api/flash/devices")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")
    lines = _lines(resp)
    assert lines[-1]["ev"] == "done"
    assert lines[-1]["ok"] is True
    assert svc.calls == [("devices",)]


def test_progress_lines_precede_the_verdict(monkeypatch):
    """A reader must be able to render movement without waiting for the end —
    that is the whole reason this endpoint streams."""
    events = [
        FlashEvent(phase="transfer", done=5, total=10),
        FlashEvent(phase="flash", text="writing 'boot'"),
    ]
    _install(monkeypatch, _FakeService(FlashResult(ok=True, rc=0), events))
    with TestClient(create_app()) as c:
        resp = c.post("/api/flash/flash", json={"partition": "boot", "image": "x.bin"})
    # image resolution happens first — this one does not exist
    assert resp.status_code == 400

    # now with a real workspace file
    import alb.api.flash_route as route

    monkeypatch.setattr(route, "_resolve_image", lambda rel: rel)
    with TestClient(create_app()) as c:
        resp = c.post("/api/flash/flash", json={"partition": "boot", "image": "x.bin"})
    lines = _lines(resp)
    assert [ln["ev"] for ln in lines] == ["progress", "progress", "done"]
    assert lines[0]["phase"] == "transfer"
    assert lines[0]["done"] == 5
    assert lines[1]["text"] == "writing 'boot'"
    assert lines[-1]["ok"] is True


def test_failure_verdict_carries_the_code(monkeypatch):
    _install(
        monkeypatch,
        _FakeService(FlashResult(ok=False, rc=1, code="FASTBOOT_BUSY", error="another job")),
    )
    with TestClient(create_app()) as c:
        resp = c.post("/api/flash/devices")
    assert resp.status_code == 200  # the JOB failed, the request did not
    done = _lines(resp)[-1]
    assert done["ok"] is False
    assert done["code"] == "FASTBOOT_BUSY"
    assert done["error"] == "another job"


def test_reboot_passes_the_target(monkeypatch):
    svc = _install(monkeypatch, _FakeService(FlashResult(ok=True, rc=0)))
    with TestClient(create_app()) as c:
        c.post("/api/flash/reboot", json={"target": "bootloader"})
        c.post("/api/flash/reboot", json={})
    assert svc.calls == [("reboot", "bootloader"), ("reboot", "")]


def test_unknown_image_is_rejected_before_any_job(monkeypatch):
    svc = _install(monkeypatch, _FakeService(FlashResult(ok=True)))
    with TestClient(create_app()) as c:
        resp = c.post("/api/flash/flash", json={"partition": "boot", "image": "not-there.bin"})
    assert resp.status_code == 400
    assert svc.calls == []


def test_image_path_cannot_escape_the_workspace(monkeypatch):
    """The image is named workspace-relative on purpose; traversal must be
    refused by the shared resolver, not by a second copy of the rules."""
    svc = _install(monkeypatch, _FakeService(FlashResult(ok=True)))
    with TestClient(create_app()) as c:
        resp = c.post(
            "/api/flash/flash", json={"partition": "boot", "image": "../../etc/passwd"}
        )
    assert resp.status_code == 400
    assert svc.calls == []


def test_partition_is_required(monkeypatch):
    _install(monkeypatch, _FakeService(FlashResult(ok=True)))
    with TestClient(create_app()) as c:
        assert c.post("/api/flash/flash", json={"image": "x.bin"}).status_code == 422
        assert (
            c.post("/api/flash/flash", json={"partition": "", "image": "x.bin"}).status_code == 422
        )
