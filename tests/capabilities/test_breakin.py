"""break_in: catch the bootloader countdown on a fake UART and stop there.

The fake link below scripts the byte stream (chunks, delays, "wait until the
capability has typed N times", EOF) and records every write with a
timestamp. No step is consumed until it has completed, so the capability's
short read timeouts — it cancels a pending read every ``repeat_ms`` while
typing — can never skip part of the script.
"""

from __future__ import annotations

import asyncio
from itertools import pairwise
from pathlib import Path
from time import perf_counter
from typing import Any

import pytest

from alb.capabilities.breakin import (
    DEFAULT_EXPECT,
    break_in,
    parse_send_keys,
)
from alb.infra.permissions import PermissionResult
from alb.transport.base import ShellResult

COUNTDOWN = b"Hit any key to stop autoboot:  3"


# ─── fakes ─────────────────────────────────────────────────────────


class FakeWriter:
    def __init__(self, events: list[tuple[str, float]], *, fail_after: int | None = None) -> None:
        self.writes: list[tuple[float, bytes]] = []
        self.wrote = asyncio.Event()  # set on every write; readers wait on it
        self._events = events
        self._fail_after = fail_after

    def write(self, data: bytes) -> None:
        if self._fail_after is not None and len(self.writes) >= self._fail_after:
            raise BrokenPipeError("bridge went away")
        self.writes.append((perf_counter(), data))
        self._events.append(("write:" + data.decode("latin-1"), perf_counter()))
        self.wrote.set()

    async def drain(self) -> None:
        return None


class FakeReader:
    """Steps: ("data", bytes) | ("sleep", s) | ("await_writes", n) | ("eof",).

    After the last step a read blocks until it is cancelled — that is what a
    live, silent UART looks like to ``StreamReader.read``.
    """

    def __init__(self, steps: list[tuple[Any, ...]], writer: FakeWriter) -> None:
        self._steps = list(steps)
        self._writer = writer
        self._wake_at: float | None = None

    async def read(self, _n: int) -> bytes:
        while self._steps:
            step = self._steps[0]
            kind = step[0]
            if kind == "sleep":
                # Absolute wake time: a cancelled sleep must not restart from zero.
                if self._wake_at is None:
                    self._wake_at = perf_counter() + float(step[1])
                remaining = self._wake_at - perf_counter()
                if remaining > 0:
                    await asyncio.sleep(remaining)
                self._wake_at = None
            elif kind == "await_writes":
                # Single-threaded loop: the count is re-checked after every
                # wake-up, so a write landing before the wait is never missed.
                while len(self._writer.writes) < int(step[1]):
                    await self._writer.wrote.wait()
                    self._writer.wrote.clear()
            elif kind == "data":
                self._steps.pop(0)
                return bytes(step[1])
            elif kind == "eof":
                self._steps.pop(0)
                return b""
            else:  # pragma: no cover - script typo
                raise AssertionError(f"unknown step {step!r}")
            self._steps.pop(0)
        await asyncio.Event().wait()  # silent link: blocks until cancelled
        raise AssertionError("unreachable")  # pragma: no cover


class FakeLink:
    def __init__(self, reader: FakeReader, writer: FakeWriter) -> None:
        self.reader = reader
        self.writer = writer


class FakeSerial:
    """A serial transport whose ``open_session`` hands out scripted links.

    ``sessions`` holds one script per open; the last one is reused if the
    capability reconnects more often than scripted. ``events`` is shared
    with the fake adb transport so test can assert ordering across both.
    """

    name = "serial"

    def __init__(
        self,
        sessions: list[list[tuple[Any, ...]]],
        *,
        events: list[tuple[str, float]] | None = None,
        open_fail: Exception | None = None,
        fail_write_after: int | None = None,
    ) -> None:
        self._sessions = sessions
        self.events: list[tuple[str, float]] = events if events is not None else []
        self._open_fail = open_fail
        self._fail_write_after = fail_write_after
        self.opens = 0
        self.closes = 0
        self.writers: list[FakeWriter] = []

    async def open_session(self) -> FakeLink:
        if self._open_fail is not None:
            raise self._open_fail
        idx = min(self.opens, len(self._sessions) - 1)
        self.opens += 1
        self.events.append(("open", perf_counter()))
        # Only the first link is allowed to die on write; a reconnected link
        # must behave, or a test about one reconnect would see a cascade.
        fail_after = self._fail_write_after if self.opens == 1 else None
        writer = FakeWriter(self.events, fail_after=fail_after)
        self.writers.append(writer)
        return FakeLink(FakeReader(self._sessions[idx], writer), writer)

    async def close_session(self, link: Any) -> None:
        self.closes += 1
        self.events.append(("close", perf_counter()))

    @property
    def writes(self) -> list[bytes]:
        return [data for w in self.writers for _, data in w.writes]


class FakeAdb:
    name = "adb"

    def __init__(self, events: list[tuple[str, float]], *, ok: bool = True) -> None:
        self._events = events
        self._ok = ok
        self.reboots: list[str] = []

    async def check_permissions(self, action: str, input_data: dict) -> PermissionResult:
        return PermissionResult(behavior="allow")

    async def reboot(self, mode: str = "normal") -> ShellResult:
        self.reboots.append(mode)
        self._events.append(("reboot", perf_counter()))
        if self._ok:
            return ShellResult(ok=True, exit_code=0, duration_ms=3)
        return ShellResult(
            ok=False,
            exit_code=1,
            stderr="error: no devices/emulators found",
            error_code="DEVICE_NOT_FOUND",
            duration_ms=3,
        )


def _run(t: FakeSerial, **kw: Any):
    kw.setdefault("timeout_s", 2.0)
    kw.setdefault("prompt_wait_s", 1.0)
    kw.setdefault("repeat_ms", 10)
    kw.setdefault("reconnect_backoff_s", 0.01)
    return break_in(t, **kw)


# ─── matching ──────────────────────────────────────────────────────


async def test_pattern_split_across_two_chunks_still_matches(tmp_path: Path) -> None:
    """A UART hands over arbitrary byte chunks; the countdown may straddle two."""
    t = FakeSerial(
        [
            [
                ("data", b"U-Boot 2024.01 (Jan 01 2024)\r\n"),
                ("data", b"Hit any key to st"),
                ("data", b"op autoboot:  3 "),
                ("await_writes", 1),
                ("data", b"\r\n=> "),
            ]
        ]
    )
    r = await _run(t, output=tmp_path / "x.log")
    assert r.ok, r.error
    assert r.data is not None
    assert r.data.outcome == "stopped_at_prompt"
    assert r.data.matched == "Hit any key to stop autoboot"
    assert r.data.prompt_line == "=>"
    assert r.data.sends >= 1
    assert t.writes[0] == b" "


async def test_keys_are_sent_repeatedly_until_the_prompt_appears(tmp_path: Path) -> None:
    """The first key may be swallowed (UART still settling after reset), so
    the key is repeated every ``repeat_ms`` until the prompt shows — and not
    once more after it."""
    t = FakeSerial(
        [
            [
                ("data", COUNTDOWN),
                ("await_writes", 3),  # the prompt only comes after the 3rd keypress
                ("data", b"\r\n=> "),
            ]
        ]
    )
    r = await _run(t, repeat_ms=10, output=tmp_path / "x.log")
    assert r.ok, r.error
    assert r.data is not None
    assert r.data.sends >= 3
    assert all(data == b" " for data in t.writes)
    # nothing typed after the prompt was seen
    assert len(t.writes) == r.data.sends
    stamps = [ts for ts, _ in t.writers[0].writes]
    gaps = [b - a for a, b in pairwise(stamps)]
    assert all(g >= 0.008 for g in gaps), f"keys sent faster than repeat_ms: {gaps}"


async def test_prompt_never_appears_reports_matched_but_no_prompt(tmp_path: Path) -> None:
    t = FakeSerial([[("data", COUNTDOWN), ("data", b"\r\nLoading Environment from MMC... OK\r\n")]])
    r = await _run(t, prompt_wait_s=0.2, output=tmp_path / "x.log")
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "BREAKIN_NO_PROMPT"
    assert r.error.details["outcome"] == "matched_no_prompt"
    assert r.error.details["matched"] == "Hit any key to stop autoboot"
    assert r.error.details["handoff"] is None
    assert r.error.details["sends"] >= 1
    assert "Loading Environment" in r.error.details["tail"]
    assert r.artifacts and (tmp_path / "x.log").read_bytes() == (
        COUNTDOWN + b"\r\nLoading Environment from MMC... OK\r\n"
    )


# ─── the board went on into its kernel ─────────────────────────────

# What a board whose bootloader could not be stopped prints after the
# countdown: the kernel start, then the Linux console, which answers every
# key typed into it with a fresh prompt.
KERNEL_START = (
    b"\r\nStarting kernel ...\r\n\r\n[    0.000000] Booting Linux on physical CPU 0x0\r\n"
)
CONSOLE_ECHO = b"^C\r\n$ "


async def test_kernel_start_after_the_countdown_ends_the_attempt(tmp_path: Path) -> None:
    """The window is over once the kernel starts. The attempt ends there —
    not after prompt_wait — and no key is typed into the kernel."""
    t = FakeSerial([[("data", COUNTDOWN), ("await_writes", 2), ("data", KERNEL_START)]])
    started = perf_counter()
    r = await _run(t, prompt_wait_s=5.0, output=tmp_path / "x.log")
    assert perf_counter() - started < 2.0, "waited out prompt_wait after the kernel started"
    assert not r.ok and r.error is not None
    assert r.error.code == "BREAKIN_BOOTED_PAST"
    assert r.error.details["outcome"] == "booted_past"
    assert r.error.details["handoff"] == "Starting kernel"
    assert r.error.details["matched"] == "Hit any key to stop autoboot"
    assert len(t.writes) == r.error.details["sends"] == 2
    assert "Booting Linux" in r.error.details["tail"]


async def test_linux_console_prompt_after_the_kernel_start_never_counts(tmp_path: Path) -> None:
    """The 10-16 false success: keys kept going into the Linux console, each
    answered with ``$ ``, and the first pause in the kernel log confirmed it
    as a bootloader prompt. Even a --prompt that accepts ``$`` must not turn
    a board that booted on into "stopped at prompt"."""
    t = FakeSerial(
        [
            [
                ("data", COUNTDOWN),
                ("await_writes", 1),
                ("data", KERNEL_START),
                ("data", CONSOLE_ECHO),
            ]
        ]
    )
    r = await _run(t, prompt=r"\$\s*$", output=tmp_path / "x.log")
    assert not r.ok and r.error is not None
    assert r.error.code == "BREAKIN_BOOTED_PAST"


async def test_kernel_start_voids_a_pending_prompt_candidate(tmp_path: Path) -> None:
    """A prompt-shaped tail is waiting out its quiet period when the kernel
    start arrives, in a chunk that itself ends in a prompt. The kernel start
    is looked at first; otherwise that last ``$ `` would become a fresh
    candidate and the next pause would confirm it."""
    t = FakeSerial(
        [
            [
                ("data", COUNTDOWN),
                ("await_writes", 1),
                ("data", b"\r\n$ "),
                ("sleep", 0.05),
                ("data", KERNEL_START + CONSOLE_ECHO),
            ]
        ]
    )
    r = await _run(t, prompt=r"\$\s*$", output=tmp_path / "x.log")
    assert not r.ok and r.error is not None
    assert r.error.code == "BREAKIN_BOOTED_PAST"


async def test_kernel_start_in_the_same_chunk_as_the_countdown(tmp_path: Path) -> None:
    """A countdown of 0 is followed at once by the kernel start; one UART
    chunk can hold both. Nothing is typed: the window had already closed."""
    t = FakeSerial([[("data", COUNTDOWN + b" 0 " + KERNEL_START + CONSOLE_ECHO)]])
    r = await _run(t, prompt=r"\$\s*$", output=tmp_path / "x.log")
    assert not r.ok and r.error is not None
    assert r.error.code == "BREAKIN_BOOTED_PAST"
    assert r.error.details["sends"] == 0
    assert t.writes == []


async def test_kernel_start_split_across_full_chunks_is_still_seen(tmp_path: Path) -> None:
    """The marker straddles two reads and the second is a full read buffer.
    Looked for only after the per-chunk trim, its first half would already be
    gone; the board would then wait out prompt_wait and report no prompt."""
    t = FakeSerial(
        [
            [
                ("data", COUNTDOWN),
                ("await_writes", 1),
                ("data", b"\r\n" + b"." * 3000 + b"Starting ker"),
                ("data", b"nel ...\r\n" + b"." * 4087),
            ]
        ]
    )
    r = await _run(t, prompt_wait_s=0.5, output=tmp_path / "x.log")
    assert not r.ok and r.error is not None
    assert r.error.code == "BREAKIN_BOOTED_PAST"


async def test_kernel_start_before_the_countdown_does_not_end_the_attempt(
    tmp_path: Path,
) -> None:
    """The reader may come up while the board is still finishing a previous
    boot; the reset that matters is yet to come. Only a kernel start AFTER
    the countdown ends the attempt."""
    t = FakeSerial(
        [
            [
                ("data", b"Starting kernel ...\r\n[    0.000000] Booting Linux\r\n"),
                ("data", COUNTDOWN),
                ("await_writes", 1),
                ("data", b"\r\n=> "),
            ]
        ]
    )
    r = await _run(t, output=tmp_path / "x.log")
    assert r.ok, r.error
    assert r.data is not None and r.data.prompt_line == "=>"


async def test_pattern_never_appears_returns_tail_and_fails(tmp_path: Path) -> None:
    """A bootloader without a key-press stop: the command must end with the
    last lines received, having typed nothing, not with a claim of success."""
    lines = b"".join(b"[    0.%06d] line %d\r\n" % (i, i) for i in range(60))
    t = FakeSerial([[("data", b"BL2: booting\r\n"), ("data", lines)]])
    r = await _run(t, timeout_s=0.3, output=tmp_path / "x.log")
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "BREAKIN_PATTERN_NOT_SEEN"
    assert r.error.details["outcome"] == "pattern_never_seen"
    assert r.error.details["sends"] == 0
    assert t.writes == []
    tail = r.error.details["tail"]
    assert tail.endswith("line 59\n") or tail.endswith("line 59")
    assert len(tail.splitlines()) <= 40
    assert "line 59" in tail and "line 20" in tail and "line 19" not in tail
    assert (tmp_path / "x.log").read_bytes() == b"BL2: booting\r\n" + lines


async def test_prompt_text_before_the_countdown_does_not_count(tmp_path: Path) -> None:
    """The prompt is only looked for in bytes received AFTER the match. With
    an unanchored --prompt (as people type them), a ``=>`` inside an earlier
    banner line must not turn "still booting" into "stopped at prompt"."""
    t = FakeSerial(
        [
            [
                ("data", b"Loading env => done\r\n"),
                ("data", b"something\r\n"),
                ("data", COUNTDOWN),
            ]
        ]
    )
    r = await _run(t, prompt="=>", prompt_wait_s=0.2, output=tmp_path / "x.log")
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "BREAKIN_NO_PROMPT"


async def test_prompt_already_in_the_same_chunk_counts_without_typing(tmp_path: Path) -> None:
    """Another reader on the shared link pressed a key first: the board is at
    the prompt, nothing needs typing, and the result says so honestly."""
    t = FakeSerial([[("data", COUNTDOWN + b" 0 \r\n=> ")]])
    r = await _run(t, output=tmp_path / "x.log")
    assert r.ok, r.error
    assert r.data is not None
    assert r.data.sends == 0
    assert r.data.prompt_line == "=>"


@pytest.mark.parametrize(
    "prompt_bytes, expected_line",
    [
        (b"\r\nU-Boot> ", "U-Boot>"),
        (b"\r\nCFE> ", "CFE>"),
        (b"\r\nMarvell>> ", "Marvell>>"),
    ],
)
async def test_default_prompt_covers_common_consoles(
    tmp_path: Path, prompt_bytes: bytes, expected_line: str
) -> None:
    t = FakeSerial([[("data", COUNTDOWN), ("await_writes", 1), ("data", prompt_bytes)]])
    r = await _run(t, output=tmp_path / "x.log")
    assert r.ok, r.error
    assert r.data is not None and r.data.prompt_line == expected_line


@pytest.mark.parametrize("prompt_bytes", [b"\r\n# ", b"\r\n$ ", b"\r\nconsole:/ $ "])
async def test_default_prompt_does_not_take_a_bare_shell_prompt(
    tmp_path: Path, prompt_bytes: bytes
) -> None:
    """``#`` / ``$`` is what a Linux console prints. A board that slipped past
    its bootloader without a kernel-start line on the UART (a silenced
    console) must still not read as stopped; such bootloaders need --prompt."""
    t = FakeSerial([[("data", COUNTDOWN), ("await_writes", 1), ("data", prompt_bytes)]])
    r = await _run(t, prompt_wait_s=0.5, output=tmp_path / "x.log")
    assert not r.ok and r.error is not None
    assert r.error.code == "BREAKIN_NO_PROMPT"
    assert "--prompt" in r.error.suggestion


async def test_a_chunk_that_merely_ends_in_a_prompt_char_mid_boot_is_not_a_prompt(
    tmp_path: Path,
) -> None:
    """A UART chunk can end anywhere. With a ``#`` --prompt (a bootloader
    whose prompt really is ``#``), `## Booting kernel` cut right after the
    first `#` looks like that prompt for a moment; the bytes that follow
    within milliseconds show a board that is still booting. Reporting
    "stopped at prompt" here is the one failure this command must not have."""
    t = FakeSerial(
        [
            [
                ("data", COUNTDOWN),
                ("await_writes", 1),
                ("data", b"\r\n#"),
                ("sleep", 0.03),
                ("data", b"# Booting kernel from Legacy Image at 82000000 ...\r\n"),
                ("sleep", 0.03),
                ("data", b"Loading Ramdisk to 8f000000 ...\r\n"),
            ]
        ]
    )
    r = await _run(t, prompt=r"(?<!\S)#\s*$", prompt_wait_s=0.5, output=tmp_path / "x.log")
    assert not r.ok and r.error is not None
    assert r.error.code == "BREAKIN_NO_PROMPT"


async def test_progress_hashes_at_the_end_of_a_chunk_are_not_a_prompt(tmp_path: Path) -> None:
    """tftp / flash progress prints `#####` and can pause mid-line; a `#`
    right after another `#` is never a shell prompt."""
    t = FakeSerial([[("data", COUNTDOWN), ("await_writes", 1), ("data", b"\r\nLoading: #####")]])
    r = await _run(t, prompt_wait_s=0.5, output=tmp_path / "x.log")
    assert not r.ok and r.error is not None
    assert r.error.code == "BREAKIN_NO_PROMPT"


async def test_custom_prompt_regex(tmp_path: Path) -> None:
    t = FakeSerial([[("data", COUNTDOWN), ("await_writes", 1), ("data", b"\r\nboard:/ $ ")]])
    r = await _run(t, prompt=r"board:/ \$ $", output=tmp_path / "x.log")
    assert r.ok, r.error
    assert r.data is not None and r.data.prompt_line == "board:/ $"


# ─── ordering: reader first, then the reboot ───────────────────────


async def test_reader_is_open_before_adb_reboot_is_sent(tmp_path: Path) -> None:
    events: list[tuple[str, float]] = []
    t = FakeSerial(
        [[("data", COUNTDOWN), ("await_writes", 1), ("data", b"\r\n=> ")]], events=events
    )
    adb = FakeAdb(events)
    r = await _run(t, reboot="adb", reboot_transport=adb, output=tmp_path / "x.log")
    assert r.ok, r.error
    assert adb.reboots == ["normal"]
    names = [name for name, _ in events]
    assert names.index("open") < names.index("reboot"), names
    assert r.data is not None and r.data.reboot == "adb"


async def test_serial_reboot_goes_over_the_same_link_after_it_is_open(tmp_path: Path) -> None:
    t = FakeSerial([[("data", COUNTDOWN), ("await_writes", 2), ("data", b"\r\n=> ")]])
    r = await _run(t, reboot="serial", output=tmp_path / "x.log")
    assert r.ok, r.error
    assert t.opens == 1, "the reboot must not open a second link"
    assert t.writes[0] == b"reboot\n"
    assert t.writes[1] == b" "
    names = [name for name, _ in t.events]
    assert names.index("open") < names.index("write:reboot\n")


async def test_on_ready_fires_after_open_and_before_reboot(tmp_path: Path) -> None:
    events: list[tuple[str, float]] = []
    t = FakeSerial(
        [[("data", COUNTDOWN), ("await_writes", 1), ("data", b"\r\n=> ")]], events=events
    )
    adb = FakeAdb(events)
    r = await _run(
        t,
        reboot="adb",
        reboot_transport=adb,
        on_ready=lambda: events.append(("ready", perf_counter())),
        output=tmp_path / "x.log",
    )
    assert r.ok, r.error
    names = [name for name, _ in events]
    assert names[:3] == ["open", "ready", "reboot"], names


async def test_adb_reboot_failure_aborts_before_watching(tmp_path: Path) -> None:
    """If the reboot never went through there is no countdown to wait for;
    say so with the reboot's own code and close the link."""
    events: list[tuple[str, float]] = []
    t = FakeSerial([[("data", COUNTDOWN)]], events=events)
    adb = FakeAdb(events, ok=False)
    r = await _run(t, reboot="adb", reboot_transport=adb, output=tmp_path / "x.log")
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "DEVICE_NOT_FOUND"
    assert r.error.details["outcome"] == "reboot_failed"
    assert "not restarted" in r.error.message
    assert t.writes == []
    assert t.closes == 1


async def test_link_open_failure_means_the_board_was_not_touched(tmp_path: Path) -> None:
    events: list[tuple[str, float]] = []
    t = FakeSerial(
        [[]],
        events=events,
        open_fail=ConnectionError("Cannot reach ser2net endpoint 127.0.0.1:9001"),
    )
    adb = FakeAdb(events)
    r = await _run(t, reboot="adb", reboot_transport=adb, output=tmp_path / "x.log")
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "SERIAL_PORT_NOT_FOUND"
    assert r.error.details["outcome"] == "link_error"
    assert adb.reboots == [], "no reboot may be sent when nobody is listening"


# ─── link behaviour ────────────────────────────────────────────────


async def test_eof_is_followed_by_a_reconnect_that_still_catches_the_countdown(
    tmp_path: Path,
) -> None:
    """TCP bridges drop idle clients; the countdown may only arrive on the
    second connection (same reason capture_uart reconnects)."""
    t = FakeSerial(
        [
            [("eof",)],
            [("data", COUNTDOWN), ("await_writes", 1), ("data", b"\r\n=> ")],
        ]
    )
    r = await _run(t, output=tmp_path / "x.log")
    assert r.ok, r.error
    assert t.opens == 2
    assert (tmp_path / "x.log").read_bytes() == COUNTDOWN + b"\r\n=> "


async def test_write_failure_mid_typing_reconnects(tmp_path: Path) -> None:
    t = FakeSerial(
        [
            [("data", COUNTDOWN), ("await_writes", 1)],  # link dies after the 1st key
            [("await_writes", 1), ("data", b"\r\n=> ")],  # the new link gets the 2nd
        ],
        fail_write_after=1,  # the first link breaks on its 2nd write
    )
    r = await _run(t, output=tmp_path / "x.log")
    assert r.ok, r.error
    assert t.opens == 2


async def test_artifact_lands_under_the_device_logs(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    t = FakeSerial([[("data", COUNTDOWN), ("await_writes", 1), ("data", b"\r\n=> ")]])
    r = await _run(t, device="dev1")
    assert r.ok, r.error
    art = r.artifacts[0]
    assert "devices/dev1/logs" in str(art)
    assert art.name.endswith("-breakin-uart.log")
    assert art.exists()


async def test_link_is_closed_on_every_outcome(tmp_path: Path) -> None:
    for sessions, kw in [
        ([[("data", COUNTDOWN), ("await_writes", 1), ("data", b"\r\n=> ")]], {}),
        ([[("data", COUNTDOWN)]], {"prompt_wait_s": 0.1}),
        ([[("data", b"nothing here")]], {"timeout_s": 0.1}),
    ]:
        t = FakeSerial(sessions)
        await _run(t, output=tmp_path / "x.log", **kw)
        assert t.closes == t.opens, (sessions, t.opens, t.closes)


# ─── input validation ──────────────────────────────────────────────


async def test_refuses_non_serial_transport() -> None:
    class Adb:
        name = "adb"

    r = await break_in(Adb())  # type: ignore[arg-type]
    assert not r.ok and r.error is not None
    assert r.error.code == "TRANSPORT_NOT_SUPPORTED"


async def test_adb_reboot_needs_an_adb_transport(tmp_path: Path) -> None:
    t = FakeSerial([[]])
    r = await _run(t, reboot="adb", output=tmp_path / "x.log")
    assert not r.ok and r.error is not None
    assert r.error.code == "TRANSPORT_NOT_CONFIGURED"
    assert t.opens == 0


@pytest.mark.parametrize(
    "kw, code",
    [
        ({"reboot": "usb"}, "BREAKIN_BAD_ARGUMENT"),
        ({"expect": "("}, "INVALID_FILTER"),
        ({"prompt": "["}, "INVALID_FILTER"),
        ({"send": b""}, "BREAKIN_BAD_ARGUMENT"),
        ({"timeout_s": 0}, "INVALID_DURATION"),
        ({"repeat_ms": 0}, "INVALID_DURATION"),
        ({"prompt_quiet_s": 0}, "INVALID_DURATION"),
    ],
)
async def test_bad_arguments_are_rejected_before_opening(kw: dict, code: str) -> None:
    t = FakeSerial([[]])
    r = await _run(t, **kw)
    assert not r.ok and r.error is not None
    assert r.error.code == code
    assert t.opens == 0


def test_default_expect_matches_the_common_countdowns() -> None:
    import re

    rx = re.compile(DEFAULT_EXPECT.encode())
    for line in (
        b"Hit any key to stop autoboot:  3",
        b"Press any key to stop",
        b"autoboot in 2 seconds",
    ):
        assert rx.search(line), line
    assert not rx.search(b"Starting kernel ...")


# ─── --send escapes ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text, expected",
    [
        (" ", b" "),
        ("\\r", b"\r"),
        ("\\n", b"\n"),
        ("\\x03", b"\x03"),
        ("\\e", b"\x1b"),
        ("a\\nb", b"a\nb"),
        ("\\\\", b"\\"),
        ("\\x1B[A", b"\x1b[A"),
    ],
)
def test_send_escapes_are_parsed(text: str, expected: bytes) -> None:
    assert parse_send_keys(text) == expected


@pytest.mark.parametrize("text", ["\\q", "\\x0", "\\xzz", "abc\\"])
def test_bad_send_escapes_are_errors_not_silent_literals(text: str) -> None:
    with pytest.raises(ValueError):
        parse_send_keys(text)
