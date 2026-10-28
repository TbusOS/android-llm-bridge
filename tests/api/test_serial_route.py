"""POST /api/serial/break-in -- NDJSON: ready, then the verdict."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from alb.api import serial_route
from alb.api.server import create_app
from alb.capabilities.breakin import BreakInResult
from alb.infra.result import fail, ok
from alb.transport.base import ShellResult

SERIAL = "0123456789ABCDEF"


def _ndjson(resp) -> list[dict[str, Any]]:
    return [json.loads(x) for x in resp.text.splitlines() if x.strip()]


class _Adb:
    def __init__(self, answers: bool) -> None:
        self.answers = answers

    async def shell(self, cmd: str, **_kw):
        return ShellResult(ok=self.answers, stdout="")


@pytest.fixture
def ws(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    return tmp_path


def _install(monkeypatch, *, adb: _Adb | None, outcome) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    serial = object()

    def build(*, override: str | None = None, device_serial: str | None = None):
        if override == "serial":
            return serial
        if adb is None:
            raise RuntimeError("no adb")
        return adb

    async def fake_break_in(transport, **kw):
        seen["transport"] = transport
        seen["kw"] = kw
        kw["on_ready"]()
        return outcome

    monkeypatch.setattr(serial_route, "build_transport", build)
    monkeypatch.setattr(serial_route, "break_in", fake_break_in)
    seen["serial"] = serial
    return seen


def _stopped(ws: Path) -> Any:
    log = ws / "logs" / "x-breakin-uart.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("")
    return ok(
        data=BreakInResult(
            outcome="stopped_at_prompt",
            matched="Hit any key to stop autoboot",
            prompt_line="=> ",
            sends=3,
            tail="...",
            duration_ms=4200,
            reboot="serial",
        ),
        artifacts=[log],
        timing_ms=4200,
    )


def _client() -> TestClient:
    return TestClient(create_app())


def test_break_in_streams_ready_then_done_using_the_shared_serial(monkeypatch, ws) -> None:
    seen = _install(monkeypatch, adb=None, outcome=_stopped(ws))
    with _client() as c:
        resp = c.post("/api/serial/break-in", json={"reboot": "serial", "timeout_s": 30})
    assert resp.headers["content-type"].startswith("application/x-ndjson")
    evs = _ndjson(resp)
    assert evs[0] == {"ev": "progress", "phase": "ready", "reboot": "serial"}
    done = evs[-1]
    assert done["ev"] == "done" and done["ok"] is True
    assert done["data"] == {
        "outcome": "stopped_at_prompt",
        "matched": "Hit any key to stop autoboot",
        "prompt_line": "=> ",
        "sends": 3,
        "tail": "...",
        "duration_ms": 4200,
        "reboot": "serial",
    }
    assert done["reboot_mode"] == "serial"
    assert done["log"] == "logs/x-breakin-uart.log"
    assert seen["transport"] is seen["serial"]
    assert seen["kw"]["reboot"] == "serial" and seen["kw"]["timeout_s"] == 30
    assert seen["kw"]["reboot_transport"] is None


def test_booted_past_is_kept_as_an_error_code_with_the_tail(monkeypatch, ws) -> None:
    failure = fail(
        code="BREAKIN_BOOTED_PAST",
        message="the board went on into its kernel",
        suggestion="retry",
        category="transport",
        details={"tail": "Starting kernel ..."},
    )
    _install(monkeypatch, adb=None, outcome=failure)
    with _client() as c:
        done = _ndjson(c.post("/api/serial/break-in", json={}))[-1]
    assert done["ok"] is False
    assert done["error"]["code"] == "BREAKIN_BOOTED_PAST"
    assert done["error"]["details"]["tail"] == "Starting kernel ..."
    assert done["reboot_mode"] is None and done["log"] is None


def test_manual_reset_passes_no_reboot(monkeypatch, ws) -> None:
    seen = _install(monkeypatch, adb=None, outcome=_stopped(ws))
    with _client() as c:
        evs = _ndjson(c.post("/api/serial/break-in", json={"reboot": ""}))
    assert evs[0]["reboot"] is None
    assert seen["kw"]["reboot"] is None


def test_auto_prefers_adb_when_a_device_answers(monkeypatch, ws) -> None:
    adb = _Adb(True)
    seen = _install(monkeypatch, adb=adb, outcome=_stopped(ws))
    with _client() as c:
        evs = _ndjson(c.post(f"/api/serial/break-in?device={SERIAL}", json={"reboot": "auto"}))
    assert evs[0]["reboot"] == "adb"
    assert seen["kw"]["reboot"] == "adb" and seen["kw"]["reboot_transport"] is adb


@pytest.mark.parametrize("adb", [_Adb(False), None])
def test_auto_falls_back_to_serial_when_adb_is_silent_or_missing(monkeypatch, ws, adb) -> None:
    seen = _install(monkeypatch, adb=adb, outcome=_stopped(ws))
    with _client() as c:
        evs = _ndjson(c.post("/api/serial/break-in", json={"reboot": "auto"}))
    assert evs[0]["reboot"] == "serial"
    assert seen["kw"]["reboot"] == "serial" and seen["kw"]["reboot_transport"] is None


def test_explicit_adb_without_an_adb_transport_is_an_envelope(monkeypatch, ws) -> None:
    _install(monkeypatch, adb=None, outcome=_stopped(ws))
    with _client() as c:
        resp = c.post("/api/serial/break-in", json={"reboot": "adb"})
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json()["error"]["code"] == "TRANSPORT_INIT_FAILED"


def test_serial_transport_failure_is_an_envelope(monkeypatch, ws) -> None:
    def boom(**_kw):
        raise RuntimeError("no serial configured")

    monkeypatch.setattr(serial_route, "build_transport", boom)
    with _client() as c:
        body = c.post("/api/serial/break-in", json={}).json()
    assert body["ok"] is False and body["error"]["code"] == "TRANSPORT_INIT_FAILED"


def test_an_exception_inside_break_in_still_ends_with_done(monkeypatch, ws) -> None:
    async def explode(*_a, **_kw):
        raise ValueError("kaput")

    _install(monkeypatch, adb=None, outcome=None)
    monkeypatch.setattr(serial_route, "break_in", explode)
    with _client() as c:
        done = _ndjson(c.post("/api/serial/break-in", json={}))[-1]
    assert done["ev"] == "done" and done["ok"] is False
    assert done["error"]["code"] == "BREAKIN_FAILED"


@pytest.mark.parametrize(
    "body",
    [{"reboot": "recovery"}, {"timeout_s": 1}, {"timeout_s": 601}, {"reboot": 5}],
)
def test_bad_bodies_are_422(monkeypatch, ws, body) -> None:
    _install(monkeypatch, adb=None, outcome=None)
    with _client() as c:
        assert c.post("/api/serial/break-in", json=body).status_code == 422


def test_unsafe_device_is_400(ws) -> None:
    with _client() as c:
        r = c.post("/api/serial/break-in?device=../x", json={})
    assert r.status_code == 400 and "../x" not in r.text
