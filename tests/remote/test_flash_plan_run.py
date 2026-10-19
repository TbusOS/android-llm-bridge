"""Running a flash plan through FlashService (one lock hold, row by row).

The fake bench below answers each job channel by the op in its opening
frame, and checks a flash row's bytes against the digest it was sent — the
same check the real agent makes — so "the image changed after the plan was
confirmed" is exercised end to end rather than asserted.
"""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

import pytest

import alb.remote.flash as flash_mod
from alb.remote.flash import FlashService
from alb.remote.flash_plan import AgentView, build_plan
from alb.remote.jobframe import KIND_CONTROL, KIND_DATA
from tests.remote.bench_tables import new_agent_ops

_HDR = struct.Struct(">cI")
CAPS = ["adb", "fastboot"]

MANIFEST = """\
tool setvar model model-x
tool flash loader_a                 loader.elf
tool flash loader_b                 loader.elf
tool reboot-bootloader
tool flash boot_a                   boot.img
tool flash boot_b                   boot.img
tool flash super                    super.img
tool erase misc
tool reboot
"""


def _ctl(msg: dict) -> bytes:
    payload = json.dumps(msg).encode()
    return _HDR.pack(KIND_CONTROL, len(payload)) + payload


def _frames(buf: bytes) -> list[tuple[bytes, bytes]]:
    out, i = [], 0
    while i + 5 <= len(buf):
        kind, length = _HDR.unpack(buf[i : i + 5])
        out.append((kind, buf[i + 5 : i + 5 + length]))
        i += 5 + length
    return out


class _Bench:
    """A fake agent. `requests` is every opening frame in order — the
    sequence of things the board was asked to do."""

    def __init__(self) -> None:
        self.agent_id = "agent-1"
        self.caps = list(CAPS)
        self.op_table = new_agent_ops()
        self.ops_reported = True
        self.flash_partitions: list[str] = []
        self.requests: list[dict] = []
        self.images: dict[str, bytes] = {}
        self.fail_partition = ""
        self.devices_answers: list[bool] = []  # empty = always listed
        # What fastboot printed for a getvar. It exits 0 whatever the device
        # answered, so a FAIL arrives as text in an otherwise ok result.
        self.getvar_stderr = ""
        self.on_flash = None  # async hook, run while a flash row is in flight

    async def open_data_channel(self, *, ctype, role, params, timeout):  # noqa: ASYNC109
        return _Chan(self)


class _Chan:
    def __init__(self, bench: _Bench) -> None:
        self.bench = bench
        self.sent: list[bytes] = []
        self.replies: list[bytes] | None = None

    async def send(self, data: bytes) -> None:
        self.sent.append(data)

    async def aclose(self) -> None:
        pass

    async def recv(self) -> bytes:
        if self.replies is None:
            self.replies = await self._answer()
        return self.replies.pop(0) if self.replies else b""

    async def _answer(self) -> list[bytes]:
        frames = _frames(b"".join(self.sent))
        req = json.loads(frames[0][1])
        bench = self.bench
        if req["op"] == "devices":
            listed = bench.devices_answers.pop(0) if bench.devices_answers else True
            return [_ctl({"ev": "done", "ok": listed, "rc": 0})]
        bench.requests.append(req)
        if req["op"] == "flash":
            data = b"".join(p for k, p in frames[1:] if k == KIND_DATA)
            if bench.on_flash is not None:
                await bench.on_flash()
            if hashlib.sha256(data).hexdigest() != req["sha256"]:
                return [
                    _ctl(
                        {
                            "ev": "done",
                            "ok": False,
                            "code": "FLASH_IMAGE_CORRUPT",
                            "error": "image digest mismatch — nothing was written",
                        }
                    )
                ]
            if req["partition"] == bench.fail_partition:
                return [
                    _ctl(
                        {
                            "ev": "done",
                            "ok": False,
                            "rc": 1,
                            "code": "FLASH_FAILED",
                            "error": "FAILED (remote: 'write failed')",
                            "stderr": "write failed",
                        }
                    )
                ]
            bench.images[req["partition"]] = data
        if req["op"] == "getvar":
            return [_ctl({"ev": "done", "ok": True, "rc": 0, "stderr": bench.getvar_stderr})]
        progress = {"ev": "progress", "phase": "flash", "done": 0, "total": 0, "text": "OKAY"}
        return [_ctl(progress), _ctl({"ev": "done", "ok": True, "rc": 0})]


@pytest.fixture(autouse=True)
def _fast_reboot_wait(monkeypatch):
    monkeypatch.setattr(flash_mod, "REBOOT_POLL_S", 0.001)
    monkeypatch.setattr(flash_mod, "REBOOT_SETTLE_S", 0.05)


def _package(root: Path, text: str = MANIFEST) -> Path:
    pkg = root / "images" / "board-x"
    pkg.mkdir(parents=True)
    (pkg / "loader.elf").write_bytes(b"L" * 100)
    (pkg / "boot.img").write_bytes(b"B" * 300)
    (pkg / "super.img").write_bytes(b"S" * 70_000)  # more than one 64 KiB chunk
    (pkg / "flash.txt").write_text(text, encoding="utf-8")
    return pkg / "flash.txt"


def _resolved(path: Path) -> Path:
    return path.resolve()


async def _plan(
    tmp_path: Path,
    bench: _Bench,
    *,
    skip_actions=("setvar", "reboot-bootloader"),
    text: str = MANIFEST,
    skip=("loader_a", "loader_b"),
):
    manifest = _package(tmp_path, text)
    return await build_plan(
        manifest,
        name="images/board-x/flash.txt",
        root=_resolved(tmp_path),
        skip=list(skip),
        skip_actions=list(skip_actions),
        agent=AgentView(caps=bench.caps, partitions=bench.flash_partitions, ops=bench.op_table),
    )


def _asked(bench: _Bench) -> list[tuple[str, str]]:
    return [
        (r["op"], r.get("partition") or r.get("target") or r.get("name") or r.get("command") or "")
        for r in bench.requests
    ]


async def test_runs_every_row_in_order_and_never_touches_skipped_ones(tmp_path):
    bench = _Bench()
    plan = await _plan(tmp_path, bench)
    events = []
    result = await FlashService(lambda: bench).run_plan(plan, on_event=events.append)
    assert result.ok, result.error
    assert _asked(bench) == [
        ("flash", "boot_a"),
        ("flash", "boot_b"),
        ("flash", "super"),
        ("erase", "misc"),
        ("reboot", ""),
    ]
    assert bench.images["super"] == b"S" * 70_000
    assert "loader_a" not in bench.images and "loader_b" not in bench.images
    summary = result.as_dict()
    assert (summary["total"], summary["ran"], summary["skipped"], summary["not_run"]) == (
        9,
        5,
        4,
        0,
    )
    steps = [e.text for e in events if e.phase == "step"]
    assert steps[0] == "line 5: flash boot_a <- boot.img"
    assert [e.done for e in events if e.phase == "step"] == [1, 2, 3, 4, 5]
    assert len([e for e in events if e.phase == "skip"]) == 4


async def test_reboot_bootloader_waits_for_the_board_to_come_back(tmp_path):
    bench = _Bench()
    bench.devices_answers = [False, False, True]  # gone, gone, back
    plan = await _plan(tmp_path, bench, skip_actions=("setvar",))
    events = []
    result = await FlashService(lambda: bench).run_plan(plan, on_event=events.append)
    assert result.ok, result.error
    assert _asked(bench)[0] == ("reboot", "bootloader")
    assert _asked(bench)[1] == ("flash", "boot_a"), "the next row runs only once it is back"
    assert bench.devices_answers == [], "polled until listed"
    assert any(e.phase == "wait" and "back in fastboot" in e.text for e in events)


async def test_a_board_that_never_comes_back_stops_the_plan(tmp_path):
    bench = _Bench()
    bench.devices_answers = [False] * 10_000
    plan = await _plan(tmp_path, bench, skip_actions=("setvar",))
    result = await FlashService(lambda: bench).run_plan(plan, reboot_wait_s=0.05)
    assert not result.ok
    assert result.code == "FASTBOOT_NO_DEVICE"
    assert result.failed_line == 4
    assert _asked(bench) == [("reboot", "bootloader")], (
        "nothing may be written to a board not in fastboot"
    )
    statuses = {r.line: r.status for r in result.rows}
    assert statuses[4] == "failed"
    assert [statuses[n] for n in (5, 6, 7, 8, 9)] == ["not-run"] * 5
    assert [statuses[n] for n in (1, 2, 3)] == ["skipped"] * 3


async def test_a_board_still_listed_after_the_settle_time_counts_as_back(tmp_path):
    """A reboot faster than one poll is never seen gone; waiting forever for
    a disappearance that already happened would fail a healthy board."""
    bench = _Bench()  # always listed
    plan = await _plan(tmp_path, bench, skip_actions=("setvar",))
    events = []
    result = await FlashService(lambda: bench).run_plan(plan, on_event=events.append)
    assert result.ok, result.error
    assert any("never seen leaving" in e.text for e in events if e.phase == "wait")


async def test_the_first_failing_row_stops_the_run(tmp_path):
    bench = _Bench()
    bench.fail_partition = "boot_b"
    plan = await _plan(tmp_path, bench)
    result = await FlashService(lambda: bench).run_plan(plan)
    assert not result.ok
    assert (result.code, result.failed_line) == ("FLASH_FAILED", 6)
    assert "write failed" in result.stderr, "the failing row's own output is the evidence"
    assert _asked(bench) == [("flash", "boot_a"), ("flash", "boot_b")]
    statuses = {r.line: r.status for r in result.rows}
    assert (statuses[5], statuses[6], statuses[7], statuses[8], statuses[9]) == (
        "ok",
        "failed",
        "not-run",
        "not-run",
        "not-run",
    )
    assert result.as_dict()["ran"] == 2


async def test_an_image_changed_after_the_plan_is_caught_before_it_is_written(tmp_path):
    bench = _Bench()
    plan = await _plan(tmp_path, bench)
    (tmp_path / "images" / "board-x" / "boot.img").write_bytes(b"X" * 300)
    result = await FlashService(lambda: bench).run_plan(plan)
    assert not result.ok
    assert (result.code, result.failed_line) == ("FLASH_IMAGE_CORRUPT", 5)
    assert bench.images == {}, "nothing written"


async def test_the_lock_is_held_for_the_whole_plan(tmp_path):
    """Between rows the lock must not be free — a second caller writing a
    partition mid-package leaves a board no manifest describes."""
    bench = _Bench()
    svc = FlashService(lambda: bench)
    seen: list[tuple[str, str]] = []

    async def meanwhile():
        r = await svc.devices()
        seen.append((r.code, svc.current))

    bench.on_flash = meanwhile
    plan = await _plan(tmp_path, bench)
    result = await svc.run_plan(plan)
    assert result.ok
    assert len(seen) == 3
    assert all(code == "FASTBOOT_BUSY" for code, _ in seen)
    assert "line 5 (1/5) flash boot_a" in seen[0][1]
    assert svc.current == "" and not svc.busy


async def test_a_plan_with_problems_opens_no_channel(tmp_path):
    bench = _Bench()
    plan = await _plan(tmp_path, bench, skip_actions=())  # setvar not skipped
    assert not plan.ok
    result = await FlashService(lambda: bench).run_plan(plan)
    assert result.code == "FLASH_PLAN_REJECTED"
    assert bench.requests == []


async def test_a_busy_bench_refuses_the_plan_at_once(tmp_path):
    bench = _Bench()
    svc = FlashService(lambda: bench)
    plan = await _plan(tmp_path, bench)
    async with svc._lock:
        result = await svc.run_plan(plan)
    assert result.code == "FASTBOOT_BUSY"
    assert bench.requests == []
    assert all(r.status == "pending" for r in result.rows), "nothing ran, nothing was marked"


async def test_the_whole_plan_lands_on_one_timeline(tmp_path):
    bench = _Bench()
    plan = await _plan(tmp_path, bench)
    result = await FlashService(lambda: bench).run_plan(plan)
    assert result.artifacts
    lines = [
        json.loads(x) for x in (Path(result.artifacts) / "timeline.jsonl").read_text().splitlines()
    ]
    head = lines[0]
    assert head["ev"] == "start" and head["op"] == "plan"
    assert head["fingerprint"] == plan.fingerprint, "which exact plan ran"
    assert [x["text"] for x in lines if x.get("phase") == "step"][-1] == "line 9: reboot"
    job = json.loads((Path(result.artifacts) / "job.json").read_text())
    assert job["ok"] is True and len(job["rows"]) == 9


# A board check first, an oem lock after the flashes (the shape of a vendor
# package's manifest).
CHECKED = """\
# board-x flashing commands
tool getvar check-firmware-board-x
tool flash boot_a boot.img
tool oem at-lock-vboot
tool reboot
"""
FAIL_ANSWER = (
    "getvar:check-firmware-board-x                      "
    "FAILED (remote: 'firmware check failed')\r\n"
    "Finished. Total time: 0.004s\r\n"
)


@pytest.mark.parametrize(
    "answer, quoted",
    [
        (FAIL_ANSWER, "firmware check failed"),  # as AOSP fastboot prints it, CRLF on Windows
        ("FAILED (remote: 'firmware check failed')\n", "firmware check failed"),  # no prefix
        ("  getvar:check-firmware-board-x FAILED\n", "FAILED"),  # indented, no reason
        (  # a local error on the way prints the same shape
            "getvar:check-firmware-board-x   FAILED (Status read failed (Protocol error))\n",
            "Status read failed",
        ),
    ],
)
async def test_a_getvar_check_that_fails_stops_the_plan_before_any_flash(tmp_path, answer, quoted):
    """fastboot exits 0 when a getvar fails. The plan must read the FAILED
    line itself: the check is there to stop a package meant for another
    board before its first flash, and a missed FAIL lets it through."""
    bench = _Bench()
    bench.getvar_stderr = answer
    plan = await _plan(tmp_path, bench, text=CHECKED, skip=(), skip_actions=())
    assert plan.ok, plan.problems
    result = await FlashService(lambda: bench).run_plan(plan)
    assert not result.ok
    assert (result.code, result.failed_line) == ("FLASH_PLAN_CHECK_FAILED", 2)
    assert quoted in result.error, "the error quotes the line that failed"
    assert quoted in result.stderr, "fastboot's own output is the evidence"
    assert _asked(bench) == [("getvar", "check-firmware-board-x")]
    assert [r.status for r in result.rows] == ["failed", "not-run", "not-run", "not-run"]


async def test_a_passing_getvar_check_lets_the_plan_run_and_oem_goes_out_in_order(tmp_path):
    bench = _Bench()
    bench.getvar_stderr = "check-firmware-board-x: firmware check success\r\n"
    plan = await _plan(tmp_path, bench, text=CHECKED, skip=(), skip_actions=())
    result = await FlashService(lambda: bench).run_plan(plan)
    assert result.ok, result.error
    assert _asked(bench) == [
        ("getvar", "check-firmware-board-x"),
        ("flash", "boot_a"),
        ("oem", "at-lock-vboot"),
        ("reboot", ""),
    ]


async def test_a_skipped_check_and_lock_are_never_sent(tmp_path):
    bench = _Bench()
    bench.getvar_stderr = FAIL_ANSWER  # would stop the plan if it were asked
    plan = await _plan(tmp_path, bench, text=CHECKED, skip=(), skip_actions=("getvar", "oem"))
    result = await FlashService(lambda: bench).run_plan(plan)
    assert result.ok, result.error
    assert _asked(bench) == [("flash", "boot_a"), ("reboot", "")]


async def test_a_symlinked_image_is_streamed_from_its_target(tmp_path):
    """What reaches the board is the bytes behind the link — and the agent's
    digest check passes on them, since the plan hashed the same bytes."""
    bench = _Bench()
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "boot.img").write_bytes(b"T" * 1000)
    manifest = _package(tmp_path / "ws", "tool flash boot_a linked.img\n")
    (manifest.parent / "linked.img").symlink_to(archive / "boot.img")
    plan = await build_plan(
        manifest,
        name="images/board-x/flash.txt",
        root=_resolved(tmp_path / "ws"),
        skip=[],
        skip_actions=[],
        agent=AgentView(caps=bench.caps, partitions=[], ops=bench.op_table),
    )
    assert plan.ok, plan.problems
    result = await FlashService(lambda: bench).run_plan(plan)
    assert result.ok, result.error
    assert bench.images["boot_a"] == b"T" * 1000
