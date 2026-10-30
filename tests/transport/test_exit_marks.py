"""Exit codes read from the prompt marker (transport/exit_marks).

The parser is checked on bytes; SETUP is checked on real interactive shells
through a pty — bash always, mksh (Android's /system/bin/sh) when installed.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys

import pytest

from alb.transport.exit_marks import ARM_MAX_BYTES, ARM_TIMEOUT_S, SETUP, ExitMarks

M = b"\x1b]133;D;%d\x07"


def mark(code: int) -> bytes:
    return M % code


def test_the_echo_of_setup_is_never_shown_and_its_own_mark_is_not_reported() -> None:
    em = ExitMarks(0.0)
    shown, codes = em.feed(b"board:/ # " + SETUP.replace(b"\n", b"\r\n"), 0.1)
    assert (shown, codes) == (b"", []) and em.waiting
    shown, codes = em.feed(mark(0) + b"board:/ # ", 0.2)
    assert shown == b"board:/ # " and codes == [] and em.armed


def test_each_prompt_reports_the_command_before_it() -> None:
    em = ExitMarks(0.0)
    em.feed(mark(0) + b"$ ", 0.1)
    shown, codes = em.feed(b"false\r\n" + mark(1) + b"$ true\r\n" + mark(0) + b"$ ", 0.2)
    assert codes == [1, 0]
    assert shown == b"false\r\n$ true\r\n$ "


def test_a_mark_split_across_chunks_is_still_found_and_never_shown() -> None:
    em = ExitMarks(0.0)
    em.feed(mark(0) + b"$ ", 0.1)
    whole = b"out\r\n" + mark(127) + b"$ "
    shown_all, codes_all = b"", []
    for i in range(len(whole)):  # one byte at a time
        shown, codes = em.feed(whole[i : i + 1], 0.2)
        shown_all += shown
        codes_all += codes
    assert codes_all == [127]
    assert shown_all == b"out\r\n$ "


def test_an_escape_that_is_not_a_mark_is_passed_through() -> None:
    em = ExitMarks(0.0)
    em.feed(mark(0), 0.1)
    colour = b"\x1b[1mbold\x1b[0m"
    shown = b"".join(em.feed(colour[i : i + 1], 0.2)[0] for i in range(len(colour)))
    assert shown == colour


def test_a_shell_without_the_mark_gets_its_output_back_after_the_timeout() -> None:
    em = ExitMarks(0.0)
    assert em.feed(b"$ echo-of-setup\r\n$ ", 0.5) == (b"", [])
    assert em.seconds_left(0.5) == pytest.approx(ARM_TIMEOUT_S - 0.5)
    shown, codes = em.feed(b"more", ARM_TIMEOUT_S + 0.1)
    assert shown == b"$ echo-of-setup\r\n$ more" and codes == []
    assert em.unavailable and not em.waiting
    assert em.feed(b"later", 9.0) == (b"later", [])


def test_holding_back_is_bounded() -> None:
    em = ExitMarks(0.0)
    big = b"x" * (ARM_MAX_BYTES + 1)
    shown, _ = em.feed(big, 0.1)
    assert shown == big and em.unavailable


# ── SETUP on real shells ────────────────────────────────────────────────


async def _codes_from(argv: list[str], env: dict[str, str]) -> tuple[list[int], bytes]:
    from alb.transport.interactive import open_pty_subprocess

    shell = await open_pty_subprocess(*argv, env=env)
    loop = asyncio.get_running_loop()
    em = ExitMarks(loop.time())
    codes: list[int] = []
    shown = b""

    async def pump(seconds: float) -> None:
        nonlocal shown
        end = loop.time() + seconds
        while loop.time() < end:
            try:
                chunk = await asyncio.wait_for(shell.read(), timeout=0.1)
            except TimeoutError:
                continue
            if not chunk:
                return
            out, got = em.feed(chunk, loop.time())
            shown += out
            codes.extend(got)

    try:
        await shell.write(SETUP)
        await pump(1.0)
        for cmd in (b"true\n", b"false\n", b"sh -c 'exit 3'\n"):
            await shell.write(cmd)
            await pump(0.6)
    finally:
        await shell.close()
    return codes, shown


def _shell_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("ENV", "PROMPT_COMMAND")}
    env["PS1"] = "probe$ "
    return env


@pytest.mark.skipif(sys.platform == "win32" or not shutil.which("bash"), reason="needs bash and a pty")
async def test_setup_on_real_bash() -> None:
    codes, shown = await _codes_from(["bash", "--norc", "--noprofile", "-i"], _shell_env())
    assert codes == [0, 1, 3]
    assert b"133;D" not in shown, "neither the marker nor the SETUP echo is shown"


@pytest.mark.skipif(sys.platform == "win32" or not shutil.which("mksh"), reason="needs mksh and a pty")
async def test_setup_on_real_mksh() -> None:
    codes, shown = await _codes_from(["mksh", "-i"], _shell_env())
    assert codes == [0, 1, 3]
    assert b"133;D" not in shown and b"\x01" not in shown
