"""`stream_read("logcat", since=...)` — the start time reaches logcat as `-T`.

timeline starts logcat at the device's current time so the buffer logcat
would otherwise dump first never arrives (capabilities/timeline.py). These
lock how that time is handed to the device on both shell transports.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import Any

import pytest

import alb.transport.adb as adb_mod
from alb.transport.adb import AdbTransport
from alb.transport.ssh import SshTransport


class _Stdout:
    async def readline(self) -> bytes:
        return b""


class _Proc:
    stdout = _Stdout()


async def _drain(stream: AsyncIterator[bytes]) -> None:
    async for _ in stream:
        pass


async def _adb_argv(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> list[str]:
    seen: list[str] = []

    @contextlib.asynccontextmanager
    async def spawn(*argv: str, **_kw: Any):  # type: ignore[no-untyped-def]
        seen.extend(argv)
        yield _Proc()

    monkeypatch.setattr(adb_mod, "spawn_stream", spawn)
    await _drain(AdbTransport().stream_read("logcat", **kwargs))
    return seen


async def test_adb_logcat_since_comes_before_the_filter(monkeypatch) -> None:
    """logcat reads its options before the filterspecs; a `-T` after them
    would be taken as a filter."""
    argv = await _adb_argv(monkeypatch, since="1760000000.123", filter="*:E")
    i = argv.index("logcat")
    assert argv[i:] == ["logcat", "-v", "threadtime", "-T", "1760000000.123", "*:E"]


async def test_adb_logcat_without_since_is_unchanged(monkeypatch) -> None:
    argv = await _adb_argv(monkeypatch)
    assert argv[argv.index("logcat") :] == ["logcat", "-v", "threadtime"]


class _SshProc:
    def __init__(self) -> None:
        self.stdout = self._lines()

    async def _lines(self) -> AsyncIterator[str]:
        return
        yield  # pragma: no cover - makes this an async generator

    async def __aenter__(self) -> _SshProc:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class _Conn:
    def __init__(self) -> None:
        self.cmds: list[str] = []

    def create_process(self, cmd: str) -> _SshProc:
        self.cmds.append(cmd)
        return _SshProc()

    def close(self) -> None:
        pass

    async def wait_closed(self) -> None:
        pass


async def _ssh_cmd(**kwargs: Any) -> list[str]:
    conn = _Conn()
    t = SshTransport(host="1.2.3.4")

    async def connect() -> _Conn:
        return conn

    t._connect = connect  # type: ignore[method-assign]
    await _drain(t.stream_read("logcat", **kwargs))
    return conn.cmds


async def test_ssh_logcat_since_goes_into_the_command_line() -> None:
    assert await _ssh_cmd(since="1760000000.123", filter="*:E") == [
        "logcat -v threadtime -T 1760000000.123 *:E"
    ]


@pytest.mark.parametrize("bad", ["1760000000; reboot", "$(id)", "1.2.3", "-1"])
async def test_ssh_refuses_a_since_that_is_not_a_time(bad: str) -> None:
    """It is pasted into a remote shell command: only digits and one dot."""
    with pytest.raises(ValueError, match="since"):
        await _ssh_cmd(since=bad)
