"""Agent-side flash job (ADR-056), loaded by path like the other agent tests.

Focus is the security boundary and the refuse-before-writing rule — the two
places where a mistake is expensive rather than annoying: the agent assembles
its own argv, and it never invokes fastboot on an image it has not verified.

Since ADR-059 every op is a row in one table and runs through one dispatcher
(`_run_job`); requests here are built the way the hub sends them. The table
itself (kinds, placeholders, builtins, the lock rule) is pinned in
test_agent_ops.py.
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


def _req(**fields) -> bytes:
    """A job request as the hub sends it: one control frame, flat fields."""
    return _frame(b"C", json.dumps(fields).encode())


def _ops(fastboot: str = "/opt/fastboot"):
    """The op table for a bench whose fastboot lives at `fastboot` (ADR-059)."""
    return agent._build_ops(fastboot)


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
    await agent._run_job(ws, _ops(fastboot))
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
    ws = _FakeWs(_req(op="reboot", target="definitely-not-a-mode"))
    await agent._run_job(ws, _ops())
    assert ran == []
    assert ws.done()["ok"] is False

    ws2 = _FakeWs(_req(op="reboot", target=""))
    await agent._run_job(ws2, _ops())
    assert ran == [["/opt/fastboot", "reboot"]]  # plain reboot = back to system
    assert ws2.done()["ok"] is True


async def test_devices_with_no_output_reports_no_device(monkeypatch):
    """rc 0 with an empty listing is fastboot's way of saying "nothing here" —
    reporting that as success would send the caller off to flash nothing."""

    async def fake_run(_ws, argv, op=""):
        return 0, "   \n", ""

    monkeypatch.setattr(agent, "_run_fastboot", fake_run)
    ws = _FakeWs(_req(op="devices"))
    await agent._run_job(ws, _ops())
    assert ws.done()["code"] == "FASTBOOT_NO_DEVICE"


async def test_devices_with_a_listing_is_success(monkeypatch):
    async def fake_run(_ws, argv, op=""):
        return 0, "2870000540\tfastboot\n", ""

    monkeypatch.setattr(agent, "_run_fastboot", fake_run)
    ws = _FakeWs(_req(op="devices"))
    await agent._run_job(ws, _ops())
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
    ws = _FakeWs(_req(op="reboot", target=""))
    await agent._run_job(ws, _ops())
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
    """A row without a ceiling would let a 'waiting for device' hang look
    like a working command until the hub's 30-minute timeout."""
    ops = _ops()
    assert {"devices", "getvar", "reboot", "flash", "oem", "erase"} <= set(ops)
    for spec in ops.values():
        assert spec.timeout_s > 0, spec.name
    # a query must answer promptly; a partition write may legitimately take minutes
    assert ops["devices"].timeout_s < ops["flash"].timeout_s
    # getvar is a query like devices, not a write — same tier
    assert ops["getvar"].timeout_s == ops["devices"].timeout_s
    # oem writes one small state item, not a partition: short, like reboot
    assert ops["oem"].timeout_s <= ops["reboot"].timeout_s


# ── getvar: transport only, and the same refuse-early rules ─────────────


@pytest.mark.parametrize("name", ["", "all", "version", "partition-size:cfg", "has-slot:boot", "is-userspace"])
def test_getvar_names_accepted(name):
    """Colons are legal here and illegal in partition names, so the two
    checks cannot share a rule — `partition-size:cfg` is a real variable."""
    assert agent._getvar_allowed(name)


@pytest.mark.parametrize(
    "name",
    [
        "-w",  # a leading dash reads as a flag
        "a b",  # a space would split into two argv elements
        "../etc/passwd",
        "x;rm -rf /",
        "x" * 65,
        ".hidden",
    ],
)
def test_getvar_names_rejected(name):
    assert not agent._getvar_allowed(name)


def test_getvar_has_no_allowlist(monkeypatch):
    """Which variables exist is PLATFORM-SPECIFIC. A hub- or agent-side list
    would hide variables the device really has — the same defect as the
    hard-coded web partition picker, which offered four names this bench
    refuses. Shape is checked; membership is the device's business."""
    monkeypatch.setattr(agent, "_FLASH_PARTITIONS", frozenset({"cfg"}))
    # the partition allowlist must not leak into variable names
    assert agent._getvar_allowed("vendor-specific-thing")
    assert not agent._partition_allowed("vendor-specific-thing")


async def test_getvar_refuses_when_no_device_is_in_fastboot(monkeypatch):
    ran: list[list[str]] = []

    async def fake_run(_ws, argv, op=""):
        ran.append(argv)
        return 0, "", ""

    async def no_device(_fastboot):
        return False

    monkeypatch.setattr(agent, "_run_fastboot", fake_run)
    monkeypatch.setattr(agent, "_device_present", no_device)
    ws = _FakeWs(_req(op="getvar", name="all"))
    await agent._run_job(ws, _ops())
    assert ran == [], "must not run a command that would block on 'waiting for any device'"
    assert ws.done()["code"] == "FASTBOOT_NO_DEVICE"


async def test_getvar_argv_and_passthrough(monkeypatch):
    """The agent assembles argv itself, and the output is handed back
    verbatim — nothing on this path parses a value."""
    ran: list[list[str]] = []

    async def fake_run(_ws, argv, op=""):
        ran.append(argv)
        return 0, "", "partition-size:cfg: 0x0\nFinished.\n"

    async def yes(_fastboot):
        return True

    monkeypatch.setattr(agent, "_run_fastboot", fake_run)
    monkeypatch.setattr(agent, "_device_present", yes)

    ws = _FakeWs(_req(op="getvar", name="partition-size:cfg"))
    await agent._run_job(ws, _ops())
    assert ran == [["/opt/fastboot", "getvar", "partition-size:cfg"]]
    done = ws.done()
    assert done["ok"] is True
    # 0x0 stays 0x0. Interpreting it here is exactly the misreading that cost
    # a day on 2026-08-10 — this bench answers 0 on flashes that SUCCEED.
    assert "0x0" in done["stderr"]

    ws2 = _FakeWs(_req(op="getvar", name=""))
    await agent._run_job(ws2, _ops())
    assert ran[-1] == ["/opt/fastboot", "getvar", "all"], "empty name means `getvar all`"


async def test_getvar_rejects_a_malformed_name_without_running_anything(monkeypatch):
    ran: list[list[str]] = []

    async def fake_run(_ws, argv, op=""):
        ran.append(argv)
        return 0, "", ""

    monkeypatch.setattr(agent, "_run_fastboot", fake_run)
    ws = _FakeWs(_req(op="getvar", name="-w"))
    await agent._run_job(ws, _ops())
    assert ran == []
    assert ws.done()["code"] == "FLASH_VAR_REJECTED"


# ── oem: an allowlist, unlike getvar ────────────────────────────────────
#
# getvar has no allowlist because it only asks. `oem` CHANGES the device, and
# what an oem command does is vendor-defined — some are one-way (some bootloader
# docs list `oem at-disable-unlock-vboot`, which permanently forbids unlocking).
# So the agent passes only commands someone has checked are reversible, and the
# check lives here, on the side that builds the argv (ADR-056).


def test_oem_allowlist_is_exactly_the_reversible_lock_pair():
    """Exact equality on purpose: widening this list must be a reviewed change,
    not something that slips in next to another edit."""
    assert agent._OEM_COMMANDS == frozenset({"at-lock-vboot", "at-unlock-vboot"})


@pytest.mark.parametrize("command", ["at-lock-vboot", "at-unlock-vboot"])
def test_oem_commands_accepted(command):
    assert agent._oem_allowed(command)


@pytest.mark.parametrize(
    "command",
    [
        "",
        "at-disable-unlock-vboot",  # one-way where implemented — must never pass
        "unlock",
        "AT-LOCK-VBOOT",  # no case folding: the device would not fold it either
        "at-lock-vboot extra",  # a space would add an argv element
        "at-lock-vboot;reboot",
        "-w",
        " at-lock-vboot",
    ],
)
def test_oem_commands_rejected(command):
    assert not agent._oem_allowed(command)


async def test_oem_rejects_an_unlisted_command_without_running_anything(monkeypatch):
    ran: list[list[str]] = []

    async def fake_run(_ws, argv, op=""):
        ran.append(argv)
        return 0, "", ""

    async def yes(_fastboot):
        return True

    monkeypatch.setattr(agent, "_run_fastboot", fake_run)
    monkeypatch.setattr(agent, "_device_present", yes)
    ws = _FakeWs(_req(op="oem", command="at-disable-unlock-vboot"))
    await agent._run_job(ws, _ops())
    assert ran == []
    assert ws.done()["code"] == "FLASH_OEM_REJECTED"
    assert not ws.events("accepted"), "a rejected job must not be accepted first"


async def test_oem_refuses_when_no_device_is_in_fastboot(monkeypatch):
    ran: list[list[str]] = []

    async def fake_run(_ws, argv, op=""):
        ran.append(argv)
        return 0, "", ""

    async def no_device(_fastboot):
        return False

    monkeypatch.setattr(agent, "_run_fastboot", fake_run)
    monkeypatch.setattr(agent, "_device_present", no_device)
    ws = _FakeWs(_req(op="oem", command="at-lock-vboot"))
    await agent._run_job(ws, _ops())
    assert ran == [], "must not run a command that would block on 'waiting for any device'"
    assert ws.done()["code"] == "FASTBOOT_NO_DEVICE"


async def test_oem_argv_and_passthrough(monkeypatch):
    """The agent assembles argv itself and hands the device's words back —
    whether the lock took is read from the device afterwards, not inferred."""
    ran: list[list[str]] = []

    async def fake_run(_ws, argv, op=""):
        ran.append(argv)
        return 0, "", "OKAY [  0.012s]\nFinished. Total time: 0.013s\n"

    async def yes(_fastboot):
        return True

    monkeypatch.setattr(agent, "_run_fastboot", fake_run)
    monkeypatch.setattr(agent, "_device_present", yes)
    ws = _FakeWs(_req(op="oem", command="at-unlock-vboot"))
    await agent._run_job(ws, _ops())
    assert ran == [["/opt/fastboot", "oem", "at-unlock-vboot"]]
    done = ws.done()
    assert done["ok"] is True
    assert "OKAY" in done["stderr"]


async def test_oem_is_dispatched_by_run_job(monkeypatch):
    """The op has to be a row in the table, or the hub gets BENCH_OP_UNKNOWN
    from an agent that could have run it."""
    ran: list[list[str]] = []

    async def fake_run(_ws, argv, timeout_s=0.0):
        ran.append(argv)
        return 0, "", ""

    async def yes(_fastboot):
        return True

    monkeypatch.setattr(agent, "_run_fastboot", fake_run)
    monkeypatch.setattr(agent, "_device_present", yes)
    ws = _FakeWs(_req(op="oem", command="at-lock-vboot"))
    await agent._run_job(ws, _ops())
    assert ran == [["/opt/fastboot", "oem", "at-lock-vboot"]]


# ── erase: same allowlist as flash, same refuse-early rules ─────────────


async def test_erase_argv_is_built_by_the_agent(monkeypatch):
    ran: list[list[str]] = []

    async def fake_run(_ws, argv, timeout_s=0.0):
        ran.append([*argv, f"timeout={timeout_s}"])
        return 0, "", "Erasing 'misc' OKAY [  0.010s]\n"

    async def yes(_fastboot):
        return True

    monkeypatch.setattr(agent, "_run_fastboot", fake_run)
    monkeypatch.setattr(agent, "_device_present", yes)
    ws = _FakeWs(_req(op="erase", partition="misc"))
    await agent._run_job(ws, _ops())
    # argv from the agent's own row, run with that row's own ceiling
    assert ran == [["/opt/fastboot", "erase", "misc", f"timeout={_ops()['erase'].timeout_s}"]]
    assert ws.done()["ok"] is True


@pytest.mark.parametrize("name", ["../misc", "misc data", "-w", ""])
async def test_erase_rejects_malformed_names_without_running_anything(monkeypatch, name):
    ran: list[list[str]] = []

    async def fake_run(_ws, argv, op=""):
        ran.append(argv)
        return 0, "", ""

    monkeypatch.setattr(agent, "_run_fastboot", fake_run)
    ws = _FakeWs(_req(op="erase", partition=name))
    await agent._run_job(ws, _ops())
    assert ran == []
    assert ws.done()["code"] == "FLASH_PARTITION_REJECTED"


async def test_erase_obeys_the_flash_allowlist(monkeypatch):
    """Erasing takes no more authority than writing, so it is governed by
    the same list — a partition that may not be flashed may not be erased."""
    ran: list[list[str]] = []

    async def fake_run(_ws, argv, op=""):
        ran.append(argv)
        return 0, "", ""

    monkeypatch.setattr(agent, "_run_fastboot", fake_run)
    monkeypatch.setattr(agent, "_FLASH_PARTITIONS", frozenset({"boot_a"}))
    ws = _FakeWs(_req(op="erase", partition="misc"))
    await agent._run_job(ws, _ops())
    assert ran == []
    assert ws.done()["code"] == "FLASH_PARTITION_REJECTED"


async def test_erase_refuses_when_no_device_is_in_fastboot(monkeypatch):
    ran: list[list[str]] = []

    async def fake_run(_ws, argv, op=""):
        ran.append(argv)
        return 0, "", ""

    async def no(_fastboot):
        return False

    monkeypatch.setattr(agent, "_run_fastboot", fake_run)
    monkeypatch.setattr(agent, "_device_present", no)
    ws = _FakeWs(_req(op="erase", partition="misc"))
    await agent._run_job(ws, _ops())
    assert ran == [], "must not run a command that would block on 'waiting for any device'"
    assert ws.done()["code"] == "FASTBOOT_NO_DEVICE"


async def test_erase_is_dispatched_by_run_job(monkeypatch):
    ran: list[list[str]] = []

    async def fake_run(_ws, argv, timeout_s=0.0):
        ran.append(argv)
        return 0, "", ""

    async def yes(_fastboot):
        return True

    monkeypatch.setattr(agent, "_run_fastboot", fake_run)
    monkeypatch.setattr(agent, "_device_present", yes)
    ws = _FakeWs(_req(op="erase", partition="misc"))
    await agent._run_job(ws, _ops())
    assert ran == [["/opt/fastboot", "erase", "misc"]]


# ── image size: the agent's free temp space, not a fixed ceiling ───────


def _flash_request(size: int) -> bytes:
    return _frame(
        b"C",
        json.dumps(
            {"op": "flash", "partition": "super", "size": size, "sha256": "00" * 32}
        ).encode(),
    )


async def test_an_image_over_2_gib_is_not_refused_for_its_size(monkeypatch):
    """The old fixed ceiling refused a 2.2 GB super image outright. Now the
    size gate passes and the request goes on to the next check — here the
    device-present check, stubbed to say no, so nothing is transferred."""
    size = 2_236_037_804

    async def plenty():
        return size * 4

    monkeypatch.setattr(agent, "_tmp_free_bytes", plenty)
    ws = _FakeWs(_flash_request(size))
    ran = await _run_flash(ws, monkeypatch, present=False)
    assert ran == []
    assert ws.done()["code"] == "FASTBOOT_NO_DEVICE", ws.done()


async def test_too_little_temp_space_refuses_before_any_transfer(monkeypatch):
    size = 3 * 1024 * 1024 * 1024
    checked: list[str] = []

    async def little():
        return size  # no room for the reserve on top

    async def fake_present(_fastboot):
        checked.append("present")
        return True

    monkeypatch.setattr(agent, "_tmp_free_bytes", little)
    ws = _FakeWs(_flash_request(size))
    monkeypatch.setattr(agent, "_device_present", fake_present)
    await agent._run_job(ws, _ops())
    done = ws.done()
    assert done["code"] == "FLASH_AGENT_NO_SPACE"
    assert "nothing was transferred" in done["error"]
    assert not ws.events("accepted"), "must refuse before accepting the image"
    assert checked == [], "refused on space before asking the device anything"


def test_hello_reports_the_op_table_instead_of_ability_strings(monkeypatch):
    """ADR-059: a flash plan asks the table ("is there an `erase` row? what
    image ceiling does `flash` report?") — the two ability strings that used
    to say so are gone from caps, and the fastboot rows come and go with the
    tool."""
    monkeypatch.setattr(agent, "_FASTBOOT_PATH", "/usr/bin/fastboot")
    monkeypatch.setattr(agent, "_OPS", _ops("/usr/bin/fastboot"))
    hello = json.loads(agent._hello("a", "n", None))
    assert hello["caps"] == ["adb", "fastboot"]
    names = [op["name"] for op in hello["ops"]]
    assert {"flash", "erase", "set-active", "boot", "usb-list"} <= set(names)
    assert hello["ops"][names.index("flash")]["max_image_bytes"] == 0  # temp space is the limit
    monkeypatch.setattr(agent, "_FASTBOOT_PATH", "")
    monkeypatch.setattr(agent, "_OPS", _ops(""))
    hello = json.loads(agent._hello("a", "n", None))
    assert hello["caps"] == ["adb"]
    assert [op["name"] for op in hello["ops"]] == ["usb-list"]
