"""Tests for config loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from alb.infra.config import load_active, load_config, load_profile


def test_load_config_returns_defaults_when_absent(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALB_CONFIG", str(tmp_path / "does_not_exist.toml"))
    cfg = load_config()
    assert cfg.default_profile == "default"
    assert cfg.adb.bin_path == "adb"
    assert cfg.permissions.mode == "standard"


def test_load_config_reads_toml(monkeypatch, tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        """
        default_profile = "work"

        [workspace]
        root = "/tmp/alb"

        [transport.adb]
        bin_path = "/opt/adb"
        server_socket = "tcp:localhost:5037"

        [permissions]
        mode = "strict"
        """
    )
    monkeypatch.setenv("ALB_CONFIG", str(cfg_file))
    cfg = load_config()
    assert cfg.default_profile == "work"
    assert cfg.adb.bin_path == "/opt/adb"
    assert cfg.adb.server_socket == "tcp:localhost:5037"
    assert cfg.permissions.mode == "strict"


def test_load_config_parses_serial_prompts_override(
    monkeypatch, tmp_path: Path
) -> None:
    """[transport.serial.prompts] in TOML becomes SerialConfig.prompts."""
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        """
        [transport.serial]
        default_baud = 1500000
        handshake_timeout = 3.5

        [transport.serial.prompts]
        shell_root = "myboard:~\\\\s*#\\\\s*$"
        uboot = "MYBOOT>\\\\s*$"
        """
    )
    monkeypatch.setenv("ALB_CONFIG", str(cfg_file))
    cfg = load_config()
    assert cfg.serial.default_baud == 1500000
    assert cfg.serial.handshake_timeout == 3.5
    assert cfg.serial.prompts == {
        "shell_root": r"myboard:~\s*#\s*$",
        "uboot": r"MYBOOT>\s*$",
    }


def test_load_config_serial_prompts_default_empty(
    monkeypatch, tmp_path: Path
) -> None:
    """Config without [transport.serial.prompts] yields empty dict."""
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("[transport.serial]\ndefault_baud = 115200\n")
    monkeypatch.setenv("ALB_CONFIG", str(cfg_file))
    cfg = load_config()
    assert cfg.serial.prompts == {}
    assert cfg.serial.handshake_timeout == 2.0   # default


def test_load_profile_returns_empty_when_absent(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    prof = load_profile("nonexistent")
    assert prof.name == "nonexistent"
    assert prof.devices == []


def test_load_profile_parses_devices(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    prof_dir = tmp_path / "profiles"
    prof_dir.mkdir()
    (prof_dir / "lab.toml").write_text(
        """
        [profile]
        name = "lab"
        primary_transport = "adb"

        [[devices]]
        serial = "abc123"
        alias = "lab-a"
        transport = "adb"

        [[devices]]
        serial = "def456"
        alias = "lab-b"
        transport = "ssh"
        ssh_host = "192.168.1.42"
        """
    )
    prof = load_profile("lab")
    assert prof.name == "lab"
    assert len(prof.devices) == 2
    assert prof.devices[0].serial == "abc123"
    assert prof.devices[1].ssh_host == "192.168.1.42"


def test_load_active_composes(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("ALB_CONFIG", str(tmp_path / "no-config.toml"))
    monkeypatch.setenv("ALB_PROFILE", "default")
    active = load_active()
    assert active.config.default_profile == "default"
    assert active.profile.name == "default"
    assert active.primary_transport == "adb"
