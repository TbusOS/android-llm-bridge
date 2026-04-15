"""Smoke tests for the `alb` CLI: it must at least start and show help/describe."""

from __future__ import annotations

from typer.testing import CliRunner

from alb.cli.main import app

runner = CliRunner()


def test_help_exits_clean() -> None:
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0
    assert "android-llm-bridge" in r.stdout


def test_version() -> None:
    r = runner.invoke(app, ["version"])
    assert r.exit_code == 0
    assert "alb" in r.stdout


def test_describe_shows_transports_and_capabilities() -> None:
    r = runner.invoke(app, ["describe"])
    assert r.exit_code == 0
    for name in ("adb", "ssh", "serial"):
        assert name in r.stdout
    for cap in ("shell", "logging", "filesync", "diagnose", "power", "app"):
        assert cap in r.stdout


def test_describe_json() -> None:
    r = runner.invoke(app, ["--json", "describe"])
    assert r.exit_code == 0
    import json
    payload = json.loads(r.stdout)
    assert "transports" in payload
    assert any(c["name"] == "shell" for c in payload["capabilities"])


def test_skills_preview_includes_capabilities() -> None:
    r = runner.invoke(app, ["skills", "preview"])
    assert r.exit_code == 0
    assert "## Supported transports" in r.stdout
    assert "## Capabilities" in r.stdout


def test_subgroups_advertise_their_commands() -> None:
    # Each add_typer group should expose --help successfully.
    for group in ("fs", "diag", "power", "app", "serial", "setup", "skills", "log"):
        r = runner.invoke(app, [group, "--help"])
        assert r.exit_code == 0, f"{group} --help failed: {r.stdout}\n{r.stderr}"
