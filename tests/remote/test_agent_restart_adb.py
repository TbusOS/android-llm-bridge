"""The agent's restart_adb handler: restart the LOCAL adb server, then
re-report devices. The subprocess calls are faked — what's pinned is the
command sequence (kill-server → start-server), that a missing adb aborts the
restart but still re-reports, and that the reply always goes out."""

from __future__ import annotations

import asyncio
import importlib.util
import json
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


# ── conflict detection (exclusive USB interface takeover) ───────────────


def test_conflicts_fuzzy_match_excludes_real_adb():
    hits = agent._adb_conflicts_from_listing(
        [
            ("adb.exe", "100"),  # the real thing — not a conflict
            ("ADB.EXE", "101"),  # case variant of the real thing
            ("vendor_adb.exe", "200"),  # renamed vendor build
            ("HD-Adb.exe", "201"),  # another renamed build
            ("notepad.exe", "300"),  # unrelated
            ("adbd", "400"),  # adb-flavoured non-exe (posix)
        ]
    )
    assert hits == ["vendor_adb.exe pid=200", "HD-Adb.exe pid=201", "adbd pid=400"]


def test_reply_adb_list_reports_conflicts_when_empty(monkeypatch):
    frames: list[str] = []

    class _Ws:
        async def send(self, text):
            frames.append(text)

    async def fake_devices():
        return []

    monkeypatch.setattr(agent, "_adb_devices", fake_devices)
    monkeypatch.setattr(agent, "_find_adb_conflicts", lambda: ["vendor_adb.exe pid=7"])
    asyncio.run(agent._reply_adb_list(_Ws()))
    frame = json.loads(frames[0])
    assert frame["devices"] == []
    assert frame["conflicts"] == ["vendor_adb.exe pid=7"]
    assert agent._STATUS.snapshot()["adb_conflicts"] == ["vendor_adb.exe pid=7"]


def test_reply_adb_list_skips_conflict_scan_when_devices_present(monkeypatch):
    frames: list[str] = []

    class _Ws:
        async def send(self, text):
            frames.append(text)

    async def fake_devices():
        return ["serial-1"]

    def boom():
        raise AssertionError("must not scan when devices are present")

    monkeypatch.setattr(agent, "_adb_devices", fake_devices)
    monkeypatch.setattr(agent, "_find_adb_conflicts", boom)
    asyncio.run(agent._reply_adb_list(_Ws()))
    frame = json.loads(frames[0])
    assert frame["devices"] == ["serial-1"]
    assert frame["conflicts"] == []


async def test_kill_conflicts_only_when_asked(monkeypatch):
    killed_calls: list[bool] = []

    def fake_kill():
        killed_calls.append(True)
        return ["vendor_adb.exe pid=7"]

    async def fake_exec(*_args, **_kw):
        return _FakeProc()

    async def fake_reply(_ws):
        return None

    monkeypatch.setattr(agent, "_kill_adb_conflicts", fake_kill)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(agent, "_reply_adb_list", fake_reply)
    await agent._restart_adb_and_report(object())  # default: no kill
    assert killed_calls == []
    await agent._restart_adb_and_report(object(), kill_conflicts=True)
    assert killed_calls == [True]


def test_kill_adb_conflicts_posix_path(monkeypatch):
    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(agent, "_find_adb_conflicts", lambda: ["vendor_adb pid=4242", "bad pid=x"])
    monkeypatch.setattr(agent.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    killed = agent._kill_adb_conflicts()
    assert killed == ["vendor_adb pid=4242"]  # unparsable pid skipped
    assert sent == [(4242, agent.signal.SIGTERM)]
