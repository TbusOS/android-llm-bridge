"""AgentLoop — ReAct-lite tool-calling iteration.

Responsibility:
    Glue between an `LLMBackend` and the existing capability / MCP tool layer.
    Takes user input → asks the model what to do → if model returns tool_calls
    → dispatches them → feeds results back → repeats until the model finishes
    or `max_turns` reached.

Design notes:
    - **Shallow agent**: no planning, no reflection, no sub-agents.  Just a
      loop.  This is deliberate — our target backends include 3B-class local
      CPU models that can't do deep reasoning reliably.  Users who want
      smarter behaviour should point `LLMBackend` at a larger model.
    - **Reuses existing tools**: tool dispatch goes through a caller-provided
      `tool_executor` callable, typically wired to the MCP server's in-process
      tool registry so we don't duplicate routing logic.
    - **Persists via ChatSession**: every message (user / assistant /
      tool-call / tool-result) is appended to JSONL for audit + replay.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from alb.agent.backend import (
    BackendError,
    ChatResponse,
    LLMBackend,
    Message,
    ToolCall,
    ToolSpec,
)
from alb.agent.session import ChatSession
from alb.infra.result import Result, fail, ok

# A tool executor receives (tool_name, arguments_dict) and returns the
# serialisable result that will be fed back to the model as a "tool" message.
ToolExecutor = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]

_DEFAULT_MAX_TURNS = 8


class AgentLoop:
    """Drive a conversation with tool calling.

    Example::

        from alb.infra.prompt_builder import default_agent_prompt
        from alb.agent.backends import get_backend

        backend = get_backend("ollama", model="qwen2.5:3b")
        tools = load_mcp_tool_specs()         # from alb.mcp.server
        executor = make_mcp_tool_executor()   # dispatch by name
        prompt = default_agent_prompt(
            device_serial="abc123",
            transport_name="adb",
            tool_count=len(tools),
        )

        loop = AgentLoop(
            backend=backend,
            tools=tools,
            tool_executor=executor,
            system_prompt=prompt.as_text(),
        )

        session = ChatSession.create(backend=backend.name, model=backend.model)
        result = await loop.run("帮我抓 30 秒 logcat", session=session)
        print(result.data)  # final assistant text
    """

    def __init__(
        self,
        backend: LLMBackend,
        tools: list[ToolSpec],
        tool_executor: ToolExecutor,
        *,
        max_turns: int = _DEFAULT_MAX_TURNS,
        system_prompt: str | None = None,
    ) -> None:
        self.backend = backend
        self.tools = tools
        self.tool_executor = tool_executor
        self.max_turns = max_turns
        self.system_prompt = system_prompt

    async def run(
        self,
        user_input: str,
        *,
        session: ChatSession | None = None,
    ) -> Result[str]:
        """Run the loop until the model stops or max_turns reached.

        Returns `Result[str]` where `data` is the final assistant text.
        Tool artifacts (logs, bugreports, etc.) are pulled from each tool
        result's `artifacts` field (if present) into `result.artifacts`.
        """
        start = perf_counter()
        artifacts: list[Path] = []

        # Compose initial messages: [system, *history, user]
        messages: list[Message] = []
        if self.system_prompt:
            messages.append(Message(role="system", content=self.system_prompt))
        if session is not None:
            messages.extend(session.messages())

        user_msg = Message(role="user", content=user_input)
        messages.append(user_msg)
        if session is not None:
            session.append(user_msg)

        tools = self.tools if self.backend.supports_tool_calls else None
        last_content = ""

        for _turn in range(self.max_turns):
            try:
                resp: ChatResponse = await self.backend.chat(messages, tools=tools)
            except BackendError as e:
                return fail(
                    code=e.code,
                    message=str(e),
                    suggestion=e.suggestion,
                    category="system",
                    timing_ms=int((perf_counter() - start) * 1000),
                )

            # Record assistant turn
            asst_msg = Message(
                role="assistant",
                content=resp.content,
                tool_calls=list(resp.tool_calls),
            )
            messages.append(asst_msg)
            if session is not None:
                session.append(asst_msg)

            last_content = resp.content or last_content

            if not resp.tool_calls:
                # Model signalled it's done (finish_reason=stop/length/error)
                return ok(
                    data=last_content,
                    artifacts=artifacts,
                    timing_ms=int((perf_counter() - start) * 1000),
                )

            # Dispatch tool calls
            for tc in resp.tool_calls:
                tool_result = await self._dispatch(tc)
                artifacts.extend(_extract_artifacts(tool_result))

                tool_msg = Message(
                    role="tool",
                    content=json.dumps(tool_result, ensure_ascii=False, default=str),
                    tool_call_id=tc.id,
                    name=tc.name,
                )
                messages.append(tool_msg)
                if session is not None:
                    session.append(tool_msg)

        # max_turns exhausted without a terminal assistant reply
        return fail(
            code="AGENT_MAX_TURNS_EXCEEDED",
            message=f"agent did not finish within {self.max_turns} turns",
            suggestion="raise max_turns or simplify the request",
            category="system",
            timing_ms=int((perf_counter() - start) * 1000),
        )

    async def run_stream(
        self,
        user_input: str,
        *,
        session: ChatSession | None = None,
        on_raw_token: Callable[[int], None] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Streaming variant of run(): yield StreamEvent dicts.

        Event types (subset + agent-loop additions):
            {"type": "token", "delta": "..."}
                Content token from the *final* assistant turn (one that stops
                without tool_calls).  Tool-decision turns stream silently.

            {"type": "tool_call_start", "id": "...", "name": "...", "arguments": {...}}
            {"type": "tool_call_end",   "id": "...", "name": "...", "result": {...}}

            {"type": "done", "ok": true, "data": "...", "session_id": "...",
             "artifacts": ["..."], "timing_ms": N, "model": "...", "usage": {...}}
            {"type": "done", "ok": false, "error": {"code","message","suggestion"}, ...}

        Requires backend.supports_streaming == True.  Otherwise raises
        NotImplementedError at first turn (mirrors backend.stream behaviour).

        `on_raw_token` is called once per *backend* token event with the
        token count from that chunk (not the buffered final-turn token).
        Used by MetricSampler to drive 1Hz tps_sample without seeing the
        full stream. Callback is sync, must be cheap, and exceptions
        from it are swallowed (best-effort, must not break the chat).
        """
        start = perf_counter()
        artifacts: list[Path] = []

        messages: list[Message] = []
        if self.system_prompt:
            messages.append(Message(role="system", content=self.system_prompt))
        if session is not None:
            messages.extend(session.messages())

        user_msg = Message(role="user", content=user_input)
        messages.append(user_msg)
        if session is not None:
            session.append(user_msg)

        tools = self.tools if self.backend.supports_tool_calls else None
        last_content = ""
        last_usage: dict[str, int] = {}
        last_model = self.backend.model

        for _turn in range(self.max_turns):
            # Drain one streamed turn into (content, tool_calls, usage)
            turn_content = ""
            turn_tool_calls: list[ToolCall] = []
            turn_usage: dict[str, int] = {}
            turn_thinking = ""
            try:
                async for ev in self.backend.stream(messages, tools=tools):
                    etype = ev.get("type")
                    if etype == "token":
                        # Peek: if this turn will end with tool_calls, we
                        # can't know yet.  Buffer and only forward if the
                        # done event has no tool_calls.
                        turn_content += ev.get("delta", "")
                        if on_raw_token is not None:
                            try:
                                on_raw_token(int(ev.get("tokens", 1)))
                            except Exception:  # noqa: BLE001 — best-effort
                                pass
                    elif etype == "done":
                        turn_content = ev.get("content", turn_content)
                        turn_thinking = ev.get("thinking", "")
                        turn_usage = ev.get("usage", {}) or {}
                        last_model = ev.get("model", last_model)
                        for tcd in ev.get("tool_calls") or []:
                            turn_tool_calls.append(ToolCall.from_dict(tcd))
            except BackendError as e:
                yield {
                    "type": "done",
                    "ok": False,
                    "error": {
                        "code": e.code,
                        "message": str(e),
                        "suggestion": e.suggestion,
                    },
                    "session_id": session.session_id if session else "",
                    "timing_ms": int((perf_counter() - start) * 1000),
                }
                return

            # Record assistant turn
            asst_msg = Message(
                role="assistant",
                content=turn_content,
                tool_calls=list(turn_tool_calls),
            )
            messages.append(asst_msg)
            if session is not None:
                session.append(asst_msg)

            last_content = turn_content or last_content
            last_usage = turn_usage or last_usage

            if not turn_tool_calls:
                # Final turn — this is the one users see; stream the collected
                # content as tokens (one delta = full content; backends that
                # want true char-level streaming can re-yield during the loop)
                if turn_content:
                    yield {"type": "token", "delta": turn_content}
                yield {
                    "type": "done",
                    "ok": True,
                    "data": turn_content,
                    "session_id": session.session_id if session else "",
                    "artifacts": [str(p) for p in artifacts],
                    "timing_ms": int((perf_counter() - start) * 1000),
                    "model": last_model,
                    "usage": last_usage,
                    "thinking": turn_thinking,
                }
                return

            # Dispatch tool calls
            for tc in turn_tool_calls:
                yield {
                    "type": "tool_call_start",
                    "id": tc.id,
                    "name": tc.name,
                    "arguments": tc.arguments,
                }
                tool_result = await self._dispatch(tc)
                artifacts.extend(_extract_artifacts(tool_result))
                yield {
                    "type": "tool_call_end",
                    "id": tc.id,
                    "name": tc.name,
                    "result": tool_result,
                }
                tool_msg = Message(
                    role="tool",
                    content=json.dumps(tool_result, ensure_ascii=False, default=str),
                    tool_call_id=tc.id,
                    name=tc.name,
                )
                messages.append(tool_msg)
                if session is not None:
                    session.append(tool_msg)

        # max_turns exhausted
        yield {
            "type": "done",
            "ok": False,
            "error": {
                "code": "AGENT_MAX_TURNS_EXCEEDED",
                "message": f"agent did not finish within {self.max_turns} turns",
                "suggestion": "raise max_turns or simplify the request",
            },
            "session_id": session.session_id if session else "",
            "timing_ms": int((perf_counter() - start) * 1000),
        }

    # ── Internal ─────────────────────────────────────────────────
    async def _dispatch(self, tc: ToolCall) -> dict[str, Any]:
        """Run one tool call; translate exceptions into an error envelope."""
        # Basic defense: unknown tool name shouldn't invoke executor
        known = {t.name for t in self.tools}
        if known and tc.name not in known:
            return {
                "ok": False,
                "error": {
                    "code": "TOOL_NOT_FOUND",
                    "message": f"unknown tool {tc.name!r}",
                    "suggestion": f"pick one of: {sorted(known)[:10]}",
                },
            }
        try:
            result = await self.tool_executor(tc.name, tc.arguments)
        except Exception as e:  # noqa: BLE001 — loop must never crash mid-conversation
            return {
                "ok": False,
                "error": {
                    "code": "TOOL_EXECUTOR_RAISED",
                    "message": f"{type(e).__name__}: {e}",
                    "suggestion": "check tool implementation / retry",
                },
            }
        # Normalise None → empty dict so json.dumps works
        return result if isinstance(result, dict) else {"value": result}


def _extract_artifacts(tool_result: dict[str, Any]) -> list[Path]:
    """Pull file-path artifacts out of a tool result dict.

    Handles both our `Result.to_dict()` shape (`{"artifacts": ["/path/..."]}`)
    and the raw Path case (if a tool returns them directly).
    """
    raw = tool_result.get("artifacts")
    if not raw:
        return []
    out: list[Path] = []
    for item in raw:
        if isinstance(item, Path):
            out.append(item)
        elif isinstance(item, str):
            out.append(Path(item))
    return out


# Back-compat alias — old code/docs may reference `new_tool_call_id`.
def new_tool_call_id() -> str:
    """Helper for synthesising tool-call IDs when a backend omits them."""
    return f"tc_{uuid4().hex[:8]}"
