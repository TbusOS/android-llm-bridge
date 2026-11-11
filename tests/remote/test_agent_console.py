"""The bench agent's console window (2026-11-11).

The window used to get the log file's lines: date, milliseconds, level and
logger name before every message, 37 partition names on one line, the full
exe path and temp folder of every job — and the web console's address on
line four, scrolled away within minutes (colleagues said they could not find
it). Pinned here: the window gets short lines and one block with the web
console's address last, the title bar keeps that address, and the log file
still gets every detail.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import io
import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any

import pytest

_AGENT_PATH = Path(__file__).resolve().parents[2] / "clients" / "windows-agent" / "alb_agent.py"


def _load_agent() -> Any:
    spec = importlib.util.spec_from_file_location("alb_agent_console_under_test", _AGENT_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


agent = _load_agent()

_WEB = "http://hub.example:8765/app/"


def _record(msg: str, level: int = logging.INFO, **extra: Any) -> logging.LogRecord:
    rec = logging.LogRecord("alb-agent", level, __file__, 0, msg, None, None)
    for k, v in extra.items():
        setattr(rec, k, v)
    return rec


# ── one line in the window ──────────────────────────────────────────────


def test_a_window_line_is_the_time_and_the_message() -> None:
    f = agent._ConsoleFormatter()
    assert re.fullmatch(
        r"\d\d:\d\d:\d\d  serial COM4 opened", f.format(_record("serial COM4 opened"))
    )


def test_warnings_and_errors_are_spelled_out() -> None:
    f = agent._ConsoleFormatter()
    assert re.fullmatch(
        r"\d\d:\d\d:\d\d  WARN  hub gone", f.format(_record("hub gone", logging.WARNING))
    )
    assert re.fullmatch(
        r"\d\d:\d\d:\d\d  ERROR bad token", f.format(_record("bad token", logging.ERROR))
    )


def test_the_short_form_replaces_the_message_in_the_window() -> None:
    rec = _record(
        "job: tool vt: D:\\t\\vt.exe -p x (in C:\\Temp\\alb-tool-vt-1)", alb_short="job   vt -p x"
    )
    assert agent._ConsoleFormatter().format(rec).endswith("  job   vt -p x")


def test_colour_only_when_the_window_can_show_it() -> None:
    rec = _record("hub gone", logging.WARNING)
    assert "\x1b[" not in agent._ConsoleFormatter(color=False).format(rec)
    assert "\x1b[33mWARN\x1b[0m" in agent._ConsoleFormatter(color=True).format(rec)


def test_no_colour_for_a_file_or_with_no_color(monkeypatch: pytest.MonkeyPatch) -> None:
    class Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert agent._console_color(io.StringIO()) is False
    if sys.platform != "win32":
        assert agent._console_color(Tty()) is True
    monkeypatch.setenv("NO_COLOR", "1")
    assert agent._console_color(Tty()) is False


# ── where a record goes ─────────────────────────────────────────────────


class _Stream(io.StringIO):
    def isatty(self) -> bool:
        return False


def _wait_for(cond: Any, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not cond() and time.monotonic() < deadline:
        time.sleep(0.01)


def test_detail_goes_to_the_file_and_the_block_to_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = logging.getLogger()
    before = list(root.handlers)
    monkeypatch.setattr(agent, "_AGENT_LOG_PATH", None)
    stream = _Stream()
    console = agent._ConsoleLog(stream)
    console.setFormatter(agent._ConsoleFormatter(console.color))
    root.addHandler(console)
    log = logging.getLogger("alb-agent")
    old_level = log.level
    log.setLevel(logging.INFO)
    try:
        agent._setup_file_logging(str(tmp_path / "agent.log"))
        log.info("both places")
        log.info("board board-a: partitions abl_a,abl_b", extra={"alb_where": "file"})
        log.info("the block", extra={"alb_where": "console"})
        log.info("job: tool vt: D:\\vt.exe x (in C:\\T)", extra={"alb_short": "job   vt x"})
        _wait_for(lambda: "job   vt x" in stream.getvalue())
    finally:
        for h in root.handlers[:]:
            if h not in before:
                root.removeHandler(h)
                h.close()
        log.setLevel(old_level)
    window = stream.getvalue()
    text = (tmp_path / "agent.log").read_text(encoding="utf-8")
    assert "both places" in window and "both places" in text
    assert "the block" in window and "the block" not in text
    assert "abl_a" not in window and "board board-a: partitions abl_a,abl_b" in text
    assert "job   vt x" in window and "C:\\T" not in window
    assert "job: tool vt: D:\\vt.exe x (in C:\\T)" in text, "the file keeps the full line"
    assert "logging to" in text and "logging to" not in window


# ── the block ───────────────────────────────────────────────────────────


def _banner(**over: Any) -> list[tuple[str, str]]:
    kw: dict[str, Any] = {
        "name": "bench-01",
        "build": "fe643f05717a",
        "fastboot": "C:\\adb\\platform-tools\\fastboot.exe",
        "boards": {
            "board-a": {"product": "model-x", "partitions": ["p"] * 37},
            "board-b": {"product": None, "partitions": ["p"] * 3},
            "board-cc": {"product": None, "partitions": ["p"] * 19},
        },
        "partitions": frozenset(),
        "edl": ("D:\\flash\\tools\\QSaharaServer.exe", "D:\\flash\\tools\\fh_loader.exe"),
        "edl_problem": "",
        "tools": ["fb", "fh", "sahara"],
        "tool_problems": 0,
        "log_path": "D:\\alb-windows-agent\\logs\\agent.log",
        "web_url": _WEB,
        "status_url": "http://127.0.0.1:8731",
    }
    kw.update(over)
    lines: list[tuple[str, str]] = agent._banner_lines(**kw)
    return lines


def test_the_block_ends_with_the_web_console_address() -> None:
    lines = _banner()
    texts = [t for t, _ in lines]
    web = next(i for i, t in enumerate(texts) if _WEB in t)
    assert lines[web] == (f"  >>>     {_WEB}", "green")
    assert lines[web - 1] == ("  >>> Web console - open in a browser on any PC:", "green")
    assert texts[web + 1] == "      Local status page (this PC only): http://127.0.0.1:8731"
    assert texts[-1].startswith("====") and len(texts) - web <= 3, "the address is at the bottom"
    assert "  alb agent bench-01 · build fe643f05717a · CONNECTED to hub" in texts


def test_the_block_counts_partitions_instead_of_listing_them() -> None:
    texts = [t for t, _ in _banner()]
    assert "  boards    board-a   product model-x      37 partitions" in texts
    assert "            board-b   product not checked   3 partitions" in texts
    assert "            board-cc  product not checked  19 partitions" in texts
    assert "  9008      QSaharaServer.exe + fh_loader.exe" in texts
    assert "  tools     fb, fh, sahara    (details: alb flash tools)" in texts
    assert max(len(t) for t in texts) <= 100


def test_the_block_for_a_bench_without_flashing() -> None:
    texts = [t for t, _ in _banner(fastboot="", boards={}, edl=None, tools=[], log_path="")]
    assert "  fastboot  not found - flashing is off (fastboot_path in agent.conf)" in texts
    assert not any(t.startswith(("  boards", "  9008", "  tools", "  log file")) for t in texts)


def test_problems_point_at_the_warnings() -> None:
    texts = [t for t, _ in _banner(edl=None, edl_problem="tool missing", tools=[], tool_problems=2)]
    assert "  9008      not enabled (the warning above says why)" in texts
    assert "  tools     none  - 2 not offered, see the warnings above" in texts


@pytest.mark.parametrize(
    ("status_url", "line"),
    [
        ("http://0.0.0.0:8731", "      Local status page: http://0.0.0.0:8731"),
        ("", "      Local status page: off"),
    ],
)
def test_the_status_page_line(status_url: str, line: str) -> None:
    assert line in [t for t, _ in _banner(status_url=status_url)]


# ── the session: block and title ────────────────────────────────────────


class _QuietWs:
    """Answers the handshake, then stays silent until closed."""

    def __init__(self) -> None:
        self.closed = asyncio.Event()

    async def send(self, data: str) -> None:
        pass

    async def recv(self) -> str:
        return json.dumps({"v": 1, "verb": "hello_ok", "server_version": 1})

    async def close(self) -> None:
        self.closed.set()

    def __aiter__(self) -> _QuietWs:
        return self

    async def __anext__(self) -> str:
        await self.closed.wait()
        raise StopAsyncIteration


class _Dial:
    def __init__(self, ws: _QuietWs) -> None:
        self.ws = ws

    async def __aenter__(self) -> _QuietWs:
        return self.ws

    async def __aexit__(self, *exc: object) -> bool:
        return False


@pytest.fixture
def window(monkeypatch: pytest.MonkeyPatch) -> Any:
    """The agent's console handler on a string, its title calls recorded."""
    titles: list[str] = []
    monkeypatch.setattr(agent, "_set_console_title", titles.append)
    stream = _Stream()
    console = agent._ConsoleLog(stream, color=False)
    console.setFormatter(agent._ConsoleFormatter())
    monkeypatch.setattr(agent, "_CONSOLE", console)
    log = logging.getLogger("alb-agent")
    old_level = log.level
    log.setLevel(logging.INFO)
    log.addHandler(console)
    try:
        yield stream, titles
    finally:
        log.removeHandler(console)
        log.setLevel(old_level)
        console.close()


async def test_the_block_and_the_title_when_the_hub_answers(
    window: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    stream, titles = window
    # A fresh event: an asyncio.Event belongs to the loop that first waits on it.
    monkeypatch.setattr(agent, "_shutdown", asyncio.Event())
    ws = _QuietWs()
    monkeypatch.setattr(agent, "ws_connect", lambda *_a, **_k: _Dial(ws))
    args = argparse.Namespace(
        hub_url="ws://hub.example:8765/agent/connect", agent_id="a", name="bench-01", token=None
    )
    agent._shutdown.clear()
    try:
        session = asyncio.create_task(agent._run_session(args))
        await asyncio.sleep(0.05)
        agent._shutdown.set()
        await asyncio.wait_for(session, 2)
    finally:
        agent._shutdown.clear()
    await asyncio.to_thread(_wait_for, lambda: _WEB in stream.getvalue() and titles)
    out = stream.getvalue()
    assert f"  >>>     {_WEB}" in out
    assert "CONNECTED to hub" in out
    assert titles == [f"alb agent bench-01 · connected · {_WEB}"]


async def test_the_title_says_when_the_hub_is_gone(
    window: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stream, titles = window
    monkeypatch.setattr(agent, "_shutdown", asyncio.Event())

    async def unreachable(_args: Any) -> None:
        agent._shutdown.set()  # one round is enough
        raise OSError("connect call failed")

    monkeypatch.setattr(agent, "_run_session", unreachable)
    monkeypatch.setattr(agent, "RECONNECT_BACKOFF_S", (0.01,))
    args = argparse.Namespace(hub_url="ws://hub.example:8765/agent/connect", name="bench-01")
    agent._shutdown.clear()
    try:
        await agent._main_loop(args)
    finally:
        agent._shutdown.clear()
    assert titles == [], "the loop stops before scheduling a retry once asked to stop"

    calls = {"n": 0}

    async def down_once(_args: Any) -> None:
        calls["n"] += 1
        if calls["n"] > 1:
            agent._shutdown.set()
        raise OSError("connect call failed")

    monkeypatch.setattr(agent, "_run_session", down_once)
    try:
        await agent._main_loop(args)
    finally:
        agent._shutdown.clear()
    await asyncio.to_thread(_wait_for, lambda: titles)
    assert titles[0] == f"alb agent bench-01 · DISCONNECTED - retrying · {_WEB}"
