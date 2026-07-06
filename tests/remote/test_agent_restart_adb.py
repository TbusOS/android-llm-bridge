"""The agent's restart_adb handler: restart the LOCAL adb server, then
re-report devices. The subprocess calls are faked — what's pinned is the
command sequence (kill-server → start-server), that a missing adb aborts the
restart but still re-reports, and that the reply always goes out."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

_AGENT_PATH = Path(__file__).resolve().parents[2] / "clients" / "windows-agent" / "alb_agent.py"


def _load_agent():
    spec = importlib.util.spec_from_file_location("alb_agent_restart_under_test", _AGENT_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


agent = _load_agent()


class _FakeProc:
    async def wait(self) -> int:
        return 0


async def test_restarts_then_reports(monkeypatch):
    calls: list[tuple[str, ...]] = []
    replied: list[object] = []

    async def fake_exec(*args, **_kw):
        calls.append(args)
        return _FakeProc()

    async def fake_reply(ws):
        replied.append(ws)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(agent, "_reply_adb_list", fake_reply)
    await agent._restart_adb_and_report(ws := object())
    assert calls == [("adb", "kill-server"), ("adb", "start-server")]
    assert replied == [ws]


async def test_missing_adb_aborts_but_still_reports(monkeypatch):
    calls: list[tuple[str, ...]] = []
    replied: list[object] = []

    async def fake_exec(*args, **_kw):
        calls.append(args)
        raise FileNotFoundError("adb not on PATH")

    async def fake_reply(ws):
        replied.append(ws)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(agent, "_reply_adb_list", fake_reply)
    await agent._restart_adb_and_report(object())
    assert calls == [("adb", "kill-server")]  # aborted after the first failure
    assert len(replied) == 1  # the (empty) enumeration still goes back
