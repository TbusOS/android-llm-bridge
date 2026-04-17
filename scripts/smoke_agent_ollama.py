"""Smoke test: 95 → 46 Ollama, verify OllamaBackend + AgentLoop tool-calling.

Run:
    uv run python scripts/smoke_agent_ollama.py
    uv run python scripts/smoke_agent_ollama.py --model qwen2.5:3b

Passes if: health OK, plain chat OK, model decides to call the fake tool,
AgentLoop terminates with a text answer derived from the tool result.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import time

import httpx

from alb.agent import AgentLoop, ChatSession, Message, ToolSpec
from alb.agent.backends.ollama import OllamaBackend


def make_client_transport() -> httpx.AsyncBaseTransport:
    """Force-direct to 46: avoid the stale lower-case no_proxy in the parent env."""
    return httpx.AsyncHTTPTransport(proxy=None)


async def case_health(backend: OllamaBackend) -> None:
    print(f"[1/4] health()  base_url={backend.base_url}  model={backend.model}")
    t0 = time.perf_counter()
    h = await backend.health()
    dt = (time.perf_counter() - t0) * 1000
    print(f"      reachable={h.get('reachable')}  model_present={h.get('model_present')}  {dt:.0f} ms")
    if not h.get("reachable"):
        raise SystemExit(f"      FAIL: {h.get('error')}")
    if not h.get("model_present"):
        print(f"      WARN: model not installed — available: {h.get('installed_models')}")


async def case_plain_chat(backend: OllamaBackend) -> None:
    print("[2/4] plain chat — 'hello' → expect short reply")
    t0 = time.perf_counter()
    resp = await backend.chat(
        [Message(role="user", content="Reply with exactly the word: PONG")],
        temperature=0.0,
        max_tokens=16,
    )
    dt = (time.perf_counter() - t0) * 1000
    out = resp.content.strip()
    tps = resp.usage.get("output_tokens", 0) / (dt / 1000) if dt else 0
    print(f"      reply={out!r}  tokens={resp.usage}  {dt:.0f} ms  ~{tps:.1f} tok/s")


async def case_tool_calling(backend: OllamaBackend) -> None:
    print("[3/4] direct tool-call — ask model to pick get_device_info")
    tools = [
        ToolSpec(
            name="get_device_info",
            description="Get info about the currently connected Android device (model, Android version).",
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
            },
        )
    ]
    t0 = time.perf_counter()
    resp = await backend.chat(
        [Message(role="user", content="What model and Android version is the connected phone?")],
        tools=tools,
        temperature=0.0,
        max_tokens=256,
    )
    dt = (time.perf_counter() - t0) * 1000
    print(f"      finish_reason={resp.finish_reason}  tool_calls={[tc.name for tc in resp.tool_calls]}  {dt:.0f} ms")
    if not resp.tool_calls:
        raise SystemExit(f"      FAIL: model did not request tool — raw content={resp.content!r}")


async def case_agent_loop(backend: OllamaBackend) -> None:
    print("[4/4] AgentLoop end-to-end — fake tool returns Pixel 7 / Android 14")

    async def fake_executor(name: str, args: dict) -> dict:
        assert name == "get_device_info", f"unexpected tool {name}"
        return {"ok": True, "data": {"model": "Pixel 7", "android_version": "14"}}

    tools = [
        ToolSpec(
            name="get_device_info",
            description="Get info about the connected Android device.",
            parameters={"type": "object", "properties": {}, "required": []},
        )
    ]
    loop = AgentLoop(
        backend=backend,
        tools=tools,
        tool_executor=fake_executor,
        system_prompt="You are a concise Android debugging assistant. Call tools to get facts, then answer in one sentence.",
        max_turns=3,
    )
    session = ChatSession.create(backend=backend.name, model=backend.model)
    t0 = time.perf_counter()
    result = await loop.run("What Android device is connected and what OS version?", session=session)
    dt = (time.perf_counter() - t0) * 1000
    if not result.ok:
        raise SystemExit(f"      FAIL: {result.error}")
    reply = (result.data or "").strip()
    print(f"      final: {reply[:160]}")
    print(f"      session_id={session.session_id}  {dt:.0f} ms")
    mentioned = ("pixel 7" in reply.lower()) and ("14" in reply)
    print(f"      tool-output grounded: {mentioned}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=os.environ.get("ALB_OLLAMA_URL", "http://localhost:11434"))
    ap.add_argument("--model", default=os.environ.get("ALB_OLLAMA_MODEL", "qwen2.5:7b"))
    args = ap.parse_args()

    backend = OllamaBackend(
        model=args.model,
        base_url=args.base_url,
        transport=make_client_transport(),
    )
    await case_health(backend)
    await case_plain_chat(backend)
    await case_tool_calling(backend)
    await case_agent_loop(backend)
    print("\nALL GREEN — OllamaBackend + AgentLoop wired end-to-end.")


if __name__ == "__main__":
    asyncio.run(main())
