"""Tests for `alb setup serial --save` config-persistence helper.

We test the helper directly rather than driving the whole `setup_serial`
flow because the latter spins up a real :class:`SerialTransport` health
check that requires a TCP listener / device.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from alb.cli.setup_cli import _persist_serial_config


@pytest.fixture(autouse=True)
def _isolated_config(monkeypatch, tmp_path: Path):
    """Redirect ALB_CONFIG so each test gets a fresh config.toml location."""
    monkeypatch.setenv("ALB_CONFIG", str(tmp_path / "config.toml"))
    yield


def _read(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def test_persist_creates_file_and_section(tmp_path: Path) -> None:
    """No prior config → helper creates the file with [transport.serial]."""
    path = _persist_serial_config(
        {"default_tcp_host": "localhost", "default_tcp_port": 19001, "default_baud": 1500000}
    )
    assert path.exists()
    raw = _read(path)
    assert raw["transport"]["serial"]["default_tcp_host"] == "localhost"
    assert raw["transport"]["serial"]["default_tcp_port"] == 19001
    assert raw["transport"]["serial"]["default_baud"] == 1500000


def test_persist_merges_with_existing_sibling_sections(tmp_path: Path) -> None:
    """An existing [transport.adb] / [permissions] must survive untouched."""
    path = Path(tmp_path / "config.toml")
    path.write_text(
        '[transport.adb]\nbin_path = "/usr/local/bin/adb"\n'
        'server_socket = "tcp:localhost:5037"\n\n'
        "[permissions]\nmode = \"strict\"\n"
    )
    _persist_serial_config({"default_tcp_port": 19001, "default_baud": 1500000})
    raw = _read(path)
    # Sibling sections survived
    assert raw["transport"]["adb"]["bin_path"] == "/usr/local/bin/adb"
    assert raw["transport"]["adb"]["server_socket"] == "tcp:localhost:5037"
    assert raw["permissions"]["mode"] == "strict"
    # New serial section added
    assert raw["transport"]["serial"]["default_tcp_port"] == 19001
    assert raw["transport"]["serial"]["default_baud"] == 1500000


def test_persist_preserves_unrelated_serial_keys(tmp_path: Path) -> None:
    """User-set [transport.serial.prompts] and handshake_timeout must survive."""
    path = Path(tmp_path / "config.toml")
    path.write_text(
        "[transport.serial]\n"
        "default_baud = 115200\n"
        "handshake_timeout = 4.5\n\n"
        '[transport.serial.prompts]\n'
        'shell = "[\\\\$#] $"\n'
    )
    _persist_serial_config({"default_baud": 1500000})  # only update baud
    raw = _read(path)
    assert raw["transport"]["serial"]["default_baud"] == 1500000
    assert raw["transport"]["serial"]["handshake_timeout"] == 4.5
    assert raw["transport"]["serial"]["prompts"]["shell"] == "[\\$#] $"


def test_persist_overwrites_existing_value(tmp_path: Path) -> None:
    """Re-running with a different --tcp-port must replace the old value."""
    path = Path(tmp_path / "config.toml")
    path.write_text("[transport.serial]\ndefault_tcp_port = 9001\n")
    _persist_serial_config({"default_tcp_port": 19001})
    raw = _read(path)
    assert raw["transport"]["serial"]["default_tcp_port"] == 19001


def test_persist_none_value_removes_key(tmp_path: Path) -> None:
    """Passing None for a key drops it from the file (let defaults take over)."""
    path = Path(tmp_path / "config.toml")
    path.write_text(
        "[transport.serial]\n"
        "default_baud = 1500000\n"
        "pty_link_dir = \"/some/path\"\n"
    )
    _persist_serial_config({"pty_link_dir": None})
    raw = _read(path)
    assert raw["transport"]["serial"]["default_baud"] == 1500000
    assert "pty_link_dir" not in raw["transport"]["serial"]


def test_persist_refuses_to_clobber_invalid_toml(tmp_path: Path) -> None:
    """If the file is malformed TOML, abort instead of silently destroying it."""
    import typer

    path = Path(tmp_path / "config.toml")
    path.write_text("this is not [valid TOML\n")
    with pytest.raises(typer.BadParameter, match="not valid TOML"):
        _persist_serial_config({"default_tcp_port": 19001})
    # File contents untouched
    assert "not [valid TOML" in path.read_text()


def test_persist_does_not_clobber_unrelated_serial_keys(tmp_path: Path) -> None:
    """Re-running --save in device mode must NOT zero out pty_link_dir.

    Locks against a previous foot-gun where device-mode --save force-set
    pty_link_dir = "" — clobbering whatever the user had previously
    configured. _persist_serial_config now only writes what the caller
    explicitly passed in `updates`.
    """
    path = Path(tmp_path / "config.toml")
    path.write_text(
        "[transport.serial]\n"
        'pty_link_dir = "/srv/pty/cache"\n'
        "handshake_timeout = 4.5\n"
    )
    # Simulate device-mode --save: only pass `default_baud`, nothing else.
    _persist_serial_config({"default_baud": 921600})
    raw = _read(path)
    assert raw["transport"]["serial"]["default_baud"] == 921600
    assert raw["transport"]["serial"]["pty_link_dir"] == "/srv/pty/cache"
    assert raw["transport"]["serial"]["handshake_timeout"] == 4.5


def test_persist_creates_parent_directory(tmp_path: Path, monkeypatch) -> None:
    """When XDG dir doesn't exist yet, helper mkdirs it."""
    target = tmp_path / "deep" / "nested" / "config.toml"
    monkeypatch.setenv("ALB_CONFIG", str(target))
    path = _persist_serial_config({"default_tcp_port": 19001})
    assert path == target
    assert path.exists()
    assert path.parent.is_dir()
