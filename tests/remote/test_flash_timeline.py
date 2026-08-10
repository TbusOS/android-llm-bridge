"""Flash + UART on one timeline (ADR-056 §决定 4).

The property that matters is correlation: a reader must be able to answer
"what was the board printing while the partition was being written" from a
single file. Everything else here protects that — one monotonic clock, both
sources in one stream, and recording that degrades to nothing rather than
taking the flash down with it.
"""

from __future__ import annotations

import asyncio
import json
from typing import ClassVar

import pytest

from alb.remote.flash import FlashEvent, FlashResult
from alb.remote.flash_timeline import FlashTimeline, new_timeline, serial_endpoint


@pytest.fixture(autouse=True)
def _isolated_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path / "ws"))


def _read(tl: FlashTimeline) -> list[dict]:
    return [
        json.loads(x) for x in (tl.dir / "timeline.jsonl").read_text().splitlines() if x.strip()
    ]


async def _serve(payloads: list[bytes], *, hold: asyncio.Event | None = None) -> tuple[str, int]:
    """A throwaway TCP server that stands in for the serial forwarder."""

    async def handler(reader, writer):
        for chunk in payloads:
            writer.write(chunk)
            await writer.drain()
        if hold is not None:
            await hold.wait()
        writer.close()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return "127.0.0.1", port


# ── the artifact set ────────────────────────────────────────────────────


async def test_creates_the_three_artifacts():
    tl = new_timeline("flash-boot")
    tl.header(label="flash boot", detail={"partition": "boot", "size": 10})
    tl.job_result(FlashResult(ok=True, rc=0))
    await tl.aclose()
    assert (tl.dir / "timeline.jsonl").is_file()
    assert (tl.dir / "job.json").is_file()
    assert json.loads((tl.dir / "job.json").read_text())["ok"] is True


async def test_directory_name_says_what_was_done():
    """A listing should read as a history of actions, not a column of
    timestamps."""
    tl = new_timeline("flash-vendor_cfg")
    await tl.aclose()
    assert tl.dir.name.startswith("flash-vendor_cfg-")


async def test_directory_name_cannot_escape_the_workspace():
    """The security property is that the label becomes ONE path segment —
    a separator surviving here would let a job name choose where its own
    evidence lands."""
    import os

    tl = new_timeline("../../etc/passwd")
    await tl.aclose()
    assert os.sep not in tl.dir.name
    assert not tl.dir.name.startswith(".")
    assert tl.dir.resolve().parent.name == "flash"


async def test_header_records_which_image_was_written():
    """Six months on, "which exact image went onto this board" is the
    question the record has to answer — the digest is what answers it."""
    tl = new_timeline("flash-boot")
    tl.header(label="flash boot", detail={"partition": "boot", "sha256": "deadbeef"})
    await tl.aclose()
    head = _read(tl)[0]
    assert head["partition"] == "boot"
    assert head["sha256"] == "deadbeef"


# ── correlation ─────────────────────────────────────────────────────────


async def test_job_and_uart_share_one_increasing_clock():
    """The whole point: both sources in one file, ordered, one clock."""
    host, port = await _serve([b"boot line one\nboot line two\n"])
    tl = new_timeline("flash-boot")
    tl.header(label="flash boot", detail={})
    import alb.remote.flash_timeline as mod

    original = mod.serial_endpoint
    mod.serial_endpoint = lambda: (host, port)
    try:
        await tl.attach_uart()
        tl.job_event(FlashEvent(phase="flash", text="writing 'boot'"))
        for _ in range(50):
            if len([r for r in _read(tl) if r["src"] == "uart"]) >= 2:
                break
            await asyncio.sleep(0.02)
        tl.job_result(FlashResult(ok=True, rc=0))
    finally:
        mod.serial_endpoint = original
        await tl.aclose()

    records = _read(tl)
    sources = {r["src"] for r in records}
    assert "uart" in sources and "job" in sources
    times = [r["t"] for r in records]
    assert times == sorted(times), "a reader relies on file order being time order"
    uart_lines = [r["line"] for r in records if r["src"] == "uart"]
    assert uart_lines == ["boot line one", "boot line two"]


async def test_raw_log_keeps_every_byte():
    """timeline.jsonl is for reasoning, uart.log is for evidence — line
    splitting is lossy on a bootloader's output."""
    raw = b"\x1b[0mstage1\r\n\x00\xffgarbage\n"
    host, port = await _serve([raw])
    tl = new_timeline("flash-boot")
    import alb.remote.flash_timeline as mod

    mod.serial_endpoint = lambda: (host, port)
    try:
        await tl.attach_uart()
        for _ in range(50):
            if (tl.dir / "uart.log").is_file() and (tl.dir / "uart.log").stat().st_size >= len(raw):
                break
            await asyncio.sleep(0.02)
    finally:
        await tl.aclose()
    assert (tl.dir / "uart.log").read_bytes() == raw


async def test_carriage_returns_are_stripped_from_lines():
    host, port = await _serve([b"with-cr\r\n"])
    tl = new_timeline("j")
    import alb.remote.flash_timeline as mod

    mod.serial_endpoint = lambda: (host, port)
    try:
        await tl.attach_uart()
        for _ in range(50):
            if any(r["src"] == "uart" for r in _read(tl)):
                break
            await asyncio.sleep(0.02)
    finally:
        await tl.aclose()
    assert [r["line"] for r in _read(tl) if r["src"] == "uart"] == ["with-cr"]


async def test_trailing_partial_line_is_not_lost():
    """A bootloader prompt with no newline is often the single most
    informative thing on the console — holding it back until a newline that
    never comes would hide it."""
    host, port = await _serve([b"complete\nno-newline-prompt> "])
    tl = new_timeline("j")
    import alb.remote.flash_timeline as mod

    mod.serial_endpoint = lambda: (host, port)
    try:
        await tl.attach_uart()
        for _ in range(60):
            if any(r["src"] == "uart" for r in _read(tl)):
                break
            await asyncio.sleep(0.02)
    finally:
        await tl.aclose()
    lines = [r for r in _read(tl) if r["src"] == "uart"]
    assert lines[0]["line"] == "complete"
    assert lines[-1]["line"] == "no-newline-prompt> "
    assert lines[-1].get("partial") is True


# ── recording never takes the flash down ────────────────────────────────


async def test_no_forwarder_is_recorded_not_raised(monkeypatch):
    import alb.remote.flash_timeline as mod

    monkeypatch.setattr(mod, "serial_endpoint", lambda: None)
    tl = new_timeline("j")
    await tl.attach_uart()  # must not raise
    await tl.aclose()
    assert tl.uart_attached is False
    notes = [r for r in _read(tl) if r.get("ev") == "uart"]
    assert notes and "nothing to watch" in notes[0]["note"]


async def test_unreachable_forwarder_is_recorded_not_raised(monkeypatch):
    import alb.remote.flash_timeline as mod

    # port 1 on loopback: reliably refused, no listener anywhere
    monkeypatch.setattr(mod, "serial_endpoint", lambda: ("127.0.0.1", 1))
    tl = new_timeline("j")
    await tl.attach_uart()
    await tl.aclose()
    assert tl.uart_attached is False
    assert "cannot attach" in tl.uart_note


def test_serial_endpoint_none_when_forwarder_absent(monkeypatch):
    """Reading status must not have the side effect of binding a port."""
    from alb.remote import forwarder

    monkeypatch.setattr(forwarder, "_SERIAL_FORWARDER", None)
    assert serial_endpoint() is None


# ── service integration ─────────────────────────────────────────────────


async def test_service_records_and_reports_the_artifact_dir(monkeypatch, tmp_path):
    """Recording lives in the service so the answer does not depend on which
    client started the job."""
    from alb.remote.flash import FlashService

    class _Ch:
        async def send(self, data): ...
        async def recv(self):
            return b""

        async def aclose(self): ...

    class _Agent:
        agent_id = "a"
        caps: ClassVar[list[str]] = ["adb", "fastboot"]

        async def open_data_channel(self, **kw):
            return _Ch()

    import alb.remote.flash_timeline as mod

    monkeypatch.setattr(mod, "serial_endpoint", lambda: None)
    result = await FlashService(lambda: _Agent()).flash("boot", _image(tmp_path))
    assert result.artifacts, "the caller must be told where the story landed"
    from pathlib import Path

    assert (Path(result.artifacts) / "timeline.jsonl").is_file()
    assert (Path(result.artifacts) / "job.json").is_file()


async def test_service_survives_a_broken_recorder(monkeypatch):
    """A bench that cannot write artifacts must still be able to flash."""
    import alb.remote.flash as flash_mod
    from alb.remote.flash import FlashService

    def boom(*a, **k):
        raise OSError("workspace is read-only")

    monkeypatch.setattr("alb.remote.flash_timeline.new_timeline", boom)

    class _Ch:
        async def send(self, data): ...
        async def recv(self):
            return b""

        async def aclose(self): ...

    class _Agent:
        agent_id = "a"
        caps: ClassVar[list[str]] = ["fastboot"]

        async def open_data_channel(self, **kw):
            return _Ch()

    result = await FlashService(lambda: _Agent()).devices()
    assert result.artifacts == ""  # nothing recorded
    assert result.code == "FLASH_FAILED"  # the job itself still ran and reported
    assert flash_mod is not None


def _image(tmp_path):
    """A minimal real file — flash() hashes it before opening any channel."""
    p = tmp_path / "img.bin"
    p.write_bytes(b"x" * 32)
    return p


# ── what each op records (2026-08-10 regression) ────────────────────────
#
# `devices` used to record like every other op, and recording means opening
# the board's PHYSICAL serial port. It is also the op people POLL. A 4-second
# poll loop of it opened COM-port after COM-port until the agent stopped
# answering keepalives and dropped its session. The cost of recording has to
# match the value of the record.


async def test_devices_records_nothing(monkeypatch):
    """A ~60 ms query whose whole answer is its return value."""
    import alb.remote.flash_timeline as mod
    from alb.remote.flash import FlashService

    opened = []
    monkeypatch.setattr(mod, "serial_endpoint", lambda: opened.append(1) or None)
    result = await FlashService(lambda: _capable_agent()).devices()
    assert result.artifacts == ""
    assert opened == [], "must not even look for a serial endpoint"


async def test_reboot_records_but_does_not_open_the_uart(monkeypatch):
    """The command returns in ~0.1 s; the board has not said anything yet.
    Keep the record, skip the port."""
    import alb.remote.flash_timeline as mod
    from alb.remote.flash import FlashService

    attached = []

    async def spy(self):
        attached.append(1)

    monkeypatch.setattr(mod.FlashTimeline, "attach_uart", spy)
    result = await FlashService(lambda: _capable_agent()).reboot()
    assert result.artifacts, "the outcome is still worth recording"
    assert attached == [], "but the physical port must not be opened"


async def test_flash_does_open_the_uart(monkeypatch, tmp_path):
    """The one op where §决定 4's correlation is the whole point."""
    import alb.remote.flash_timeline as mod
    from alb.remote.flash import FlashService

    attached = []

    async def spy(self):
        attached.append(1)

    monkeypatch.setattr(mod.FlashTimeline, "attach_uart", spy)
    result = await FlashService(lambda: _capable_agent()).flash("boot", _image(tmp_path))
    assert result.artifacts
    assert attached == [1]


def _capable_agent():
    class _Ch:
        async def send(self, data): ...
        async def recv(self):
            return b""

        async def aclose(self): ...

    class _Agent:
        agent_id = "a"
        caps: ClassVar[list[str]] = ["adb", "fastboot"]

        async def open_data_channel(self, **kw):
            return _Ch()

    return _Agent()
