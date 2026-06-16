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
