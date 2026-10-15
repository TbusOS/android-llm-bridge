"""A connected agent must stop when asked to (POSIX signals).

The bug: on POSIX the SIGINT / SIGTERM handlers only set `_shutdown`, and a
connected session sits in `async for raw in ws` without ever looking at it —
a stop request to a live agent did nothing until the hub dropped the session
(seen 2026-10-15 running the agent on Linux: SIGTERM, still connected)."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
from pathlib import Path

_AGENT_PATH = Path(__file__).resolve().parents[2] / "clients" / "windows-agent" / "alb_agent.py"


def _load_agent():
    spec = importlib.util.spec_from_file_location("alb_agent_shutdown_under_test", _AGENT_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


agent = _load_agent()


class _QuietWs:
    """Answers the handshake, then stays silent until closed — like a hub
    with nothing to say."""

    def __init__(self) -> None:
        self.closed = asyncio.Event()
        self.sent: list[str] = []

    async def send(self, data: str) -> None:
        self.sent.append(data)

    async def recv(self) -> str:
        return json.dumps({"v": 1, "verb": "hello_ok", "server_version": 1})

    async def close(self) -> None:
        self.closed.set()

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        await self.closed.wait()
        raise StopAsyncIteration


def _dial(ws: _QuietWs):
    class _Ctx:
        async def __aenter__(self):
            return ws

        async def __aexit__(self, *exc):
            return False

    return lambda *_a, **_k: _Ctx()


async def test_a_stop_request_ends_a_live_session(monkeypatch):
    ws = _QuietWs()
    monkeypatch.setattr(agent, "ws_connect", _dial(ws))
    args = argparse.Namespace(hub_url="ws://hub/agent/connect", agent_id="a", name="n", token=None)
    agent._shutdown.clear()
    try:
        session = asyncio.create_task(agent._run_session(args))
        await asyncio.sleep(0.05)
        assert not session.done(), "the session should be up and waiting"
        agent._shutdown.set()  # what the POSIX signal handler does
        await asyncio.wait_for(session, 2)
        assert ws.closed.is_set()
    finally:
        agent._shutdown.clear()
