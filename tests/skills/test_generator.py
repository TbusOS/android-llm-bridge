"""Tests for the SKILL.md generator."""

from __future__ import annotations

import json
from pathlib import Path

from alb.skills.generator import dump_registry_json, generate, render


def test_render_mentions_all_transports() -> None:
    out = render()
    assert "name: android-llm-bridge" in out
    assert "## Supported transports" in out
    for t in ("adb", "ssh", "serial", "hybrid"):
        assert t in out


def test_render_mentions_all_m1_capabilities() -> None:
    out = render()
    for cap in ("shell", "logging", "filesync", "diagnose", "power", "app"):
        assert f"`{cap}`" in out


def test_render_includes_error_codes_table() -> None:
    out = render()
    assert "## Error codes" in out
    assert "PERMISSION_DENIED" in out
    assert "TRANSPORT_NOT_CONFIGURED" in out


def test_generate_writes_file(tmp_path: Path) -> None:
    dest = tmp_path / "SKILL.md"
    result = generate(dest)
    assert result == dest
    assert result.exists()
    content = result.read_text()
    assert "android-llm-bridge" in content
    assert "Capabilities" in content


def test_dump_registry_json_is_valid(tmp_path: Path) -> None:
    path = dump_registry_json(tmp_path / "SKILL.json")
    payload = json.loads(path.read_text())
    assert "transports" in payload
    assert "capabilities" in payload
    assert any(c["name"] == "shell" for c in payload["capabilities"])
    assert any(t["name"] == "adb" for t in payload["transports"])
