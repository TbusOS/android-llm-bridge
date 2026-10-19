"""capture_timeline / iter_timeline — fake byte sources only, no device.

What these lock: the one thing the timeline is for is ORDER ACROSS SOURCES,
and that is only true if every line is stamped the instant it arrives and
written before the next one can arrive. Each test here is a way that
property could silently break.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from alb.capabilities import timeline as tl
from alb.capabilities.timeline import capture_timeline, format_line, iter_timeline
from alb.infra.permissions import PermissionResult
from alb.transport.base import ShellResult


# ─── helpers ───────────────────────────────────────────────────────
def _records(directory: Path) -> list[dict]:
    """Every record of timeline.jsonl that carries a line (meta skipped)."""
    out = []
    for raw in (directory / "timeline.jsonl").read_text(encoding="utf-8").splitlines():
        rec = json.loads(raw)
        if "line" in rec:
            out.append(rec)
    return out


def _timed(*steps: tuple[float, bytes]):
    """A source that yields each chunk after sleeping the given seconds,
    then keeps the stream open until the capture cancels it (like a real
    `logcat` / `dmesg -w` / UART, which never end on their own)."""

    def factory() -> AsyncIterator[bytes]:
        async def gen() -> AsyncIterator[bytes]:
            for delay, chunk in steps:
                await asyncio.sleep(delay)
                yield chunk
            await asyncio.sleep(3600)

        return gen()

    return factory


def _ending(*chunks: bytes):
    """A source that yields its chunks and then ends (EOF)."""

    def factory() -> AsyncIterator[bytes]:
        async def gen() -> AsyncIterator[bytes]:
            for c in chunks:
                yield c

        return gen()

    return factory


# ─── ordering across sources ───────────────────────────────────────
async def test_interleaving_follows_arrival_time_across_sources(tmp_path: Path) -> None:
    """Three sources, each delivering at a different moment: the file order
    (and `t`) must follow arrival, not the order the sources were started
    in and not per-source grouping."""
    streams = {
        "logcat": _timed((0.00, b"L1\n"), (0.09, b"L2\n")),
        "uart": _timed((0.03, b"U1\n")),
        "dmesg": _timed((0.06, b"D1\n")),
    }
    r = await capture_timeline(0.4, streams=streams, output=tmp_path / "cap")
    assert r.ok, r.error
    recs = _records(tmp_path / "cap")
    assert [x["line"] for x in recs] == ["L1", "U1", "D1", "L2"]
    assert [x["src"] for x in recs] == ["logcat", "uart", "dmesg", "logcat"]
    ts = [x["t"] for x in recs]
    assert ts == sorted(ts) and ts[0] < 0.03 and ts[-1] >= 0.09
    assert r.data is not None
    assert r.data.lines == {"logcat": 2, "dmesg": 1, "uart": 1}


async def test_wall_is_iso_and_source_timestamp_text_is_untouched(tmp_path: Path) -> None:
    line = b"10-15 08:00:01.234  1234  1234 E MyTag  : boom\n"
    r = await capture_timeline(
        0.2, sources={"logcat"}, streams={"logcat": _ending(line)}, output=tmp_path
    )
    assert r.ok
    rec = _records(tmp_path)[0]
    assert rec["line"] == line.decode().rstrip("\n")  # device's own timestamp kept verbatim
    assert rec["wall"].endswith("+00:00") and "T" in rec["wall"]
    assert rec["t"] >= 0.0


# ─── UART byte handling ────────────────────────────────────────────
async def test_uart_partial_lines_are_joined_across_chunks_and_cr_stripped(tmp_path: Path) -> None:
    chunks = [
        b"[    0.100000] mmc0: err",
        b"or -110 whilst initialising\r\n[    0.300000] ok\r\n",
    ]
    r = await capture_timeline(
        0.3, sources={"uart"}, streams={"uart": _ending(*chunks)}, output=tmp_path
    )
    assert r.ok
    lines = [x["line"] for x in _records(tmp_path) if not x.get("note")]
    assert lines == ["[    0.100000] mmc0: error -110 whilst initialising", "[    0.300000] ok"]
    # raw bytes untouched, \r included
    assert (tmp_path / "uart.log").read_bytes() == b"".join(chunks)


async def test_idle_flush_writes_trailing_fragment_as_partial(tmp_path: Path) -> None:
    """A bootloader prompt has no newline. After 200 ms of silence it must
    appear anyway, marked partial, and the bytes that arrive later must NOT
    be glued onto it."""
    streams = {"uart": _timed((0.0, b"=> "), (0.5, b"printenv\n"))}
    r = await capture_timeline(
        0.8, sources={"uart"}, streams=streams, idle_flush_s=0.2, output=tmp_path
    )
    assert r.ok
    recs = _records(tmp_path)
    assert [x["line"] for x in recs] == ["=> ", "printenv"]
    assert recs[0].get("partial") is True
    assert recs[1].get("partial") is None
    # the prompt is stamped when it ARRIVED, not when the idle timer fired
    assert recs[0]["t"] < 0.1


async def test_fragment_completed_before_idle_flush_is_one_line(tmp_path: Path) -> None:
    streams = {"uart": _timed((0.0, b"half"), (0.05, b" line\n"))}
    r = await capture_timeline(
        0.4, sources={"uart"}, streams=streams, idle_flush_s=0.2, output=tmp_path
    )
    assert r.ok
    assert [x["line"] for x in _records(tmp_path)] == ["half line"]


async def test_fragment_pending_at_capture_end_is_flushed(tmp_path: Path) -> None:
    streams = {"uart": _timed((0.0, b"no newline at end"))}
    r = await capture_timeline(
        0.1, sources={"uart"}, streams=streams, idle_flush_s=5.0, output=tmp_path
    )
    assert r.ok
    recs = _records(tmp_path)
    assert recs[-1]["line"] == "no newline at end" and recs[-1].get("partial") is True


async def test_overlong_run_without_newline_is_cut_not_buffered(tmp_path: Path) -> None:
    blob = b"x" * (tl._MAX_LINE * 2 + 10)
    r = await capture_timeline(
        0.3, sources={"uart"}, streams={"uart": _ending(blob)}, output=tmp_path
    )
    assert r.ok
    recs = _records(tmp_path)
    assert sum(1 for x in recs if x.get("truncated")) == 2
    assert (tmp_path / "uart.log").read_bytes() == blob


async def test_undecodable_bytes_are_replaced_not_fatal(tmp_path: Path) -> None:
    r = await capture_timeline(
        0.2, sources={"uart"}, streams={"uart": _ending(b"\xff\xfeok\n")}, output=tmp_path
    )
    assert r.ok
    assert _records(tmp_path)[0]["line"].endswith("ok")


# ─── one source failing must not stop the others ───────────────────
async def test_source_raising_is_noted_and_others_continue(tmp_path: Path) -> None:
    def broken() -> AsyncIterator[bytes]:
        async def gen() -> AsyncIterator[bytes]:
            raise PermissionError("klogctl: Operation not permitted")
            yield b""  # pragma: no cover

        return gen()

    streams = {
        "dmesg": broken,
        "logcat": _timed((0.05, b"L1\n"), (0.05, b"L2\n")),
        "uart": _timed((0.08, b"U1\n")),
    }
    r = await capture_timeline(0.4, streams=streams, output=tmp_path)
    assert r.ok, "one dead source must not fail the capture"
    recs = _records(tmp_path)
    notes = [x for x in recs if x.get("note")]
    assert len(notes) == 1 and notes[0]["src"] == "dmesg"
    assert notes[0]["line"].startswith("[alb] dmesg unavailable:")
    assert "Operation not permitted" in notes[0]["line"]
    assert [x["line"] for x in recs if not x.get("note")] == ["L1", "U1", "L2"]
    assert r.data is not None
    assert r.data.lines["dmesg"] == 0 and r.data.lines["logcat"] == 2
    assert r.data.notes == [notes[0]["line"]]


async def test_dmesg_ending_with_no_output_is_noted_with_root_hint(tmp_path: Path) -> None:
    """`adb shell dmesg -w` without root exits at once; its stderr is not
    ours to read, so the note names the usual cause."""
    streams = {"dmesg": _ending(), "logcat": _timed((0.02, b"L1\n"))}
    r = await capture_timeline(0.3, sources={"dmesg", "logcat"}, streams=streams, output=tmp_path)
    assert r.ok
    note = next(x for x in _records(tmp_path) if x.get("note"))
    assert note["src"] == "dmesg"
    assert "no output" in note["line"] and "root" in note["line"]
    assert not (tmp_path / "dmesg.txt").read_bytes()


async def test_source_ending_early_with_lines_is_noted(tmp_path: Path) -> None:
    streams = {"logcat": _ending(b"a\n", b"b\n"), "uart": _timed((0.02, b"U\n"))}
    r = await capture_timeline(0.3, sources={"logcat", "uart"}, streams=streams, output=tmp_path)
    assert r.ok
    note = next(x for x in _records(tmp_path) if x.get("note"))
    assert note["src"] == "logcat" and "ended early after 2 lines" in note["line"]


async def test_no_readable_source_is_a_failure_with_the_notes(tmp_path: Path) -> None:
    """No adb transport, no serial transport: every requested source is
    noted, nothing waits out the duration — and the result is NOT ok. An ok
    with zero lines would read as "the board printed nothing"."""
    r = await capture_timeline(5, transport=None, serial=None, output=tmp_path)
    assert not r.ok and r.error is not None
    assert r.error.code == "TIMELINE_NO_SOURCE"
    assert r.error.details["duration_captured_ms"] < 2000
    assert "serial transport not configured" in r.error.message
    notes = {x["src"]: x["line"] for x in _records(tmp_path) if x.get("note")}
    assert notes["uart"] == "[alb] uart unavailable: serial transport not configured"
    assert notes["logcat"] == "[alb] logcat unavailable: no adb/ssh transport"
    assert notes["dmesg"] == "[alb] dmesg unavailable: no adb/ssh transport"
    assert not (tmp_path / "uart.log").exists()  # no source, no raw file
    assert r.artifacts == [tmp_path / "timeline.jsonl"]  # the notes are the evidence


async def test_one_missing_source_does_not_fail_the_rest(tmp_path: Path) -> None:
    """uart missing, logcat readable: ok, the missing one is a note."""
    streams = {"logcat": _timed((0.0, b"I/x: up\n"))}
    r = await capture_timeline(
        0.3, sources={"logcat", "uart"}, serial=None, streams=streams, output=tmp_path
    )
    assert r.ok and r.data is not None
    assert r.data.lines == {"logcat": 1, "uart": 0}
    assert "[alb] uart unavailable: serial transport not configured" in r.data.notes


async def test_a_quiet_but_readable_source_is_still_ok(tmp_path: Path) -> None:
    """Zero lines from a source that WAS read is a quiet board, not a
    failure — only "nothing could be read" is."""
    r = await capture_timeline(0.2, sources={"uart"}, streams={"uart": _timed()}, output=tmp_path)
    assert r.ok and r.data is not None
    assert r.data.lines == {"uart": 0}


async def test_serial_open_failure_marker_is_one_note_and_not_in_raw(tmp_path: Path) -> None:
    """SerialTransport.stream_read yields a synthetic marker when the port
    cannot be opened, and the reconnect loop repeats it every 0.5 s. One
    note, not 120 of them; and it is alb's text, so it must not be in
    uart.log as if the board had printed it."""
    marker = b"[alb serial open failed: connection refused]\n"
    streams = {"uart": _timed((0.0, marker), (0.05, marker), (0.05, marker), (0.05, b"U-Boot\n"))}
    r = await capture_timeline(0.4, sources={"uart"}, streams=streams, output=tmp_path)
    assert r.ok
    recs = _records(tmp_path)
    assert [x["line"] for x in recs] == [
        "[alb] uart unavailable: [alb serial open failed: connection refused]",
        "[alb] uart link up",
        "U-Boot",
    ]
    assert (tmp_path / "uart.log").read_bytes() == b"U-Boot\n"


# ─── incremental writing ───────────────────────────────────────────
async def test_jsonl_is_readable_mid_capture(tmp_path: Path) -> None:
    """The generator reads the file back between its own yields: the line
    it just produced must already be on disk when it resumes."""
    seen: list[list[str]] = []
    jsonl = tmp_path / "timeline.jsonl"

    def factory() -> AsyncIterator[bytes]:
        async def gen() -> AsyncIterator[bytes]:
            yield b"first\n"
            seen.append([x["line"] for x in _records(tmp_path)])
            yield b"second\n"
            seen.append([x["line"] for x in _records(tmp_path)])
            await asyncio.sleep(3600)

        return gen()

    r = await capture_timeline(
        0.2, sources={"logcat"}, streams={"logcat": factory}, output=tmp_path
    )
    assert r.ok
    assert seen == [["first"], ["first", "second"]]
    assert jsonl.read_text(encoding="utf-8").count("\n") >= 4  # start, 2 lines, end


async def test_jsonl_starts_and_ends_with_meta_records(tmp_path: Path) -> None:
    r = await capture_timeline(
        0.1, sources={"logcat"}, streams={"logcat": _ending(b"x\n")}, output=tmp_path
    )
    assert r.ok
    raw = [json.loads(line) for line in (tmp_path / "timeline.jsonl").read_text().splitlines()]
    assert raw[0]["src"] == "meta" and raw[0]["ev"] == "start" and raw[0]["sources"] == ["logcat"]
    assert raw[-1]["src"] == "meta" and raw[-1]["ev"] == "end" and raw[-1]["lines"] == {"logcat": 1}
    assert json.loads((tmp_path / "summary.json").read_text())["lines"] == {"logcat": 1}


# ─── transports (mocked) ───────────────────────────────────────────
# What the device answers to the two probes taken before the streams start:
# its clock, in the shape logcat stamps its threadtime lines with.
CLOCK = "10-19 16:50:00.123\n"
STAMP_CMD = "date '+%m-%d %H:%M:%S.%3N'"


def _adb_mock(
    lines_by_source: dict[str, list[bytes | float]],
    *,
    clock: str | None = CLOCK,
    dmesg_count: str | None = "0\n",
) -> AsyncMock:
    """`clock` / `dmesg_count`: stdout for the date probe / `dmesg | wc -l`;
    None = the command fails. A float among the lines is a pause, in seconds."""
    t = AsyncMock()
    t.name = "adb"
    t.check_permissions = AsyncMock(return_value=PermissionResult(behavior="allow"))
    calls: list[tuple[str, dict]] = []
    answers = {STAMP_CMD: clock, "dmesg | wc -l": dmesg_count}

    async def shell(cmd: str, **kw) -> ShellResult:
        out = answers.get(cmd)
        if out is None:
            return ShellResult(ok=False, exit_code=1, stderr="not here")
        return ShellResult(ok=True, exit_code=0, stdout=out)

    t.shell = shell

    def stream_read(source: str, **kw) -> AsyncIterator[bytes]:
        calls.append((source, kw))

        async def gen() -> AsyncIterator[bytes]:
            for line in lines_by_source.get(source, []):
                if isinstance(line, float):
                    await asyncio.sleep(line)
                    continue
                await asyncio.sleep(0.01)
                yield line
            await asyncio.sleep(3600)

        return gen()

    t.stream_read = stream_read
    t.calls = calls
    return t


async def test_logcat_and_dmesg_come_from_transport_stream_read(tmp_path: Path) -> None:
    t = _adb_mock({"logcat": [b"L\n"], "dmesg": [b"[ 0.1] D\n"]})
    r = await capture_timeline(
        0.3, sources={"logcat", "dmesg"}, transport=t, logcat_filter="*:E", output=tmp_path
    )
    assert r.ok
    assert sorted(x["line"] for x in _records(tmp_path)) == ["L", "[ 0.1] D"]
    assert ("logcat", {"filter": "*:E", "tail": 1}) in t.calls
    assert ("dmesg", {}) in t.calls
    assert (tmp_path / "logcat.txt").read_bytes() == b"L\n"
    assert (tmp_path / "dmesg.txt").read_bytes() == b"[ 0.1] D\n"


# What `logcat -v threadtime -T 1` sends at the start: its divider, the one
# line it repeats from the buffer, then what is logged from then on.
_DIVIDER = b"--------- beginning of main\n"
_OLD = b"10-19 16:49:59.900  1203  1203 I Old: logged before the capture\n"
_LIVE = b"10-19 16:50:00.500  1203  1203 I Live: logged during the capture\n"


async def test_logcat_starts_from_its_last_line_and_leaves_it_off_the_timeline(
    tmp_path: Path,
) -> None:
    """Without -T logcat dumps its whole buffer, all of it stamped at the start.
    `-T 1` repeats one old line; it is older than the device's clock at the
    start, so it goes to logcat.txt only."""
    t = _adb_mock({"logcat": [_DIVIDER, _OLD, _LIVE]})
    r = await capture_timeline(0.3, sources={"logcat"}, transport=t, output=tmp_path)
    assert r.ok
    assert t.calls == [("logcat", {"filter": None, "tail": 1})]
    lines = [x["line"] for x in _records(tmp_path)]
    assert lines[0] == _DIVIDER.decode().rstrip("\n"), "logcat's own divider is not a log line"
    assert lines[1].startswith("[alb] logcat: 1 line(s) from before the capture started")
    assert "still in logcat.txt" in lines[1]
    assert lines[2:] == [_LIVE.decode().rstrip("\n")]
    assert r.data.lines == {"logcat": 2}
    assert (tmp_path / "logcat.txt").read_bytes() == _DIVIDER + _OLD + _LIVE


@pytest.mark.parametrize(
    "src, lines, count",
    [
        ("logcat", [_OLD, _LIVE], None),
        ("dmesg", [b"[ 1.0] old\n" + b"[ 900.0] live\n"], "1\n"),  # one chunk
    ],
)
async def test_the_left_out_note_reads_before_the_first_live_line(
    tmp_path: Path, src: str, lines: list[bytes | float], count: str | None
) -> None:
    """The note marks where live lines begin. Stamped when it is written, it
    is a few microseconds later than the line that arrived in the same
    chunk, and `alb timeline show` (which sorts by time) put it after that
    line — seen on the 10-19 bench."""
    t = _adb_mock({src: lines}, dmesg_count=count or "0\n")
    await capture_timeline(0.3, sources={src}, transport=t, output=tmp_path)
    shown = [r for r in iter_timeline(tmp_path / "timeline.jsonl") if r["src"] == src]
    assert shown[0].get("note") and "1 line(s) from before" in shown[0]["line"]
    assert shown[0]["t"] == shown[1]["t"], "stamped with the chunk it came in"


async def test_a_logcat_line_from_the_start_moment_on_is_live(tmp_path: Path) -> None:
    """Equal to the clock is not older: the backlog ends there, and a later
    line that looks old again is not held back any more."""
    at_start = b"10-19 16:50:00.123  1 1 I At: the start\n"
    t = _adb_mock({"logcat": [_OLD, at_start, _OLD]})
    await capture_timeline(0.3, sources={"logcat"}, transport=t, output=tmp_path)
    lines = [x["line"] for x in _records(tmp_path) if not x.get("note")]
    assert lines == [at_start.decode().rstrip("\n"), _OLD.decode().rstrip("\n")]


@pytest.mark.parametrize(
    "clock",
    [
        "10-19 16:50:00\n",  # a date without %N: whole seconds
        "10-19 16:50:00.%3N\n",  # one that echoes the escape back
    ],
)
async def test_a_device_clock_without_milliseconds_still_works(tmp_path: Path, clock: str) -> None:
    """Whole seconds count as .000: a line at 16:49:59.999 is old, one at
    16:50:00.000 is not."""
    old = b"10-19 16:49:59.999  1 1 I Old: x\n"
    live = b"10-19 16:50:00.000  1 1 I Live: x\n"
    t = _adb_mock({"logcat": [old, live]}, clock=clock)
    await capture_timeline(0.3, sources={"logcat"}, transport=t, output=tmp_path)
    lines = [x["line"] for x in _records(tmp_path) if not x.get("note")]
    assert lines == [live.decode().rstrip("\n")]


@pytest.mark.parametrize("clock", [None, "date: bad format\n", ""])
async def test_an_unreadable_device_clock_keeps_the_old_line_and_says_so(
    tmp_path: Path, clock: str | None
) -> None:
    t = _adb_mock({"logcat": [_OLD, _LIVE]}, clock=clock)
    r = await capture_timeline(0.3, sources={"logcat"}, transport=t, output=tmp_path)
    assert r.ok
    assert t.calls == [("logcat", {"filter": None, "tail": 1})], "still not the whole buffer"
    assert any("device clock could not be read" in n for n in r.data.notes)
    assert r.data.lines == {"logcat": 2}


async def test_lines_that_keep_looking_old_stop_being_held_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A capture started on Dec 31: every line of the new year sorts before
    the start as text. The backlog stops a fixed time after its first line,
    whatever the stamps say."""
    monkeypatch.setattr(tl, "_BACKLOG_WINDOW_S", 0.1)
    old = b"12-31 23:59:59.800  1 1 I Old: x\n"
    early = b"01-01 00:00:00.100  1 1 I NewYear: inside the window\n"
    later = b"01-01 00:00:00.400  1 1 I NewYear: after the window\n"
    t = _adb_mock({"logcat": [old, early, 0.3, later]}, clock="12-31 23:59:59.900\n")
    r = await capture_timeline(0.6, sources={"logcat"}, transport=t, output=tmp_path)
    lines = [x["line"] for x in _records(tmp_path)]
    assert lines[-1] == later.decode().rstrip("\n")
    assert any("2 line(s) from before the capture started" in n for n in r.data.notes)
    assert (tmp_path / "logcat.txt").read_bytes() == old + early + later


async def test_a_capture_that_ends_with_only_old_logcat_lines_still_notes_them(
    tmp_path: Path,
) -> None:
    """The note is what tells a reader logcat.txt has a line the timeline does
    not; a quiet device must not lose it. Nothing is missing, so no "ended
    before the rest" — unlike dmesg, logcat never said how many there are."""
    t = _adb_mock({"logcat": [_OLD]})
    r = await capture_timeline(0.3, sources={"logcat"}, transport=t, output=tmp_path)
    notes = [x["line"] for x in _records(tmp_path) if x.get("note")]
    assert len(notes) == 1 and "1 line(s) from before the capture started" in notes[0]
    assert "ended before the rest" not in notes[0]
    assert r.data.lines == {"logcat": 0}


async def test_dmesg_lines_already_in_the_buffer_stay_out_of_the_timeline(tmp_path: Path) -> None:
    """The 10-16 capture: 24345 old lines all at +0.1 s, ahead of the UART.
    The lines counted before the stream started go to dmesg.txt only; a note
    marks where the live ones begin."""
    old = [b"[    1.000000] old one\n", b"[    2.000000] old two\n", b"\n"]
    new = [b"[  900.000000] new one\n", b"[  901.000000] new two\n"]
    t = _adb_mock({"dmesg": old + new}, dmesg_count="3\n")
    r = await capture_timeline(0.4, sources={"dmesg"}, transport=t, output=tmp_path)
    assert r.ok
    lines = [x["line"] for x in _records(tmp_path)]
    assert lines[0].startswith("[alb] dmesg: 3 line(s) from before the capture started")
    assert "still in dmesg.txt" in lines[0]
    assert lines[1:] == ["[  900.000000] new one", "[  901.000000] new two"]
    assert r.data.lines == {"dmesg": 2}
    assert (tmp_path / "dmesg.txt").read_bytes() == b"".join(old + new), "raw keeps every line"


async def test_a_capture_that_ends_inside_the_dmesg_backlog_says_so(tmp_path: Path) -> None:
    t = _adb_mock({"dmesg": [b"[ 1.0] a\n", b"[ 2.0] b\n"]}, dmesg_count="5\n")
    r = await capture_timeline(0.3, sources={"dmesg"}, transport=t, output=tmp_path)
    notes = [x["line"] for x in _records(tmp_path) if x.get("note")]
    assert any(
        "2 line(s) from before the capture" in n and "ended before the rest" in n for n in notes
    )
    assert r.data.lines == {"dmesg": 0}


@pytest.mark.parametrize("count", [None, "wc: not found\n", ""])
async def test_an_uncountable_dmesg_buffer_takes_everything_and_says_so(
    tmp_path: Path, count: str | None
) -> None:
    t = _adb_mock({"dmesg": [b"[ 1.0] a\n"]}, dmesg_count=count)
    r = await capture_timeline(0.3, sources={"dmesg"}, transport=t, output=tmp_path)
    assert r.data.lines == {"dmesg": 1}
    assert any("could not be counted" in n for n in r.data.notes)


async def test_logcat_permission_deny_is_a_note(tmp_path: Path) -> None:
    t = _adb_mock({"dmesg": [b"D\n"]})
    t.check_permissions = AsyncMock(
        return_value=PermissionResult(behavior="deny", reason="logcat blocked by rule")
    )
    r = await capture_timeline(0.2, sources={"logcat", "dmesg"}, transport=t, output=tmp_path)
    assert r.ok
    note = next(x for x in _records(tmp_path) if x.get("note"))
    assert note["src"] == "logcat" and "logcat blocked by rule" in note["line"]
    assert [src for src, _ in t.calls] == ["dmesg"]  # denied → logcat never opened


async def test_uart_uses_reconnecting_stream_and_holds_the_duration(tmp_path: Path) -> None:
    """A TCP UART bridge EOFs when idle; the capture must keep reopening the
    stream for the whole duration (the start-capture-then-reboot workflow)."""
    opens = 0

    def stream_read(source: str, **kw) -> AsyncIterator[bytes]:
        nonlocal opens
        opens += 1
        n = opens

        async def gen() -> AsyncIterator[bytes]:
            if n == 2:
                yield b"late boot line\n"

        return gen()

    serial = AsyncMock()
    serial.name = "serial"
    serial.stream_read = stream_read
    # Longer than one reconnect backoff (0.5 s), so a second open can happen.
    r = await capture_timeline(1.2, sources={"uart"}, serial=serial, output=tmp_path)
    assert r.ok
    assert r.data is not None
    assert r.data.duration_captured_ms >= 1000
    assert opens >= 2
    assert [x["line"] for x in _records(tmp_path)] == ["late boot line"]


# ─── input validation / workspace ──────────────────────────────────
@pytest.mark.parametrize("bad", [0, -1, 3601])
async def test_invalid_duration(bad: float) -> None:
    r = await capture_timeline(bad, streams={"logcat": _ending()})
    assert not r.ok and r.error is not None and r.error.code == "INVALID_DURATION"


async def test_unknown_or_empty_sources_rejected() -> None:
    r = await capture_timeline(1, sources={"logcat", "kmsg"})
    assert not r.ok and r.error is not None and r.error.code == "INVALID_SOURCE"
    assert "kmsg" in r.error.message
    r = await capture_timeline(1, sources=set())
    assert not r.ok and r.error is not None and r.error.code == "INVALID_SOURCE"


async def test_default_directory_is_under_device_timeline(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    r = await capture_timeline(
        0.1, sources={"logcat"}, streams={"logcat": _ending(b"x\n")}, device="abc"
    )
    assert r.ok and r.data is not None
    assert r.data.directory.parent == tmp_path / "devices" / "abc" / "timeline"
    assert r.data.directory.name.startswith("capture-")
    assert (r.data.directory / "timeline.jsonl").exists()


async def test_unsafe_device_serial_rejected() -> None:
    r = await capture_timeline(
        0.1, sources={"logcat"}, streams={"logcat": _ending()}, device="../x"
    )
    assert not r.ok and r.error is not None and r.error.code == "INVALID_DEVICE_SERIAL"


# ─── reading back ──────────────────────────────────────────────────
def _write_jsonl(path: Path, recs: list[dict]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")
    return path


_SAMPLE = [
    {"t": 0.0, "wall": "w", "src": "meta", "ev": "start"},
    {"t": 0.5, "wall": "w", "src": "logcat", "line": "app start"},
    {"t": 1.0, "wall": "w", "src": "uart", "line": "=> ", "partial": True},
    {"t": 1.5, "wall": "w", "src": "dmesg", "line": "[ 1.5] mmc0: error -110"},
    {"t": 2.0, "wall": "w", "src": "uart", "line": "Kernel panic"},
    {"t": 9.0, "wall": "w", "src": "logcat", "line": "late error"},
]


def test_iter_timeline_filters_src_grep_and_around(tmp_path: Path) -> None:
    f = _write_jsonl(tmp_path / "timeline.jsonl", _SAMPLE)
    assert [r["line"] for r in iter_timeline(tmp_path)] == [
        "app start",
        "=> ",
        "[ 1.5] mmc0: error -110",
        "Kernel panic",
        "late error",
    ]  # meta skipped; directory or file both accepted
    assert [r["src"] for r in iter_timeline(f, sources={"uart", "dmesg"})] == [
        "uart",
        "dmesg",
        "uart",
    ]
    assert [r["line"] for r in iter_timeline(f, grep="error")] == [
        "[ 1.5] mmc0: error -110",
        "late error",
    ]
    assert [r["t"] for r in iter_timeline(f, around=1.5, window=0.6)] == [1.0, 1.5, 2.0]
    assert [r["t"] for r in iter_timeline(f, sources={"uart"}, around=1.5, window=0.6)] == [
        1.0,
        2.0,
    ]


def test_iter_timeline_reorders_a_record_written_late(tmp_path: Path) -> None:
    """An idle-flushed partial is written up to IDLE_FLUSH_S after its stamp,
    so it can sit after a later-stamped line in the file."""
    recs = [
        {"t": 1.00, "src": "logcat", "line": "a"},
        {"t": 1.15, "src": "dmesg", "line": "c"},
        {"t": 1.05, "src": "uart", "line": "b", "partial": True},
        {"t": 5.00, "src": "logcat", "line": "d"},
    ]
    f = _write_jsonl(tmp_path / "t.jsonl", recs)
    assert [r["line"] for r in iter_timeline(f)] == ["a", "b", "c", "d"]


def test_iter_timeline_skips_half_written_last_line(tmp_path: Path) -> None:
    f = tmp_path / "t.jsonl"
    f.write_text('{"t": 0.1, "src": "uart", "line": "ok"}\n{"t": 0.2, "src": "ua', encoding="utf-8")
    assert [r["line"] for r in iter_timeline(f)] == ["ok"]


def test_format_line_is_plain_and_aligned() -> None:
    assert format_line({"t": 12.345, "src": "uart", "line": "hello"}) == "  +12.345s uart   | hello"
    assert format_line({"t": 0.5, "src": "logcat", "line": "x"}) == "   +0.500s logcat | x"
    assert format_line({"t": 1, "src": "uart", "line": "=> ", "partial": True}).endswith(
        "| =>  [partial]"
    )


async def test_an_empty_uart_from_a_port_never_opened_is_noted(monkeypatch, tmp_path: Path) -> None:
    """Same gap as capture: the hub hangs up on a COM the agent could not
    open, and an empty uart source would read as a silent board."""
    reason = "could not open port 'COM27': PermissionError(13, '拒绝访问。', None, 5)"
    monkeypatch.setattr("alb.capabilities.timeline.hub_serial_open_failure", lambda *a, **k: reason)
    serial = AsyncMock()
    serial.name = "serial"
    serial.tcp_host, serial.tcp_port = "127.0.0.1", 9001
    r = await capture_timeline(
        0.2, sources={"uart"}, serial=serial, streams={"uart": _ending()}, output=tmp_path
    )
    assert not r.ok and r.error is not None, "the only source could not be read"
    assert r.error.code == "TIMELINE_NO_SOURCE"
    notes = [x["line"] for x in _records(tmp_path) if x.get("note")]
    assert any("could not open the serial port" in n and "拒绝访问" in n for n in notes)


async def test_a_uart_that_spoke_is_not_second_guessed(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "alb.capabilities.timeline.hub_serial_open_failure", lambda *a, **k: "anything"
    )
    serial = AsyncMock()
    serial.name = "serial"
    r = await capture_timeline(
        0.2,
        sources={"uart"},
        serial=serial,
        streams={"uart": _ending(b"U-Boot\n")},
        output=tmp_path,
    )
    assert r.ok
    notes = [x["line"] for x in _records(tmp_path) if x.get("note")]
    assert not any("could not open the serial port" in n for n in notes), notes
