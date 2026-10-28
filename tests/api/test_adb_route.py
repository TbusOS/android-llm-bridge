"""/api/adb/root | unroot | remount -- thin wrappers over capabilities.adbd."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from alb.api import adb_route
from alb.api.server import create_app
from alb.capabilities.adbd import AdbdResult, RemountResult
from alb.infra.result import fail, ok

SERIAL = "0123456789ABCDEF"


@pytest.fixture
def seen(monkeypatch) -> list[object]:
    """Transports handed to build_transport, so a test can see the serial."""
    calls: list[object] = []

    def build(**kw):
        calls.append(kw)
        return object()

    monkeypatch.setattr(adb_route, "build_transport", build)
    return calls


def _client() -> TestClient:
    return TestClient(create_app())


def test_root_success_envelope(monkeypatch, seen) -> None:
    res = AdbdResult(outcome="became_root", uid=0, adbd_output="restarting adbd as root", polls=2, wait_ms=1800)
    monkeypatch.setattr(adb_route.adbd_cap, "root", AsyncMock(return_value=ok(data=res, timing_ms=1900)))
    with _client() as c:
        body = c.post(f"/api/adb/root?device={SERIAL}").json()
    assert body == {
        "ok": True,
        "data": {
            "outcome": "became_root",
            "uid": 0,
            "adbd_output": "restarting adbd as root",
            "polls": 2,
            "wait_ms": 1800,
        },
        "timing_ms": 1900,
    }
    assert seen == [{"device_serial": SERIAL}]


def test_root_refusal_keeps_its_error_code(monkeypatch, seen) -> None:
    monkeypatch.setattr(
        adb_route.adbd_cap,
        "root",
        AsyncMock(
            return_value=fail(
                code="ADB_ROOT_REFUSED",
                message="adbd cannot run as root in production builds",
                suggestion="flash a userdebug build",
                category="permission",
            )
        ),
    )
    with _client() as c:
        body = c.post("/api/adb/root").json()
    assert body["ok"] is False
    assert body["error"]["code"] == "ADB_ROOT_REFUSED"
    assert "production builds" in body["error"]["message"]


def test_unroot_no_effect_code_is_kept(monkeypatch, seen) -> None:
    monkeypatch.setattr(
        adb_route.adbd_cap,
        "unroot",
        AsyncMock(
            return_value=fail(
                code="ADB_UNROOT_NO_EFFECT",
                message="adbd restarted but the uid is still 0",
                suggestion="Nothing to retry",
                category="transport",
            )
        ),
    )
    with _client() as c:
        body = c.post(f"/api/adb/unroot?device={SERIAL}").json()
    assert body["ok"] is False and body["error"]["code"] == "ADB_UNROOT_NO_EFFECT"


def test_remount_outcome(monkeypatch, seen) -> None:
    monkeypatch.setattr(
        adb_route.adbd_cap,
        "remount",
        AsyncMock(return_value=ok(data=RemountResult(outcome="needs_reboot", output="reboot your device"))),
    )
    with _client() as c:
        body = c.post(f"/api/adb/remount?device={SERIAL}").json()
    assert body["ok"] is True
    assert body["data"] == {"outcome": "needs_reboot", "output": "reboot your device"}


def test_transport_failure_is_an_envelope(monkeypatch) -> None:
    def boom(**_kw):
        raise RuntimeError("no adb")

    monkeypatch.setattr(adb_route, "build_transport", boom)
    with _client() as c:
        body = c.post(f"/api/adb/root?device={SERIAL}").json()
    assert body["ok"] is False and body["error"]["code"] == "TRANSPORT_INIT_FAILED"


@pytest.mark.parametrize("verb", ["root", "unroot", "remount"])
def test_unsafe_device_is_400_and_not_echoed(verb, seen) -> None:
    with _client() as c:
        r = c.post(f"/api/adb/{verb}?device=a/../b")
    assert r.status_code == 400
    assert "a/../b" not in r.text
    assert seen == []
