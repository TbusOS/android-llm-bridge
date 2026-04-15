"""Smoke tests — verify the skeleton imports and basic invariants."""

from __future__ import annotations


def test_package_imports() -> None:
    import alb

    assert alb.__version__
    assert alb.__license__ == "MIT"


def test_registry_non_empty() -> None:
    from alb.infra.registry import CAPABILITIES, TRANSPORTS

    assert len(TRANSPORTS) > 0
    assert len(CAPABILITIES) > 0
    # Required names
    transport_names = {t.name for t in TRANSPORTS}
    assert {"adb", "ssh", "serial"}.issubset(transport_names)

    cap_names = {c.name for c in CAPABILITIES}
    required_caps = {"shell", "logging", "filesync", "diagnose", "power", "app"}
    assert required_caps.issubset(cap_names)


def test_error_codes_registered() -> None:
    from alb.infra.errors import ERROR_CODES, lookup

    assert "TRANSPORT_NOT_CONFIGURED" in ERROR_CODES
    assert "PERMISSION_DENIED" in ERROR_CODES
    spec = lookup("DEVICE_NOT_FOUND")
    assert spec is not None
    assert spec.category == "device"


def test_result_helpers() -> None:
    from alb.infra.result import fail, ok

    r = ok(data={"x": 1})
    assert r.ok
    assert r.to_dict()["data"] == {"x": 1}

    r2 = fail(code="DEVICE_NOT_FOUND", message="x", suggestion="y")
    assert not r2.ok
    assert r2.error is not None
    assert r2.error.code == "DEVICE_NOT_FOUND"
    assert r2.to_dict()["error"]["suggestion"] == "y"


async def test_permission_blocklist() -> None:
    from alb.infra.permissions import default_check

    r = await default_check("adb", "shell.execute", {"cmd": "rm -rf /"})
    assert r.behavior == "deny"
    assert r.matched_rule is not None

    r2 = await default_check("adb", "shell.execute", {"cmd": "ls /sdcard"})
    assert r2.behavior == "allow"


def test_workspace_path_structure() -> None:
    from alb.infra.workspace import iso_timestamp, workspace_path

    p = workspace_path("logs", f"{iso_timestamp()}-test.txt", device="abc")
    assert p.parent.name == "logs"
    assert p.parent.parent.name == "abc"
    assert p.parent.parent.parent.name == "devices"
