"""FlashService.run_op — the one path every bench op takes (ADR-059).

Pinned: what the hub decides from the table row (lock or not, record or not,
fastboot needed or not) is actually decided from it; a bad value never opens
a channel; the op name rides the channel-open request so the agent can say
"busy" before dialling back; the USB listing comes back with modes read.
"""

from __future__ import annotations

import asyncio
import json
import struct

import pytest

import alb.remote.flash as flash_mod
from alb.remote.bench_ops import OpSpec, ParamSpec
from alb.remote.flash import FlashService
from alb.remote.jobframe import KIND_CONTROL
from tests.remote.bench_tables import new_agent_ops

_HDR = struct.Struct(">cI")


def _ctl(msg: dict) -> bytes:
    payload = json.dumps(msg).encode()
    return _HDR.pack(KIND_CONTROL, len(payload)) + payload


def _done(**kw) -> bytes:
    return _ctl({"ev": "done", "ok": True, "rc": 0, "stdout": "", "stderr": "", **kw})


class _Channel:
    def __init__(self, replies: list[bytes]) -> None:
        self._replies = list(replies)
        self.sent: list[bytes] = []

    async def send(self, data: bytes) -> None:
        self.sent.append(data)

    async def recv(self) -> bytes:
        return self._replies.pop(0) if self._replies else b""

    async def aclose(self) -> None:
        pass

    def request(self) -> dict:
        kind, length = _HDR.unpack(self.sent[0][:5])
        assert kind == KIND_CONTROL
        return json.loads(self.sent[0][5 : 5 + length])


class _Agent:
    def __init__(self, replies: list[bytes] | None = None, *, reported: bool = True) -> None:
        self.agent_id = "agent-1"
        self.name = "bench"
        self.caps = ["adb", "fastboot"]
        self.flash_partitions: list[str] = ["boot_a", "misc"]
        if reported:
            self.op_table = new_agent_ops()
            self.ops_reported = True
        self.channel = _Channel(replies or [_done()])
        self.opened: list[dict] = []

    async def open_data_channel(self, *, ctype, role, params, timeout):  # noqa: ASYNC109
        self.opened.append(params)
        return self.channel


@pytest.fixture
def recorded(monkeypatch):
    """Capture what each run asked the recorder for, without writing files."""
    calls: list[dict] = []

    async def fake_open(label, request, *, enabled, watch_uart):
        calls.append({"label": label, "enabled": enabled, "watch_uart": watch_uart})
        return None

    monkeypatch.setattr(flash_mod, "_open_timeline", fake_open)
    return calls


async def test_the_op_name_rides_the_channel_open_request(recorded):
    agent = _Agent()
    r = await FlashService(lambda: agent).run_op("set-active", {"slot": "b"})
    assert r.ok
    assert agent.opened == [{"op": "set-active"}]
    assert agent.channel.request() == {"op": "set-active", "slot": "b"}


async def test_a_bad_value_never_opens_a_channel(recorded):
    agent = _Agent()
    r = await FlashService(lambda: agent).run_op("erase", {"partition": "super"})
    assert r.code == "FLASH_PARTITION_REJECTED"
    assert "allowlist" in r.error
    assert agent.opened == []


async def test_a_host_op_runs_while_a_flash_holds_the_lock(recorded):
    """usb-list is wanted exactly while a long flash is stuck."""
    agent = _Agent([_done(data={"devices": []})])
    svc = FlashService(lambda: agent)
    await svc._lock.acquire()
    try:
        # Bounded: a regression that makes host ops wait for the lock would
        # otherwise hang the suite instead of failing it.
        r = await asyncio.wait_for(svc.run_op("usb-list"), 5)
    finally:
        svc._lock.release()
    assert r.ok, r.error


async def test_an_exclusive_op_is_refused_while_the_lock_is_held(recorded):
    agent = _Agent()
    svc = FlashService(lambda: agent)
    await svc._lock.acquire()
    try:
        r = await svc.run_op("erase", {"partition": "misc"})
    finally:
        svc._lock.release()
    assert r.code == "FASTBOOT_BUSY"
    assert agent.opened == []


async def test_a_host_op_does_not_need_fastboot(recorded):
    agent = _Agent([_done(data={"devices": []})])
    agent.caps = ["adb"]
    agent.op_table = {k: v for k, v in new_agent_ops().items() if v.group == "host"}
    r = await FlashService(lambda: agent).run_op("usb-list")
    assert r.ok, r.error


async def test_recording_follows_the_table(recorded):
    agent = _Agent()
    svc = FlashService(lambda: agent)
    await svc.run_op("erase", {"partition": "misc"})  # record: events
    agent.channel = _Channel([_done(data={"devices": []})])
    await svc.run_op("usb-list")  # record: none
    assert recorded[0]["enabled"] is True and recorded[0]["watch_uart"] is False
    assert recorded[1]["enabled"] is False


async def test_unknown_op_on_a_reporting_agent_lists_what_it_offers(recorded):
    r = await FlashService(lambda: _Agent()).run_op("power-cycle")
    assert r.code == "BENCH_OP_UNKNOWN"
    assert "usb-list" in r.error


async def test_a_new_op_on_an_old_agent_says_update_it(recorded):
    r = await FlashService(lambda: _Agent(reported=False)).run_op("usb-list")
    assert r.code == "BENCH_AGENT_TOO_OLD"
    assert "update alb_agent.py" in r.error


async def test_an_old_agent_without_fastboot_keeps_the_old_answer(recorded):
    agent = _Agent(reported=False)
    agent.caps = ["adb"]
    r = await FlashService(lambda: agent).devices()
    assert r.code == "FASTBOOT_UNAVAILABLE"


async def test_image_ops_carry_size_and_digest(tmp_path, recorded):
    img = tmp_path / "boot.img"
    img.write_bytes(b"x" * 1000)
    agent = _Agent()
    r = await FlashService(lambda: agent).run_op("boot", {}, image=img)
    assert r.ok, r.error
    req = agent.channel.request()
    assert req["op"] == "boot" and req["size"] == 1000 and len(req["sha256"]) == 64


async def test_an_image_op_without_an_image_is_refused(recorded):
    agent = _Agent()
    r = await FlashService(lambda: agent).run_op("boot", {})
    assert r.code == "BENCH_PARAM_REJECTED" and agent.opened == []


async def test_an_image_over_the_agents_ceiling_is_refused_here(tmp_path, recorded):
    img = tmp_path / "big.img"
    img.write_bytes(b"x" * 100)
    agent = _Agent()
    agent.op_table = {
        **new_agent_ops(),
        "flash": OpSpec(
            "flash", params=(ParamSpec("partition", "partition"),), image=True, max_image_bytes=10
        ),
    }
    r = await FlashService(lambda: agent).run_op("flash", {"partition": "boot_a"}, image=img)
    assert r.code == "FLASH_AGENT_NO_SPACE" and agent.opened == []


async def test_usb_listing_comes_back_with_modes(recorded):
    listing = {
        "devices": [
            {
                "name": "Android",
                "vid": "abcd",
                "pid": "0001",
                "serial": "S1",
                "interface": "",
                "problem": 28,
                "service": "",
                "compatible_ids": ["USB\\Class_ff&SubClass_42&Prot_03"],
            }
        ]
    }
    agent = _Agent([_done(data=listing)])
    r = await FlashService(lambda: agent).run_op("usb-list")
    assert r.data["boards"][0]["mode"] == "fastboot"
    assert r.data["boards"][0]["driver"] == "missing"
    assert r.as_dict()["data"] == r.data


async def test_old_wrappers_still_send_the_old_request_shape(recorded):
    """An agent from before the table must still understand what it gets."""
    agent = _Agent(reported=False)
    svc = FlashService(lambda: agent)
    await svc.reboot("")
    assert agent.channel.request() == {"op": "reboot", "target": ""}
    agent.channel = _Channel([_done()])
    await svc.getvar("")
    assert agent.channel.request() == {"op": "getvar", "name": ""}
