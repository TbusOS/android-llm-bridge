"""Reading a board's mode from the bench host's USB listing (ADR-059).

The case this exists for (2026-10-14): a board confirmed over UART to be in
its bootloader's fastboot, and fastboot on the bench host listing nothing.
"Driver not installed" and "cable not on this host" look the same to
fastboot; they must not look the same here.
"""

from __future__ import annotations

from alb.remote.usb_modes import annotate, classify


def _dev(**kw) -> dict:
    base = {
        "name": "",
        "instance_id": "",
        "vid": "",
        "pid": "",
        "serial": "",
        "interface": "",
        "status": "OK",
        "problem": 0,
        "class": "",
        "service": "WinUSB",
        "manufacturer": "",
        "compatible_ids": [],
    }
    return {**base, **kw}


FASTBOOT_IDS = ["USB\\Class_ff&SubClass_42&Prot_03", "USB\\Class_ff&SubClass_42", "USB\\Class_ff"]
ADB_IDS = ["USB\\Class_ff&SubClass_42&Prot_01", "USB\\Class_ff&SubClass_42", "USB\\Class_ff"]


def test_fastboot_without_a_driver_says_so():
    """The 2026-10-14 case: the class triple is there, no driver is bound."""
    info = classify(
        _dev(vid="abcd", pid="1234", compatible_ids=FASTBOOT_IDS, problem=28, service="")
    )
    assert info["mode"] == "fastboot"
    assert info["driver"] == "missing"
    assert "driver" in info["hint"]


def test_fastboot_is_found_by_class_not_by_vendor_id():
    """Vendors ship their own VID; the class triple is what fastboot itself
    matches on."""
    assert classify(_dev(vid="1234", pid="5678", compatible_ids=FASTBOOT_IDS))["mode"] == "fastboot"


def test_class_match_is_case_insensitive():
    ids = ["usb\\class_FF&subclass_42&prot_03"]
    assert classify(_dev(compatible_ids=ids))["mode"] == "fastboot"


def test_adb_interface():
    assert classify(_dev(compatible_ids=ADB_IDS))["mode"] == "adb"


def test_download_modes_by_vid_pid():
    assert classify(_dev(vid="05c6", pid="9008"))["mode"] == "qualcomm-edl"
    assert classify(_dev(vid="0e8d", pid="2000"))["mode"] == "mediatek-preloader"
    assert classify(_dev(vid="2207", pid="350b"))["mode"] == "rockchip-usb"
    assert classify(_dev(vid="1f3a", pid="efe8"))["mode"] == "allwinner-fel"


def test_an_ordinary_device_is_other_with_no_hint():
    info = classify(_dev(vid="046d", pid="c52b", name="USB Receiver"))
    assert info["mode"] == "other" and info["hint"] == ""


def test_a_driver_problem_other_than_missing_is_named():
    info = classify(_dev(vid="05c6", pid="9008", problem=10))
    assert info["driver"] == "problem"
    assert info["problem"] == "the device cannot start"


def test_unknown_problem_code_without_service():
    assert classify(_dev(problem=None, service=""))["driver"] == "unknown"


def test_boards_summary_uses_the_parent_serial_for_a_composite_device():
    """A composite adb device: the parent carries the board's serial, the
    MI_01 child carries the class triple and a Windows-made suffix."""
    data = {
        "devices": [
            _dev(vid="18d1", pid="4ee7", serial="BOARD123", name="Android Composite"),
            _dev(
                vid="18d1",
                pid="4ee7",
                serial="6&2b0c1a&0&0001",
                interface="01",
                compatible_ids=ADB_IDS,
                name="ADB Interface",
            ),
            _dev(vid="046d", pid="c52b", name="USB Receiver"),
        ]
    }
    out = annotate(data)
    assert len(out["devices"]) == 3
    assert [b["serial"] for b in out["boards"]] == ["BOARD123"]
    assert out["boards"][0]["mode"] == "adb"


def test_no_board_means_an_empty_summary():
    out = annotate({"devices": [_dev(vid="046d", pid="c52b")]})
    assert out["boards"] == []
    assert out["devices"][0]["mode"] == "other"
