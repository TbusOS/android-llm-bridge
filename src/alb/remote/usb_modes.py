"""What mode is the board in — read from the bench host's USB listing (ADR-059).

The agent's `usb-list` op returns raw facts only: name, VID:PID, serial,
driver status and the USB class triples (Windows "CompatibleID"). Deciding
what those mean happens here, on the hub, for two reasons:

* The table grows. A new vendor's download mode is one row here, not a new
  agent on a machine someone has to walk over to.
* It answers the question the bench could not answer on 2026-10-14: a board
  confirmed (over UART) to be sitting in its bootloader's fastboot, and
  `fastboot devices` on the bench host listing nothing. The cable not being
  on that host and the driver not being installed look identical from
  fastboot's side; they are different rows in this listing.

adb and fastboot are recognised by the interface class triple, not by
VID:PID — every vendor ships its own VID. AOSP's own matching rules:
fastboot accepts an interface with class 0xff / subclass 0x42 / protocol
0x03 (system/core/fastboot/fastboot.cpp, the interface filter), adb uses
0xff / 0x42 / 0x01 (packages/modules/adb/adb.h, ADB_CLASS / ADB_SUBCLASS /
ADB_PROTOCOL). That triple is in the CompatibleID even when no driver is
bound, which is exactly the case that needs diagnosing.

Download modes have no class convention, so those go by VID:PID; each row
names its source (the usb.ids database shipped with usbutils).
"""

from __future__ import annotations

from typing import Any

_FASTBOOT_TRIPLE = "class_ff&subclass_42&prot_03"
_ADB_TRIPLE = "class_ff&subclass_42&prot_01"

# (vid, pid or "" for any) -> (mode, what it is). Source for each: usb.ids.
_DOWNLOAD_MODES: dict[tuple[str, str], tuple[str, str]] = {
    # usb.ids: 05c6 Qualcomm, Inc. / 9008 "Gobi Wireless Modem (QDL mode)"
    ("05c6", "9008"): ("qualcomm-edl", "Qualcomm emergency download (9008)"),
    # usb.ids: 0e8d MediaTek Inc. / 2000 "MT65xx Preloader"
    ("0e8d", "2000"): ("mediatek-preloader", "MediaTek preloader download"),
    # usb.ids: 2207 Fuzhou Rockchip Electronics Company — every PID listed
    # there is "<chip> in Mask ROM mode"
    ("2207", ""): ("rockchip-usb", "Rockchip Mask ROM / loader"),
    # usb.ids: 1f3a / efe8 "V972 tablet in flashing mode" (the FEL mode PID)
    ("1f3a", "efe8"): ("allwinner-fel", "Allwinner FEL download"),
}

# Windows Device Manager problem codes that matter here (Microsoft's
# "Error messages in Device Manager" reference). 28 is the one this module
# exists for: the device enumerated, no driver was installed for it.
_PROBLEMS = {
    10: "the device cannot start",
    22: "the device is disabled",
    28: "no driver is installed",
    31: "the driver failed to load",
    43: "Windows stopped the device after it reported problems",
    52: "the driver's signature could not be verified",
}

_HINTS = {
    ("fastboot", "missing"): "a board is in fastboot but no driver is bound on the bench host — "
    "install the Android bootloader (WinUSB) driver there; fastboot cannot see it until then",
    ("adb", "missing"): "a board offers adb but no driver is bound on the bench host — "
    "install the Android ADB (WinUSB) driver there",
    ("qualcomm-edl", "any"): "the board is in emergency download mode — adb and fastboot cannot "
    "reach it; flashing needs the vendor's download tool",
    ("mediatek-preloader", "any"): "the board is in the MediaTek download path — "
    "adb and fastboot cannot reach it",
    ("rockchip-usb", "any"): "the board is in Rockchip Mask ROM / loader mode — "
    "adb and fastboot cannot reach it",
    (
        "allwinner-fel",
        "any",
    ): "the board is in Allwinner FEL mode — adb and fastboot cannot reach it",
}


def driver_state(dev: dict[str, Any]) -> str:
    """ok / missing / problem / unknown, from the problem code."""
    problem = dev.get("problem")
    if problem is None:
        return "unknown" if not dev.get("service") else "ok"
    if problem == 0:
        return "ok"
    if problem == 28:
        return "missing"
    return "problem"


def classify(dev: dict[str, Any]) -> dict[str, str]:
    """{mode, what, driver, problem, hint} for one listing entry."""
    ids = [str(c).lower() for c in dev.get("compatible_ids") or []]
    vid, pid = str(dev.get("vid") or "").lower(), str(dev.get("pid") or "").lower()
    mode, what = "other", ""
    if any(_FASTBOOT_TRIPLE in c for c in ids):
        mode, what = "fastboot", "Android fastboot interface"
    elif any(_ADB_TRIPLE in c for c in ids):
        mode, what = "adb", "Android adb interface"
    else:
        hit = _DOWNLOAD_MODES.get((vid, pid)) or _DOWNLOAD_MODES.get((vid, ""))
        if hit:
            mode, what = hit
    driver = driver_state(dev)
    problem = dev.get("problem")
    hint = _HINTS.get((mode, driver)) or _HINTS.get((mode, "any"), "")
    if driver == "problem" and isinstance(problem, int):
        hint = hint or f"driver problem {problem}: {_PROBLEMS.get(problem, 'see Device Manager')}"
    return {
        "mode": mode,
        "what": what,
        "driver": driver,
        "problem": _PROBLEMS.get(problem, "") if isinstance(problem, int) else "",
        "hint": hint,
    }


def annotate(data: dict[str, Any]) -> dict[str, Any]:
    """Add `mode` etc. to every device and a `boards` summary: the entries
    that are a board in a mode alb knows, one per physical device (a
    composite adb device lists a parent and an interface child — the
    interface carries the class triple, the parent the serial)."""
    devices = [d for d in data.get("devices") or [] if isinstance(d, dict)]
    boards: dict[tuple[str, str, str], dict[str, Any]] = {}
    for dev in devices:
        info = classify(dev)
        dev.update(info)
        if info["mode"] == "other":
            continue
        key = (str(dev.get("vid") or ""), str(dev.get("pid") or ""), _parent_serial(devices, dev))
        current = boards.get(key)
        # Prefer the entry that shows a driver problem — that is the news.
        if current is None or (current["driver"] == "ok" and info["driver"] != "ok"):
            boards[key] = {
                "mode": info["mode"],
                "what": info["what"],
                "driver": info["driver"],
                "hint": info["hint"],
                "vid": key[0],
                "pid": key[1],
                "serial": key[2],
                "name": str(dev.get("name") or ""),
            }
    return {**data, "devices": devices, "boards": list(boards.values())}


def _parent_serial(devices: list[dict[str, Any]], dev: dict[str, Any]) -> str:
    """An interface child (`&MI_01`) carries a Windows-generated instance
    suffix, not the board's serial; the parent with the same VID:PID does."""
    if not dev.get("interface"):
        return str(dev.get("serial") or "")
    for other in devices:
        if (
            not other.get("interface")
            and other.get("vid") == dev.get("vid")
            and other.get("pid") == dev.get("pid")
        ):
            return str(other.get("serial") or "")
    return str(dev.get("serial") or "")
