"""Hub-side flash job driver (ADR-056).

The behaviours worth pinning are the ones whose absence is expensive rather
than merely annoying: a second caller is refused instead of queued, an
unavailable bench is reported instantly rather than by timeout, and a
channel that dies mid-job is reported as "unknown state" rather than as a
plain failure.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import struct

import pytest

from alb.remote.flash import CHUNK, FlashService, digest_file
from alb.remote.jobframe import KIND_CONTROL, KIND_DATA
from alb.remote.protocol import ChannelRole, ChannelType

_HDR = struct.Struct(">cI")


class _Channel:
    """A fake DataChannel that replays scripted agent frames and records what
    the hub sent."""

    def __init__(self, replies: list[bytes]) -> None:
        self._replies = list(replies)
        self.sent: list[bytes] = []
        self.closed = False

    async def send(self, data: bytes) -> None:
        self.sent.append(data)

    async def recv(self) -> bytes:
        if not self._replies:
            return b""
        return self._replies.pop(0)

    async def aclose(self) -> None:
        self.closed = True

    # ── helpers for assertions ──
    def frames(self) -> list[tuple[bytes, bytes]]:
        out: list[tuple[bytes, bytes]] = []
        buf = b"".join(self.sent)
        i = 0
        while i + 5 <= len(buf):
            kind, length = _HDR.unpack(buf[i : i + 5])
            out.append((kind, buf[i + 5 : i + 5 + length]))
            i += 5 + length
        return out

    def request(self) -> dict:
        kind, payload = self.frames()[0]
        assert kind == KIND_CONTROL
        return json.loads(payload)

    def image_bytes(self) -> bytes:
        return b"".join(p for k, p in self.frames()[1:] if k == KIND_DATA)


def _ctl(msg: dict) -> bytes:
    payload = json.dumps(msg).encode()
    return _HDR.pack(KIND_CONTROL, len(payload)) + payload


class _Agent:
    def __init__(self, channel: _Channel, caps: list[str] | None = None) -> None:
        self.agent_id = "agent-1"
        self.caps = ["adb", "fastboot"] if caps is None else caps
        self.channel = channel
        self.opened: list[dict] = []

    async def open_data_channel(
        self,
        *,
        ctype,
        role,
        params,
        timeout,  # noqa: ASYNC109 — mirrors AgentConnection's real signature
    ):
        self.opened.append({"ctype": ctype, "role": role, "params": params, "timeout": timeout})
        return self.channel


def _service(agent) -> FlashService:
    return FlashService(lambda: agent)


# ── availability is answered from caps, not by trying ───────────────────


async def test_no_agent_is_reported_instantly():
    r = await _service(None).devices()
    assert r.ok is False
    assert r.code == "FASTBOOT_UNAVAILABLE"
    assert "no agent" in r.error


async def test_agent_without_the_capability_is_refused_without_a_channel():
    agent = _Agent(_Channel([]), caps=["adb"])
    r = await _service(agent).devices()
    assert r.code == "FASTBOOT_UNAVAILABLE"
    assert agent.opened == [], "must not open a channel to discover this"


async def test_status_reports_availability():
    svc = _service(_Agent(_Channel([])))
    assert svc.status() == {"available": True, "busy": False, "job": ""}


# ── the job round trip ──────────────────────────────────────────────────


async def test_devices_round_trip():
    ch = _Channel([_ctl({"ev": "accepted"}), _ctl({"ev": "done", "ok": True, "rc": 0})])
    agent = _Agent(ch)
    r = await _service(agent).devices()
    assert r.ok is True
    assert ch.request() == {"op": "devices"}
    assert agent.opened[0]["ctype"] is ChannelType.JOB
    assert agent.opened[0]["role"] is ChannelRole.JOB
    assert ch.closed is True


async def test_reboot_with_no_target_is_back_to_the_system():
    ch = _Channel([_ctl({"ev": "done", "ok": True, "rc": 0})])
    r = await _service(_Agent(ch)).reboot()
    assert r.ok is True
    assert ch.request() == {"op": "reboot", "target": ""}


async def test_flash_sends_digest_up_front_then_the_image(tmp_path):
    img = tmp_path / "cfg.bin"
    payload = b"\x00\x01\x02" * 1000
    img.write_bytes(payload)
    ch = _Channel([_ctl({"ev": "done", "ok": True, "rc": 0, "stdout": "OKAY"})])
    r = await _service(_Agent(ch)).flash("vendor_cfg", img)

    assert r.ok is True
    req = ch.request()
    assert req["op"] == "flash"
    assert req["partition"] == "vendor_cfg"
    assert req["size"] == len(payload)
    # The digest travels BEFORE the bytes — that ordering is what lets the
    # agent refuse a damaged transfer without touching the device.
    assert req["sha256"] == hashlib.sha256(payload).hexdigest()
    assert ch.image_bytes() == payload
    # the trailing zero-length data frame marks the end of the image
    assert ch.frames()[-1] == (KIND_DATA, b"")


async def test_flash_streams_in_chunks_not_one_giant_frame(tmp_path):
    img = tmp_path / "big.bin"
    img.write_bytes(b"x" * (CHUNK * 2 + 7))
    ch = _Channel([_ctl({"ev": "done", "ok": True, "rc": 0})])
    await _service(_Agent(ch)).flash("boot", img)
    data_frames = [p for k, p in ch.frames() if k == KIND_DATA]
    assert len(data_frames) == 4  # 2 full chunks + remainder + end marker
    assert all(len(p) <= CHUNK for p in data_frames)


async def test_progress_events_reach_the_sink(tmp_path):
    img = tmp_path / "i.bin"
    img.write_bytes(b"z" * 16)
    ch = _Channel(
        [
            _ctl({"ev": "progress", "phase": "transfer", "done": 8, "total": 16}),
            _ctl({"ev": "progress", "phase": "flash", "text": "writing 'boot'"}),
            _ctl({"ev": "done", "ok": True, "rc": 0}),
        ]
    )
    seen = []
    r = await _service(_Agent(ch)).flash("boot", img, on_event=seen.append)
    assert [e.phase for e in seen] == ["transfer", "flash"]
    assert seen[1].text == "writing 'boot'"
    assert r.events == seen


async def test_unknown_events_are_ignored_not_fatal():
    """A newer agent may add event kinds; refusing to proceed on one would
    make every future agent addition a breaking change."""
    ch = _Channel(
        [_ctl({"ev": "something-new", "x": 1}), _ctl({"ev": "done", "ok": True, "rc": 0})]
    )
    assert (await _service(_Agent(ch)).devices()).ok is True


# ── failure surfaces ────────────────────────────────────────────────────


async def test_channel_closing_without_a_verdict_says_state_unknown():
    """ "The flash failed" and "we never learned whether it did" are different
    facts — only the second leaves a board nobody can reason about."""
    ch = _Channel([])  # dial-back succeeded, then silence
    r = await _service(_Agent(ch)).devices()
    assert r.ok is False
    assert "unknown" in r.error


async def test_agent_failure_code_is_passed_through():
    ch = _Channel(
        [
            _ctl(
                {
                    "ev": "done",
                    "ok": False,
                    "rc": 1,
                    "code": "FLASH_IMAGE_CORRUPT",
                    "error": "digest mismatch",
                }
            )
        ]
    )
    r = await _service(_Agent(ch)).devices()
    assert (r.ok, r.code, r.rc) == (False, "FLASH_IMAGE_CORRUPT", 1)


async def test_malformed_rc_does_not_lose_the_verdict():
    ch = _Channel([_ctl({"ev": "done", "ok": False, "rc": "nonsense", "code": "FLASH_FAILED"})])
    r = await _service(_Agent(ch)).devices()
    assert r.code == "FLASH_FAILED"  # still reported, rc falls back
    assert r.rc == -1


async def test_missing_image_is_refused_before_any_channel(tmp_path):
    agent = _Agent(_Channel([]))
    r = await _service(agent).flash("boot", tmp_path / "nope.bin")
    assert r.code == "FLASH_IMAGE_CORRUPT"
    assert agent.opened == []


async def test_empty_image_is_refused(tmp_path):
    img = tmp_path / "empty.bin"
    img.write_bytes(b"")
    agent = _Agent(_Channel([]))
    r = await _service(agent).flash("boot", img)
    assert r.code == "FLASH_IMAGE_CORRUPT"
    assert agent.opened == []


# ── one job at a time, refused rather than queued ───────────────────────


async def test_second_job_is_refused_immediately_not_queued():
    """ADR-056 §决定 3. Queuing would leave the second caller blocked behind
    work it cannot see, holding a board it thinks is about to be written."""
    release = asyncio.Event()

    class _SlowChannel(_Channel):
        async def recv(self) -> bytes:
            await release.wait()
            return await super().recv()

    ch = _SlowChannel([_ctl({"ev": "done", "ok": True, "rc": 0})])
    svc = _service(_Agent(ch))

    first = asyncio.create_task(svc.devices())
    await asyncio.sleep(0)  # let it take the lock
    for _ in range(50):
        if svc.busy:
            break
        await asyncio.sleep(0.01)
    assert svc.busy is True
    assert svc.current == "devices"

    second = await svc.devices()
    assert second.code == "FASTBOOT_BUSY"
    assert "devices" in second.error

    release.set()
    assert (await first).ok is True
    assert svc.busy is False
    assert svc.current == ""


async def test_lock_is_released_after_a_failure():
    ch = _Channel([])  # closes with no verdict → failure path
    svc = _service(_Agent(ch))
    assert (await svc.devices()).ok is False
    assert svc.busy is False  # a failed job must not wedge the bench


async def test_digest_file_matches_hashlib(tmp_path):
    p = tmp_path / "f.bin"
    data = b"abc" * 5000
    p.write_bytes(data)
    assert await digest_file(p) == (len(data), hashlib.sha256(data).hexdigest())


@pytest.mark.parametrize("caps", [[], ["adb"], ["serial"]])
async def test_capability_variants_refused(caps):
    assert (await _service(_Agent(_Channel([]), caps=caps)).devices()).code == (
        "FASTBOOT_UNAVAILABLE"
    )
