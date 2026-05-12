"""Smoke tests for the `alb` CLI: it must at least start and show help/describe."""

from __future__ import annotations

import pytest
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


def test_bad_profile_flag_emits_bad_parameter() -> None:
    """L-035: `alb --profile ../etc <cmd>` shows a friendly typer error
    rather than a Python traceback. Locks the part 140 meta-audit fix."""
    r = runner.invoke(app, ["--profile", "../etc", "version"])
    assert r.exit_code != 0
    combined = r.stdout + (r.stderr or "")
    assert "invalid profile name" in combined.lower()


def test_bad_alb_profile_env_does_not_block_help(monkeypatch) -> None:
    """ALB_PROFILE env validation is LAZY (deferred to load_active call).

    A leftover/stale env var should not block `alb --help` or
    `alb <subcmd> --help`. The env is caught when subcommands actually
    invoke `load_active()` (e.g. `alb status`), via `profile_path()`
    raising `InvalidProfileName`.
    """
    monkeypatch.setenv("ALB_PROFILE", "../etc")
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0
    r2 = runner.invoke(app, ["fs", "--help"])
    assert r2.exit_code == 0


def test_bad_alb_profile_env_lazy_raise_in_subcommand(monkeypatch) -> None:
    """When a subcommand actually loads the profile, bad env raises."""
    from alb.infra.config import load_profile
    from alb.infra.workspace import InvalidProfileName

    monkeypatch.setenv("ALB_PROFILE", "../etc")
    with pytest.raises(InvalidProfileName):
        load_profile()  # picks up ALB_PROFILE from env when name=None


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
