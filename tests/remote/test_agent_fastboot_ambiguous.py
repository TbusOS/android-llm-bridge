"""More than one board in fastboot on the bench host → refuse (ADR-069 A0).

fastboot without -s opens the first device it finds and says nothing about
the others. Two boards in fastboot on one host, and a write meant for one
lands on the other and reports success — the failure that shows no sign.
Until a job can name its board (ADR-069 B1) the agent refuses to guess.

Counted from `fastboot devices` AND the host's USB listing: a board with no
driver (or held open by another program) is missing from `fastboot devices`
on Windows, and it may be the very board the operator meant.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import struct
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_AGENT_PATH = Path(__file__).resolve().parents[2] / "clients" / "windows-agent" / "alb_agent.py"


def _load_agent() -> ModuleType:
    spec = importlib.util.spec_from_file_location("alb_agent_ambiguous_under_test", _AGENT_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


agent = _load_agent()

_HDR = struct.Struct(">cI")


def _frame(kind: bytes, payload: bytes) -> bytes:
    return _HDR.pack(kind, len(payload)) + payload


def _req(**fields: Any) -> bytes:
    return _frame(b"C", json.dumps(fields).encode())


class _FakeWs:
    def __init__(self, incoming: bytes) -> None:
        self._data = incoming
        self._pos = 0
        self.sent: list[dict[str, Any]] = []

    async def recv(self) -> bytes:
        out = self._data[self._pos : self._pos + 4096]
        self._pos += len(out)
        return out

    async def send(self, data: bytes) -> None:
        kind, length = _HDR.unpack(data[:5])
        assert kind == b"C"
        self.sent.append(json.loads(data[5 : 5 + length]))

    def events(self, ev: str) -> list[dict[str, Any]]:
        return [m for m in self.sent if m.get("ev") == ev]

    def done(self) -> dict[str, Any]:
        msgs = self.events("done")
        assert msgs, f"no done frame; got {self.sent}"
        return msgs[-1]


# ── a fake /sys/bus/usb/devices ─────────────────────────────────────────


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{value}\n", encoding="utf-8")


def _sysfs_device(root: Path, name: str, serial: str, prot: str = "03") -> None:
    """One device with one interface of class ff/42/<prot> (03 fastboot,
    01 adb), laid out as real sysfs is: the interface under the device and
    an alias at the root."""
    dev = root / name
    for attr, value in {
        "idVendor": "18d1",
        "idProduct": "d00d" if prot == "03" else "4ee7",
        "product": "Android",
        "serial": serial,
        "bDeviceClass": "00",
        "bDeviceSubClass": "00",
        "bDeviceProtocol": "00",
    }.items():
        _write(dev / attr, value)
    itf = dev / f"{name}:1.0"
    for attr, value in {
        "bInterfaceClass": "ff",
        "bInterfaceSubClass": "42",
        "bInterfaceProtocol": prot,
        "bInterfaceNumber": "00",
    }.items():
        _write(itf / attr, value)
    os.symlink(itf, root / f"{name}:1.0")


@pytest.fixture
def usb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "sys-bus-usb-devices"
    (root / "usb1").mkdir(parents=True)  # a root hub: never a board
    monkeypatch.setattr(agent, "_SYSFS_USB_ROOT", root)
    monkeypatch.setattr(agent.sys, "platform", "linux")
    return root


class _Fastboot:
    """`fastboot devices` lists `listed` boards; records what would run."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, listed: int) -> None:
        self.ran: list[list[str]] = []
        self.listed = listed

        async def fake_run(_ws: Any, argv: list[str], *_a: Any, **_k: Any) -> tuple[int, str, str]:
            self.ran.append(argv)
            return 0, "SER1\tfastboot\n", "OKAY\n"

        async def fake_present(_fastboot: str) -> int:
            return self.listed

        monkeypatch.setattr(agent, "_run_fastboot", fake_run)
        monkeypatch.setattr(agent, "_device_present", fake_present)


def _ops() -> dict[str, Any]:
    table: dict[str, Any] = agent._build_ops("/opt/fastboot")
    return table


# ── counting: one entry per physical board ──────────────────────────────


def test_sysfs_device_and_its_interface_are_one_board(usb: Path) -> None:
    _sysfs_device(usb, "1-3", "1000000003")
    listing = agent._enumerate_sysfs_usb(usb)
    assert len(listing) == 2, "the device and its interface — the shape being folded"
    assert len(agent._fastboot_boards(listing)) == 1


def test_two_sysfs_boards_count_two_and_adb_is_not_fastboot(usb: Path) -> None:
    _sysfs_device(usb, "1-3", "1000000003")
    _sysfs_device(usb, "1-4", "1000000004")
    _sysfs_device(usb, "2-1", "2000000001", prot="01")  # adb, not fastboot
    boards = agent._fastboot_boards(agent._enumerate_sysfs_usb(usb))
    assert sorted(b["instance_id"] for b in boards) == ["1-3", "1-4"]


def test_windows_plain_and_composite_fastboot_count_once_each() -> None:
    triple = ["USB\\Class_ff&SubClass_42&Prot_03", "USB\\Class_ff&SubClass_42", "USB\\Class_ff"]
    listing = agent._parse_usb_json(
        json.dumps(
            [
                {  # a plain fastboot device: the triple is on the device
                    "Name": "Android Bootloader Interface",
                    "PNPDeviceID": "USB\\VID_18D1&PID_D00D\\SER1",
                    "ConfigManagerErrorCode": 0,
                    "Service": "WinUSB",
                    "CompatibleID": triple,
                },
                {  # a composite parent: no class of its own
                    "Name": "USB Composite Device",
                    "PNPDeviceID": "USB\\VID_18D1&PID_4EE0\\SER2",
                    "ConfigManagerErrorCode": 0,
                    "Service": "usbccgp",
                    "CompatibleID": ["USB\\COMPOSITE"],
                },
                {  # ... whose fastboot interface is a child
                    "Name": "Android Bootloader Interface",
                    "PNPDeviceID": "USB\\VID_18D1&PID_4EE0&MI_01\\6&1A2B&0&0001",
                    "ConfigManagerErrorCode": 28,
                    "CompatibleID": triple,
                },
            ]
        )
    )
    boards = agent._fastboot_boards(listing)
    assert len(boards) == 2
    assert "no driver" in agent._usb_board_text(boards[1])


# ── the refusal ─────────────────────────────────────────────────────────


async def test_getvar_refused_when_usb_shows_a_second_board_fastboot_cannot_see(
    usb: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`fastboot devices` lists one; the USB listing shows two. The unseen
    one may be the board meant — and getvar would read the other."""
    _sysfs_device(usb, "1-3", "1000000003")
    _sysfs_device(usb, "1-4", "1000000004")
    fb = _Fastboot(monkeypatch, listed=1)
    ws = _FakeWs(_req(op="getvar", name="product"))
    await agent._run_job(ws, _ops())
    done = ws.done()
    assert done["code"] == "FLASH_DEVICE_AMBIGUOUS", done
    assert "1000000003" in done["error"] and "1000000004" in done["error"]
    assert fb.ran == []
    assert not ws.events("accepted")


async def test_flash_refused_before_a_byte_of_the_image_is_taken(
    usb: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sysfs_device(usb, "1-3", "1000000003")
    _sysfs_device(usb, "1-4", "1000000004")
    fb = _Fastboot(monkeypatch, listed=2)
    img = b"x" * 16
    wire = _req(
        op="flash", partition="boot", size=len(img), sha256=hashlib.sha256(img).hexdigest()
    ) + _frame(b"D", img)
    ws = _FakeWs(wire)
    await agent._run_job(ws, _ops())
    assert ws.done()["code"] == "FLASH_DEVICE_AMBIGUOUS"
    assert not ws.events("accepted"), "refused before the transfer was accepted"
    assert fb.ran == []


@pytest.mark.parametrize("op", [{"op": "erase", "partition": "boot"}, {"op": "reboot"}])
async def test_every_device_op_is_refused(
    usb: Path, monkeypatch: pytest.MonkeyPatch, op: dict[str, str]
) -> None:
    _sysfs_device(usb, "1-3", "1000000003")
    _sysfs_device(usb, "1-4", "1000000004")
    fb = _Fastboot(monkeypatch, listed=2)
    ws = _FakeWs(_req(**op))
    await agent._run_job(ws, _ops())
    assert ws.done()["code"] == "FLASH_DEVICE_AMBIGUOUS"
    assert fb.ran == []


async def test_fastboot_devices_alone_still_refuses_when_usb_cannot_be_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(agent, "_SYSFS_USB_ROOT", tmp_path / "absent")
    monkeypatch.setattr(agent.sys, "platform", "linux")
    fb = _Fastboot(monkeypatch, listed=2)
    ws = _FakeWs(_req(op="getvar", name="product"))
    await agent._run_job(ws, _ops())
    done = ws.done()
    assert done["code"] == "FLASH_DEVICE_AMBIGUOUS"
    assert "could not be read" in done["error"]
    assert fb.ran == []


async def test_one_board_runs_even_when_usb_cannot_be_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(agent, "_SYSFS_USB_ROOT", tmp_path / "absent")
    monkeypatch.setattr(agent.sys, "platform", "linux")
    fb = _Fastboot(monkeypatch, listed=1)
    ws = _FakeWs(_req(op="getvar", name="product"))
    await agent._run_job(ws, _ops())
    assert ws.done()["ok"] is True
    assert fb.ran == [["/opt/fastboot", "getvar", "product"]]


async def test_one_board_in_fastboot_and_one_in_adb_runs(
    usb: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sysfs_device(usb, "1-3", "1000000003")
    _sysfs_device(usb, "1-4", "1000000004", prot="01")
    fb = _Fastboot(monkeypatch, listed=1)
    ws = _FakeWs(_req(op="getvar", name="product"))
    await agent._run_job(ws, _ops())
    assert ws.done()["ok"] is True
    assert len(fb.ran) == 1


async def test_listing_the_devices_is_not_refused(
    usb: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`devices` is how an operator sees the two boards; refusing it would
    hide the very thing the refusal asks them to fix."""
    _sysfs_device(usb, "1-3", "1000000003")
    _sysfs_device(usb, "1-4", "1000000004")
    fb = _Fastboot(monkeypatch, listed=2)
    ws = _FakeWs(_req(op="devices"))
    await agent._run_job(ws, _ops())
    assert ws.done()["ok"] is True
    assert fb.ran == [["/opt/fastboot", "devices"]]


async def test_device_present_counts_the_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Proc:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"SER1\tfastboot\r\nSER2\tfastboot\r\n\r\n", b""

    async def fake_exec(*_argv: str, **_kw: Any) -> _Proc:
        return _Proc()

    monkeypatch.setattr(agent.asyncio, "create_subprocess_exec", fake_exec)
    assert await agent._device_present("/opt/fastboot") == 2
