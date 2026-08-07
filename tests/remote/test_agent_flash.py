"""Agent-side flash job (ADR-056), loaded by path like the other agent tests.

Focus is the security boundary and the refuse-before-writing rule — the two
places where a mistake is expensive rather than annoying: the agent assembles
its own argv, and it never invokes fastboot on an image it has not verified.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import struct
import sys
from pathlib import Path

import pytest

_AGENT_PATH = Path(__file__).resolve().parents[2] / "clients" / "windows-agent" / "alb_agent.py"


def _load_agent():
    spec = importlib.util.spec_from_file_location("alb_agent_flash_under_test", _AGENT_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


agent = _load_agent()

_HDR = struct.Struct(">cI")


def _frame(kind: bytes, payload: bytes) -> bytes:
    return _HDR.pack(kind, len(payload)) + payload


class _FakeWs:
    """Feeds canned frames to the agent and records what it sends back."""

    def __init__(self, incoming: bytes, *, chunk: int = 4096) -> None:
        self._data = incoming
        self._chunk = chunk
        self._pos = 0
        self.sent: list[dict] = []

    async def recv(self) -> bytes:
        if self._pos >= len(self._data):
            return b""
        out = self._data[self._pos : self._pos + self._chunk]
        self._pos += len(out)
        return out

    async def send(self, data: bytes) -> None:
        kind, length = _HDR.unpack(data[:5])
        assert kind == b"C", "the agent must only send control frames"
        self.sent.append(json.loads(data[5 : 5 + length]))

    def events(self, ev: str) -> list[dict]:
        return [m for m in self.sent if m.get("ev") == ev]

    def done(self) -> dict:
        msgs = self.events("done")
        assert msgs, f"no done frame; got {self.sent}"
        return msgs[-1]


# ── partition names are argv elements, so the shape check is a control ──


@pytest.mark.parametrize("name", ["boot", "vendor_cfg", "super.img", "a-b", "x" * 64])
def test_partition_names_accepted(name):
    assert agent._partition_allowed(name)


@pytest.mark.parametrize(
    "name",
    [
        "",
        "../../etc/passwd",  # path escape
        "boot/../x",
        "boot partition",  # a space would split into two argv elements
        "-w",  # a leading dash reads as a flag, not a partition
        "boot;rm -rf /",
        "x" * 65,
        ".hidden",
    ],
)
def test_partition_names_rejected(name):
    assert not agent._partition_allowed(name)


def test_allowlist_narrows_further(monkeypatch):
    monkeypatch.setattr(agent, "_FLASH_PARTITIONS", frozenset({"vendor_cfg"}))
    assert agent._partition_allowed("vendor_cfg")
    assert not agent._partition_allowed("boot")  # well-formed but not allowed


def test_empty_allowlist_means_any_well_formed(monkeypatch):
    monkeypatch.setattr(agent, "_FLASH_PARTITIONS", frozenset())
    assert agent._partition_allowed("boot")


# ── capability advertisement ────────────────────────────────────────────


def test_hello_advertises_fastboot_only_when_present(monkeypatch):
    """The hub answers "can this bench flash?" from caps, so claiming the
    capability without the tool would trade an instant answer for a timeout."""
    monkeypatch.setattr(agent, "_FASTBOOT_PATH", "/usr/bin/fastboot")
    assert "fastboot" in json.loads(agent._hello("a", "n", None))["caps"]
    monkeypatch.setattr(agent, "_FASTBOOT_PATH", "")
    caps = json.loads(agent._hello("a", "n", None))["caps"]
    assert caps == ["adb"]


def test_resolve_fastboot_rejects_a_configured_path_that_is_absent(tmp_path):
    assert agent._resolve_fastboot(str(tmp_path / "nope.exe")) == ""


def test_resolve_fastboot_takes_configured_path_over_PATH(tmp_path):
    exe = tmp_path / "fastboot"
    exe.write_text("#!/bin/sh\n")
    assert agent._resolve_fastboot(str(exe)) == str(exe)


# ── the refuse-before-writing rule ──────────────────────────────────────


async def _run_flash(ws, monkeypatch, *, fastboot="/nonexistent/fastboot", present=True):
    ran: list[list[str]] = []

    async def fake_run(_ws, argv, op=""):
        ran.append(argv)
        return 0, "OKAY", ""

    async def fake_present(_fastboot):
        return present

    monkeypatch.setattr(agent, "_run_fastboot", fake_run)
    # Default to "a device is there" so each test exercises the path it names;
    # the no-device path has its own tests below.
    monkeypatch.setattr(agent, "_device_present", fake_present)
    reader = agent._JobReader(ws)
    req = await reader.read_control()
    await agent._job_flash(ws, reader, fastboot, req)
    return ran


async def test_digest_mismatch_never_invokes_fastboot(monkeypatch):
    """The whole point of sending the digest up front: a corrupt transfer
    must cost a retry, not a half-written partition."""
    img = b"A" * 64
    wire = _frame(
        b"C",
        json.dumps({"op": "flash", "partition": "boot", "size": 64, "sha256": "00" * 32}).encode(),
    ) + _frame(b"D", img)
    ws = _FakeWs(wire)
    ran = await _run_flash(ws, monkeypatch)
    assert ran == [], "fastboot must not run on an unverified image"
    done = ws.done()
    assert done["ok"] is False
    assert done["code"] == "FLASH_IMAGE_CORRUPT"
    assert "nothing was written" in done["error"]


async def test_short_transfer_is_an_error_not_a_flash(monkeypatch):
    img = b"B" * 10
    wire = _frame(
        b"C",
        json.dumps({"op": "flash", "partition": "boot", "size": 64, "sha256": "00" * 32}).encode(),
    ) + _frame(b"D", img)
    ws = _FakeWs(wire)
    ran = await _run_flash(ws, monkeypatch)
    assert ran == []
    assert ws.done()["code"] == "FLASH_IMAGE_CORRUPT"


async def test_good_image_reaches_fastboot_with_agent_built_argv(monkeypatch):
    img = b"payload-bytes" * 8
    wire = _frame(
        b"C",
        json.dumps(
            {
                "op": "flash",
                "partition": "vendor_cfg",
                "size": len(img),
                "sha256": hashlib.sha256(img).hexdigest(),
            }
        ).encode(),
    ) + _frame(b"D", img)
    ws = _FakeWs(wire, chunk=7)  # exercise reassembly across small chunks
    ran = await _run_flash(ws, monkeypatch, fastboot="/opt/fastboot")
    assert len(ran) == 1
    argv = ran[0]
    # argv is assembled by the agent: its own executable, the vetted partition
    # name, and a temp path the hub never saw.
    assert argv[0] == "/opt/fastboot"
    assert argv[1:3] == ["flash", "vendor_cfg"]
    assert argv[3].endswith("image.bin")
    assert ws.done()["ok"] is True
    assert ws.events("progress"), "transfer progress must be reported"


async def test_rejected_partition_never_transfers(monkeypatch):
    wire = _frame(
        b"C",
        json.dumps(
            {"op": "flash", "partition": "../../etc/x", "size": 4, "sha256": "00" * 32}
        ).encode(),
    )
    ws = _FakeWs(wire)
    ran = await _run_flash(ws, monkeypatch)
    assert ran == []
    assert ws.done()["code"] == "FLASH_PARTITION_REJECTED"
    assert not ws.events("accepted"), "a rejected job must not be accepted first"


async def test_reboot_target_allowlist(monkeypatch):
    ran: list[list[str]] = []

    async def fake_run(_ws, argv, op=""):
        ran.append(argv)
        return 0, "", ""

    async def yes(_fastboot):
        return True

    monkeypatch.setattr(agent, "_run_fastboot", fake_run)
    monkeypatch.setattr(agent, "_device_present", yes)
    ws = _FakeWs(b"")
    await agent._job_reboot(ws, "/opt/fastboot", "definitely-not-a-mode")
    assert ran == []
    assert ws.done()["ok"] is False

    ws2 = _FakeWs(b"")
    await agent._job_reboot(ws2, "/opt/fastboot", "")
    assert ran == [["/opt/fastboot", "reboot"]]  # plain reboot = back to system
    assert ws2.done()["ok"] is True


async def test_devices_with_no_output_reports_no_device(monkeypatch):
    """rc 0 with an empty listing is fastboot's way of saying "nothing here" —
    reporting that as success would send the caller off to flash nothing."""

    async def fake_run(_ws, argv, op=""):
        return 0, "   \n", ""

    monkeypatch.setattr(agent, "_run_fastboot", fake_run)
    ws = _FakeWs(b"")
    await agent._job_devices(ws, "/opt/fastboot")
    assert ws.done()["code"] == "FASTBOOT_NO_DEVICE"


async def test_devices_with_a_listing_is_success(monkeypatch):
    async def fake_run(_ws, argv, op=""):
        return 0, "2870000540\tfastboot\n", ""

    monkeypatch.setattr(agent, "_run_fastboot", fake_run)
    ws = _FakeWs(b"")
    await agent._job_devices(ws, "/opt/fastboot")
    done = ws.done()
    assert done["ok"] is True
    assert done["code"] == ""


# ── never block on a device that is not there ───────────────────────────
#
# Real-hardware regression (2026-08-07): a web click ran `fastboot reboot`
# while the board was still in Android. fastboot printed
# "< waiting for any device >" and sat there, holding the single-job lock
# until the hub's 30-minute ceiling — a bench taken out of service by a
# command that was never going to succeed.


async def test_reboot_refuses_when_no_device_is_in_fastboot(monkeypatch):
    ran: list[list[str]] = []

    async def fake_run(_ws, argv, op=""):
        ran.append(argv)
        return 0, "", ""

    monkeypatch.setattr(agent, "_run_fastboot", fake_run)

    async def no_device(_fastboot):
        return False

    monkeypatch.setattr(agent, "_device_present", no_device)
    ws = _FakeWs(b"")
    await agent._job_reboot(ws, "/opt/fastboot", "")
    assert ran == [], "must not invoke a command that would block"
    done = ws.done()
    assert done["code"] == "FASTBOOT_NO_DEVICE"
    assert not ws.events("accepted")


async def test_flash_refuses_before_receiving_the_image(monkeypatch):
    """Checked BEFORE the transfer: streaming megabytes to a bench whose
    board is not in fastboot wastes the tunnel and fails anyway."""
    img = b"z" * 32
    wire = _frame(
        b"C",
        json.dumps(
            {
                "op": "flash",
                "partition": "boot",
                "size": len(img),
                "sha256": hashlib.sha256(img).hexdigest(),
            }
        ).encode(),
    ) + _frame(b"D", img)
    ws = _FakeWs(wire)

    ran = await _run_flash(ws, monkeypatch, present=False)
    assert ran == []
    done = ws.done()
    assert done["code"] == "FASTBOOT_NO_DEVICE"
    assert "nothing was transferred" in done["error"]


async def test_flash_proceeds_when_a_device_is_present(monkeypatch):
    img = b"y" * 16
    wire = _frame(
        b"C",
        json.dumps(
            {
                "op": "flash",
                "partition": "boot",
                "size": len(img),
                "sha256": hashlib.sha256(img).hexdigest(),
            }
        ).encode(),
    ) + _frame(b"D", img)
    ws = _FakeWs(wire)

    ran = await _run_flash(ws, monkeypatch, fastboot="/opt/fastboot", present=True)
    assert len(ran) == 1


def test_every_op_has_a_timeout():
    """A missing entry silently falls back to 300 s — long enough for a
    'waiting for device' hang to look like a working command."""
    assert set(agent._FASTBOOT_TIMEOUT_S) == {"devices", "reboot", "flash"}
    # a query must answer promptly; a partition write may legitimately take minutes
    assert agent._FASTBOOT_TIMEOUT_S["devices"] < agent._FASTBOOT_TIMEOUT_S["flash"]
