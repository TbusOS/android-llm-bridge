"""Tests for `alb doctor` health check.

We test each layer probe in isolation via monkeypatching, plus an
integration test over the rendered output / exit code path.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from alb.capabilities.doctor import (
    Layer,
    compute_exit_code,
    probe_binaries,
    probe_config,
    probe_env,
    probe_serial_async,
    probe_ssh,
)


def _run(coro):
    """Tiny helper: run an async probe from a sync test."""
    import asyncio
    return asyncio.run(coro)


# ─── Layer.worst aggregator ────────────────────────────────────────


def test_layer_worst_picks_highest_severity() -> None:
    layer = Layer("x")
    layer.add("a", "ok")
    layer.add("b", "warn")
    layer.add("c", "err")
    assert layer.worst == "err"


def test_layer_worst_skips_count_as_neutral() -> None:
    """`skip` is informational; an `ok` companion wins the layer status."""
    layer = Layer("x")
    layer.add("a", "ok")
    layer.add("b", "skip")
    assert layer.worst == "ok"


def test_layer_worst_all_skip_is_skip() -> None:
    layer = Layer("x")
    layer.add("a", "skip")
    layer.add("b", "skip")
    assert layer.worst == "skip"


def test_layer_worst_empty_is_skip() -> None:
    assert Layer("x").worst == "skip"


# ─── env layer ─────────────────────────────────────────────────────


def test_probe_env_marks_set_vs_unset(monkeypatch) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", "/some/path")
    monkeypatch.delenv("ADB_SERVER_SOCKET", raising=False)
    monkeypatch.delenv("ALB_CONFIG", raising=False)
    monkeypatch.delenv("ALB_PROFILE", raising=False)
    layer = probe_env()
    by_name = {c.name: c for c in layer.checks}
    assert by_name["ALB_WORKSPACE"].status == "ok"
    assert by_name["ADB_SERVER_SOCKET"].status == "skip"
    # Worst is "ok" because skip is neutral
    assert layer.worst == "ok"


# ─── binaries layer ────────────────────────────────────────────────


def test_probe_binaries_adb_required(monkeypatch) -> None:
    """Missing adb is err; missing picocom/socat is skip."""
    def fake_which(name):
        return None  # nothing in PATH

    import shutil
    monkeypatch.setattr(shutil, "which", fake_which)
    layer = probe_binaries()
    by_name = {c.name: c for c in layer.checks}
    assert by_name["adb"].status == "err"
    assert by_name["picocom"].status == "skip"
    assert layer.worst == "err"


def test_probe_binaries_adb_present(monkeypatch) -> None:
    import shutil

    def fake_which(name):
        return f"/usr/local/bin/{name}" if name == "adb" else None

    monkeypatch.setattr(shutil, "which", fake_which)
    layer = probe_binaries()
    by_name = {c.name: c for c in layer.checks}
    assert by_name["adb"].status == "ok"
    assert by_name["picocom"].status == "skip"
    assert layer.worst == "ok"


# ─── config layer ──────────────────────────────────────────────────


def test_probe_config_missing_file_is_skip(monkeypatch, tmp_path: Path) -> None:
    """Missing config.toml → skip (defaults), not error."""
    monkeypatch.setenv("ALB_CONFIG", str(tmp_path / "absent.toml"))
    monkeypatch.setenv("ALB_PROFILE", "default")
    layer = probe_config()
    by_name = {c.name: c for c in layer.checks}
    assert by_name["global config"].status == "skip"
    assert by_name["profile"].status == "ok"


def test_probe_config_present_file(monkeypatch, tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text('default_profile = "default"\n')
    monkeypatch.setenv("ALB_CONFIG", str(cfg))
    layer = probe_config()
    by_name = {c.name: c for c in layer.checks}
    assert by_name["global config"].status == "ok"
    assert str(cfg) in by_name["global config"].detail


# ─── serial layer ──────────────────────────────────────────────────


def test_probe_serial_endpoint_not_listening(monkeypatch, tmp_path: Path) -> None:
    """Endpoint down → warn (not err): ser2net could just be off."""
    monkeypatch.setenv("ALB_CONFIG", str(tmp_path / "absent.toml"))
    # Force check_tcp_listen → False without actually opening sockets
    monkeypatch.setattr(
        "alb.capabilities.doctor.check_tcp_listen", lambda *a, **kw: False
    )
    layer = _run(probe_serial_async())
    assert any(c.status == "warn" for c in layer.checks)
    assert layer.worst == "warn"


def test_probe_serial_endpoint_listening_and_open(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALB_CONFIG", str(tmp_path / "absent.toml"))
    monkeypatch.setattr(
        "alb.capabilities.doctor.check_tcp_listen", lambda *a, **kw: True
    )
    # Mock SerialTransport.health() to claim connected
    fake_health = AsyncMock(return_value={"connected": True})
    monkeypatch.setattr(
        "alb.capabilities.doctor.SerialTransport",
        lambda **kw: type("FakeT", (), {"health": fake_health})(),
    )
    layer = _run(probe_serial_async())
    by_name = {c.name: c for c in layer.checks}
    listening = next(k for k in by_name if "listening" in k)
    assert by_name[listening].status == "ok"
    assert by_name["endpoint open"].status == "ok"
    assert layer.worst == "ok"


# ─── ssh layer ─────────────────────────────────────────────────────


def test_probe_ssh_unset_is_skip(monkeypatch) -> None:
    monkeypatch.delenv("ALB_SSH_HOST", raising=False)
    layer = probe_ssh()
    assert layer.worst == "skip"


def test_probe_ssh_set_and_unreachable(monkeypatch) -> None:
    monkeypatch.setenv("ALB_SSH_HOST", "example-host.invalid")
    monkeypatch.setenv("ALB_SSH_PORT", "22")
    monkeypatch.setattr(
        "alb.capabilities.doctor.check_tcp_listen", lambda *a, **kw: False
    )
    layer = probe_ssh()
    assert layer.worst == "err"


# ─── exit code ─────────────────────────────────────────────────────


def test_exit_code_no_err_returns_zero() -> None:
    a = Layer("a")
    a.add("x", "ok")
    b = Layer("b")
    b.add("y", "warn")
    assert compute_exit_code([a, b]) == 0


def test_exit_code_err_returns_one() -> None:
    a = Layer("a")
    a.add("x", "ok")
    b = Layer("b")
    b.add("y", "err")
    assert compute_exit_code([a, b]) == 1


# ─── End-to-end via CliRunner ──────────────────────────────────────


def test_alb_doctor_json_smoke(monkeypatch, tmp_path: Path) -> None:
    """`alb --json doctor` returns parseable JSON with expected layers."""
    from typer.testing import CliRunner

    from alb.cli.main import app

    # Force adb missing so we land at err (deterministic regardless of host)
    import shutil
    monkeypatch.setattr(shutil, "which", lambda _: None)
    monkeypatch.setenv("ALB_CONFIG", str(tmp_path / "absent.toml"))
    monkeypatch.delenv("ALB_SSH_HOST", raising=False)
    # Avoid real TCP sockets
    monkeypatch.setattr(
        "alb.capabilities.doctor.check_tcp_listen", lambda *a, **kw: False
    )

    runner = CliRunner()
    r = runner.invoke(app, ["--json", "doctor"])
    # err in adb layer → exit 1
    assert r.exit_code == 1
    payload = json.loads(r.stdout)
    layer_names = [l["name"] for l in payload["layers"]]
    assert layer_names == ["env", "binaries", "config", "adb", "serial", "ssh"]
    # binaries layer should be err (adb missing)
    binaries = next(l for l in payload["layers"] if l["name"] == "binaries")
    assert binaries["status"] == "err"
    assert payload["summary"]["err"] >= 1
