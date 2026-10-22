"""Tests for the shared transport factory (round11 SEC-2).

build_transport gates a non-empty device_serial through is_safe_device so
every entry point (api / cli / mcp) is protected at the root — a malformed
serial can never reach a transport's argv. None (env-default device) and
well-formed serials are always allowed.
"""

from __future__ import annotations

import pytest

from alb.infra.workspace import InvalidDeviceSerial
from alb.transport.factory import build_transport


@pytest.mark.parametrize(
    "bad",
    [
        "x; reboot",
        "$(reboot)",
        "a b",  # space
        "../../etc/passwd",
        "dev`id`",
        "dev|nc",
        "-bad-leading-dash",  # regex requires alnum first char
    ],
)
def test_build_transport_rejects_unsafe_serial(bad: str) -> None:
    with pytest.raises(InvalidDeviceSerial):
        build_transport(override="adb", device_serial=bad)


def test_build_transport_allows_none_device() -> None:
    # None = env-default device; must NOT raise the serial gate.
    t = build_transport(override="adb", device_serial=None)
    assert t.name == "adb"


@pytest.mark.parametrize(
    "good",
    ["emulator-5554", "0123456789ABCDEF", "192.168.1.5:5555", "ABC.def-1"],
)
def test_build_transport_allows_wellformed_serial(good: str) -> None:
    t = build_transport(override="adb", device_serial=good)
    assert t.name == "adb"


# ─── serial_fallback_for (2026-10-23) ──────────────────────────────
def test_serial_fallback_only_when_adb_was_the_default(monkeypatch) -> None:
    from alb.transport import factory

    monkeypatch.delenv("ALB_TRANSPORT", raising=False)
    monkeypatch.setattr(
        factory, "active_settings",
        lambda force_reload=False: type("S", (), {"primary_transport": "adb"})(),
    )
    monkeypatch.setattr(
        factory, "build_transport",
        lambda **kw: type("T", (), {"name": kw.get("override")})(),
    )
    t = factory.serial_fallback_for()
    assert t is not None and t.name == "serial"
    # Anything that chose the transport or the device turns it off.
    assert factory.serial_fallback_for(override="adb") is None
    assert factory.serial_fallback_for(device_serial="abc123") is None
    monkeypatch.setenv("ALB_TRANSPORT", "adb")
    assert factory.serial_fallback_for() is None


def test_serial_fallback_off_when_default_is_not_adb(monkeypatch) -> None:
    from alb.transport import factory

    monkeypatch.delenv("ALB_TRANSPORT", raising=False)
    monkeypatch.setattr(
        factory, "active_settings",
        lambda force_reload=False: type("S", (), {"primary_transport": "serial"})(),
    )
    assert factory.serial_fallback_for() is None
