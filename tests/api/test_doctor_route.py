"""Tests for GET /api/doctor — env health snapshot route."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from alb.api.server import create_app


def test_doctor_route_returns_six_layers(monkeypatch, tmp_path: Path) -> None:
    """Happy path: the route returns the same six-layer shape as the CLI
    JSON, with a `summary` block on top."""
    import shutil

    # Deterministic: kill all external probes so the test doesn't depend
    # on the host's binaries / network state.
    monkeypatch.setattr(shutil, "which", lambda _: None)
    monkeypatch.setenv("ALB_CONFIG", str(tmp_path / "absent.toml"))
    monkeypatch.delenv("ALB_SSH_HOST", raising=False)
    monkeypatch.setattr(
        "alb.capabilities.doctor.check_tcp_listen", lambda *a, **kw: False
    )

    app = create_app()
    with TestClient(app) as c:
        r = c.get("/api/doctor")
    assert r.status_code == 200
    body = r.json()
    assert "layers" in body and "summary" in body
    names = [l["name"] for l in body["layers"]]
    assert names == ["env", "binaries", "config", "adb", "serial", "ssh"]
    # adb missing → binaries layer reports err
    binaries = next(l for l in body["layers"] if l["name"] == "binaries")
    assert binaries["status"] == "err"
    assert body["summary"]["err"] >= 1


def test_doctor_route_each_check_has_required_fields(monkeypatch, tmp_path: Path) -> None:
    """Each `checks` entry MUST have name / status / detail keys so the
    web renderer can map blindly without `.get(..., "")` everywhere."""
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _: None)
    monkeypatch.setenv("ALB_CONFIG", str(tmp_path / "absent.toml"))
    monkeypatch.delenv("ALB_SSH_HOST", raising=False)
    monkeypatch.setattr(
        "alb.capabilities.doctor.check_tcp_listen", lambda *a, **kw: False
    )

    app = create_app()
    with TestClient(app) as c:
        body = c.get("/api/doctor").json()
    for layer in body["layers"]:
        assert {"name", "status", "checks"} <= layer.keys()
        for check in layer["checks"]:
            assert {"name", "status", "detail"} == check.keys()
            assert check["status"] in ("ok", "warn", "err", "skip")
