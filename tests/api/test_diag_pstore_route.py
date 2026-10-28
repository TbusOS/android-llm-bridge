"""POST /api/diag/pstore -- records with a short summary and a relative path."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from alb.api import diag_route
from alb.api.server import create_app
from alb.capabilities.diagnose import PstoreFile, PstoreResult
from alb.infra.result import fail, ok

SERIAL = "0123456789ABCDEF"


@pytest.fixture
def ws(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(diag_route, "build_transport", lambda **_kw: object())
    return tmp_path


def _client() -> TestClient:
    return TestClient(create_app())


def test_pstore_returns_records_with_panic_line_and_relative_path(monkeypatch, ws: Path) -> None:
    saved = ws / "devices" / SERIAL / "pstore" / "2026-10-23T12-00-00"
    saved.mkdir(parents=True)
    (saved / "console-ramoops-0").write_text("\n\n[    0.1] boot ok\n[    9.9] Kernel panic - x\n")
    (saved / "pmsg-ramoops-0").write_bytes(b"\x00\x01binary")
    result = PstoreResult(
        source="pstore",
        pstore_present=True,
        last_kmsg_present=False,
        files=[
            PstoreFile("console-ramoops-0", 4096, str(saved / "console-ramoops-0"), None),
            PstoreFile(
                "dmesg-ramoops-0", 100, str(saved / "dmesg-ramoops-0"),
                "Kernel panic - not syncing: boom",
            ),
            PstoreFile("pmsg-ramoops-0", 8, str(saved / "pmsg-ramoops-0"), None, kind="logcat"),
            PstoreFile("lost-0", 12, "", None, error="pull failed"),
        ],
        saved_dir=str(saved),
        note="4 pstore file(s) in /sys/fs/pstore",
    )
    monkeypatch.setattr(diag_route.diag_cap, "pstore_pull", AsyncMock(return_value=ok(data=result, timing_ms=7)))
    with _client() as c:
        body = c.post(f"/api/diag/pstore?device={SERIAL}").json()
    assert body["ok"] is True
    data = body["data"]
    assert data["source"] == "pstore"
    assert data["saved_dir"] == f"devices/{SERIAL}/pstore/2026-10-23T12-00-00"
    console, dmesg, pmsg, lost = data["records"]
    assert console == {
        "name": "console-ramoops-0",
        "size": 4096,
        "kind": "kernel",
        "summary": "[    0.1] boot ok",  # first non-blank line, no panic line known
        "has_panic": False,
        "saved_path": f"devices/{SERIAL}/pstore/2026-10-23T12-00-00/console-ramoops-0",
        "error": "",
    }
    assert dmesg["summary"] == "Kernel panic - not syncing: boom" and dmesg["has_panic"] is True
    assert "logcat -L" in pmsg["summary"]
    assert lost["saved_path"] is None and lost["error"] == "pull failed" and lost["summary"] == ""
    assert str(ws) not in str(body)  # no absolute host path leaks


def test_pstore_none_source_has_no_records(monkeypatch, ws: Path) -> None:
    result = PstoreResult("none", False, False, [], None, "no record")
    monkeypatch.setattr(diag_route.diag_cap, "pstore_pull", AsyncMock(return_value=ok(data=result)))
    with _client() as c:
        data = c.post("/api/diag/pstore").json()["data"]
    assert data["records"] == [] and data["saved_dir"] is None and data["source"] == "none"


def test_pstore_keeps_root_required(monkeypatch, ws: Path) -> None:
    monkeypatch.setattr(
        diag_route.diag_cap,
        "pstore_pull",
        AsyncMock(
            return_value=fail(
                code="ROOT_REQUIRED",
                message="listing /sys/fs/pstore denied",
                suggestion="needs root",
                category="permission",
            )
        ),
    )
    with _client() as c:
        body = c.post(f"/api/diag/pstore?device={SERIAL}").json()
    assert body["ok"] is False and body["error"]["code"] == "ROOT_REQUIRED"


def test_pstore_unsafe_device(ws: Path) -> None:
    with _client() as c:
        r = c.post("/api/diag/pstore?device=../x")
    assert r.status_code == 400 and "../x" not in r.text
