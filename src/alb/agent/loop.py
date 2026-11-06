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

import asyncio
import dataclasses
import json
import logging
import os
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
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

_log = logging.getLogger(__name__)


@asynccontextmanager
async def _closing(it: AsyncIterator[Any]) -> AsyncGenerator[AsyncIterator[Any], None]:
    """contextlib.aclosing for any async iterator: close it on the way out
    if it can be closed (backend streams are typed as AsyncIterator)."""
    try:
        yield it
    finally:
        close = getattr(it, "aclose", None)
        if close is not None:
            await close()

_DEFAULT_MAX_TURNS = 8

# Output ceiling per model turn, passed to the backend as max_tokens.
# Without one, Ollama has no limit (num_predict unset) and keeps going past
# the context window by dropping the start: 2026-11-03 one answer to "你的
# 模型id是啥" ran 12 minutes and 17197 tokens on the CPU-only LLM host until
# the client hung up, and every other chat waited behind it. 4096 is far
# above what a normal answer takes (the same question: under 30 tokens)
# and matches the Anthropic backend's own default; at ~24 tokens/s it caps
# a runaway at about 3 minutes. A turn that hits it ends with
# LLM_OUTPUT_TRUNCATED.
DEFAULT_MAX_OUTPUT_TOKENS = 4096

_OUTPUT_TRUNCATED_MESSAGE = (
    "the answer reached the {limit}-token output limit and was cut off"
)
_OUTPUT_TRUNCATED_SUGGESTION = (
    "ask again, or ask for a shorter answer; a reply that repeats itself "
    "until the limit is the model looping, a new session usually helps"
)

# How much of one tool result the model reads (characters of its JSON).
# On the CPU-only LLM host the model reads ~80 tokens/s, so a long result is
# where a turn's time goes: 2026-11-05, ling3-tiny through the hub, a 22.5 KB
# `alb_describe_errors` result took 64.6 s to read and a 15.4 KB
# `alb_log_search` result 78.4 s, while turns whose results stayed under 1 KB
# finished in 8-13 s. 4000 characters is about 1000 tokens, ~12 s to read.
# Only the model's copy is cut: the tool_call_end event (what the page shows)
# carries the whole result. The session keeps the cut copy, because a later
# turn sends the history again and the model would re-read it in full.
# ALB_AGENT_TOOL_RESULT_MAX_CHARS overrides it; 0 passes results whole.
DEFAULT_TOOL_RESULT_MAX_CHARS = 4000
_TOOL_RESULT_ENV = "ALB_AGENT_TOOL_RESULT_MAX_CHARS"

# The web page finds this note in session history (cutInStored in
# web/src/features/v3/chat/toolText.ts matches "…[alb cut N of M characters
# here"); change both together.
# The last sentence is there because a cut list reads like a whole one:
# 2026-11-05, asked for the serial error codes, the model answered with the 3
# that were in the kept part (of 6) and did not say anything was missing.
_TOOL_RESULT_CUT_NOTE = (
    " …[alb cut {cut} of {total} characters here to keep the model fast; the "
    "person sees the whole result on the page. If what you need is in the cut "
    "part, call the tool again with narrower arguments (a filter, a pattern, "
    "fewer lines) instead of guessing. If you answer from the part shown, "
    "tell the person the result was cut and the answer may be incomplete]… "
)


def tool_result_max_chars_from_env() -> int:
    """The limit from ALB_AGENT_TOOL_RESULT_MAX_CHARS, or the default.

    A value that is not a whole number >= 0 is ignored with a warning: a
    typo must not silently turn the limit off (or into something absurd)."""
    raw = os.environ.get(_TOOL_RESULT_ENV, "").strip()
    if not raw:
        return DEFAULT_TOOL_RESULT_MAX_CHARS
    try:
        value = int(raw)
    except ValueError:
        value = -1
    if value < 0:
        _log.warning(
            "%s=%r is not a whole number >= 0; using %d",
            _TOOL_RESULT_ENV, raw, DEFAULT_TOOL_RESULT_MAX_CHARS,
        )
        return DEFAULT_TOOL_RESULT_MAX_CHARS
    return value


def cut_tool_result(
    result: dict[str, Any], max_chars: int
) -> tuple[str, dict[str, int] | None]:
    """The tool result as the model reads it, and how much of it that is.

    The text is the result's JSON, cut to `max_chars` characters when longer
    (0 = never cut). It keeps the start and the end — two thirds and one
    third. The start holds `ok` / `error` and the first entries; the end
    holds the newest log lines, which is what a log question is usually
    about. A note in the gap says how much was cut and how to get the rest.

    The second value is None when nothing was cut, else
    ``{"kept": <characters the model sees>, "total": <characters of the JSON>}``.
    run_stream puts it on the tool_call_end event, so the page can say the
    model saw only part of the output: the model does not reliably say so
    itself (2026-11-05, ling3-tiny answered 3 of 6 error codes and did not
    mention the rest even with the note's last sentence)."""
    full = json.dumps(result, ensure_ascii=False, default=str)
    if max_chars <= 0 or len(full) <= max_chars:
        return full, None
    head = max_chars * 2 // 3
    tail = max_chars - head
    note = _TOOL_RESULT_CUT_NOTE.format(cut=len(full) - head - tail, total=len(full))
    text = full[:head] + note + (full[-tail:] if tail else "")
    return text, {"kept": head + tail, "total": len(full)}


def tool_result_for_model(result: dict[str, Any], max_chars: int) -> str:
    """Only the text of cut_tool_result()."""
    return cut_tool_result(result, max_chars)[0]


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
        user_note: str | None = None,
        max_output_tokens: int | None = DEFAULT_MAX_OUTPUT_TOKENS,
        tool_result_max_chars: int | None = None,
    ) -> None:
        """`user_note` is appended to every user message the model sees,
        e.g. "(请用简体中文回答)".  The session keeps what the person typed.
        It goes on *every* user message, history included, so a request is
        always the previous request plus new messages and the model server
        can reuse what it has already read.

        `max_output_tokens` caps each model turn (see
        DEFAULT_MAX_OUTPUT_TOKENS); None leaves it to the backend.

        `tool_result_max_chars` caps how much of one tool result the model
        reads (see DEFAULT_TOOL_RESULT_MAX_CHARS); None reads it from
        ALB_AGENT_TOOL_RESULT_MAX_CHARS, 0 passes results whole."""
        self.backend = backend
        self.tools = tools
        self.tool_executor = tool_executor
        self.max_turns = max_turns
        self.system_prompt = system_prompt
        self.user_note = user_note
        self.max_output_tokens = max_output_tokens
        self.tool_result_max_chars = (
            tool_result_max_chars_from_env()
            if tool_result_max_chars is None
            else max(0, tool_result_max_chars)
        )

    def _for_model(self, msg: Message) -> Message:
        if msg.role != "user" or not self.user_note:
            return msg
        return dataclasses.replace(msg, content=f"{msg.content}\n\n{self.user_note}")

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
            messages.extend(self._for_model(m) for m in session.messages())

        user_msg = Message(role="user", content=user_input)
        messages.append(self._for_model(user_msg))
        if session is not None:
            session.append(user_msg)

        tools = self.tools if self.backend.supports_tool_calls else None
        last_content = ""

        for _turn in range(self.max_turns):
            try:
                resp: ChatResponse = await self.backend.chat(
                    messages, tools=tools, max_tokens=self.max_output_tokens
                )
            except BackendError as e:
                return fail(
                    code=e.code,
                    message=str(e),
                    suggestion=e.suggestion,
                    category="system",
                    timing_ms=int((perf_counter() - start) * 1000),
                )

            turn_calls, _renamed = self._repair_tool_names(list(resp.tool_calls))

            # Record assistant turn
            asst_msg = Message(
                role="assistant",
                content=resp.content,
                tool_calls=turn_calls,
            )
            messages.append(asst_msg)
            if session is not None:
                session.append(asst_msg)

            last_content = resp.content or last_content

            if not turn_calls:
                if resp.finish_reason == "length":
                    return fail(
                        code="LLM_OUTPUT_TRUNCATED",
                        message=_OUTPUT_TRUNCATED_MESSAGE.format(limit=self.max_output_tokens),
                        suggestion=_OUTPUT_TRUNCATED_SUGGESTION,
                        category="system",
                        timing_ms=int((perf_counter() - start) * 1000),
                    )
                # Model signalled it's done (finish_reason=stop/error)
                return ok(
                    data=last_content,
                    artifacts=artifacts,
                    timing_ms=int((perf_counter() - start) * 1000),
                )

            # Dispatch tool calls
            for tc in turn_calls:
                tool_result = await self._dispatch(tc)
                artifacts.extend(_extract_artifacts(tool_result))

                tool_msg = _tool_message(
                    tc, tool_result_for_model(tool_result, self.tool_result_max_chars)
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
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Streaming variant of run(): yield StreamEvent dicts.

        Event types (subset + agent-loop additions):
            {"type": "token", "delta": "..."}
                Content text, forwarded as soon as the backend produces it,
                from every turn.  A turn that ends in tool calls may say
                something first ("let me check the log"); those tokens arrive
                before its tool_call_start events.  Clients that show one
                answer per question start a new text block after a tool call.

            {"type": "tool_call_start", "id": "...", "name": "...", "arguments": {...},
             "requested_name": "..."}
                `requested_name` is present only when the model's tool name
                was repaired (see _repair_tool_names).
            {"type": "tool_call_end",   "id": "...", "name": "...", "result": {...},
             "cut": {"kept": N, "total": M}}
                `result` is always the whole result.  `cut` is present only
                when the model got a cut copy (see cut_tool_result): it read
                N of the M characters of the result's JSON.

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
            messages.extend(self._for_model(m) for m in session.messages())

        user_msg = Message(role="user", content=user_input)
        messages.append(self._for_model(user_msg))
        if session is not None:
            session.append(user_msg)

        tools = self.tools if self.backend.supports_tool_calls else None
        last_content = ""
        last_usage: dict[str, int] = {}
        last_model = self.backend.model

        for _turn in range(self.max_turns):
            # Drain one streamed turn into (content, tool_calls, usage)
            turn_content = ""
            # What the client has already been sent for this turn.
            streamed = ""
            turn_tool_calls: list[ToolCall] = []
            turn_usage: dict[str, int] = {}
            turn_thinking = ""
            turn_finish = "stop"
            try:
                # When the page stops the chat while we sit in a yield
                # below, the backend's request is closed right away instead
                # of whenever the generator is collected.
                stream = self.backend.stream(
                    messages, tools=tools, max_tokens=self.max_output_tokens
                )
                async with _closing(stream) as bstream:
                    async for ev in bstream:
                        etype = ev.get("type")
                        if etype == "token":
                            # Forward at once.  Before 2026-11-03 this was
                            # held until the turn ended, so a 6 s answer
                            # appeared all at once after 6 s of nothing.
                            delta = ev.get("delta", "")
                            turn_content += delta
                            if on_raw_token is not None:
                                try:
                                    on_raw_token(int(ev.get("tokens", 1)))
                                except Exception:  # noqa: BLE001 — best-effort
                                    pass
                            if delta:
                                streamed += delta
                                yield {"type": "token", "delta": delta}
                        elif etype == "done":
                            turn_content = ev.get("content", turn_content)
                            turn_thinking = ev.get("thinking", "")
                            turn_usage = ev.get("usage", {}) or {}
                            turn_finish = ev.get("finish_reason") or "stop"
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

            turn_tool_calls, renamed = self._repair_tool_names(turn_tool_calls)

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
                # Final turn.  Send whatever the token events did not carry:
                # all of it for a backend that streams no tokens, or a
                # thinking-only reply the backend promoted to content.
                if len(turn_content) > len(streamed) and turn_content.startswith(streamed):
                    yield {"type": "token", "delta": turn_content[len(streamed):]}
                if turn_finish == "length":
                    # The text so far stays on the page and in the session;
                    # the error says why it stops mid-sentence.
                    yield {
                        "type": "done",
                        "ok": False,
                        "error": {
                            "code": "LLM_OUTPUT_TRUNCATED",
                            "message": _OUTPUT_TRUNCATED_MESSAGE.format(limit=self.max_output_tokens),
                            "suggestion": _OUTPUT_TRUNCATED_SUGGESTION,
                        },
                        "session_id": session.session_id if session else "",
                        "timing_ms": int((perf_counter() - start) * 1000),
                        "model": last_model,
                        "usage": last_usage,
                    }
                    return
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
            unanswered = list(turn_tool_calls)
            try:
                for tc in turn_tool_calls:
                    start_ev: dict[str, Any] = {
                        "type": "tool_call_start",
                        "id": tc.id,
                        "name": tc.name,
                        "arguments": tc.arguments,
                    }
                    if tc.id in renamed:
                        start_ev["requested_name"] = renamed[tc.id]
                    yield start_ev
                    tool_result = await self._dispatch(tc)
                    artifacts.extend(_extract_artifacts(tool_result))
                    text, cut = cut_tool_result(tool_result, self.tool_result_max_chars)
                    tool_msg = _tool_message(tc, text)
                    messages.append(tool_msg)
                    if session is not None:
                        session.append(tool_msg)
                    unanswered.pop(0)
                    end_ev: dict[str, Any] = {
                        "type": "tool_call_end",
                        "id": tc.id,
                        "name": tc.name,
                        "result": tool_result,
                    }
                    if cut is not None:
                        end_ev["cut"] = cut
                    yield end_ev
            except (asyncio.CancelledError, GeneratorExit):
                # Stopped in the middle of the calls (the page's Stop). Give
                # each call that has no result one, so the session's next
                # turn sends a history where every tool call is answered.
                if session is not None:
                    for tc in unanswered:
                        session.append(_tool_message(tc, tool_result_for_model(_STOPPED_RESULT, 0)))
                raise

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
    def _repair_tool_names(
        self, calls: list[ToolCall]
    ) -> tuple[list[ToolCall], dict[str, str]]:
        """Fix tool names a backend glued a stray prefix onto.

        Returns (calls, {call id: name the model sent}) — the second part
        only holds the calls that were changed.  See repair_tool_name().
        """
        known = {t.name for t in self.tools}
        out: list[ToolCall] = []
        renamed: dict[str, str] = {}
        for tc in calls:
            fixed = repair_tool_name(tc.name, known)
            if fixed is None:
                out.append(tc)
                continue
            _log.warning("tool name %r repaired to %r", tc.name, fixed)
            renamed[tc.id] = tc.name
            out.append(dataclasses.replace(tc, name=fixed))
        return out, renamed

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


_STOPPED_RESULT: dict[str, Any] = {
    "ok": False,
    "error": {
        "code": "CHAT_STOPPED",
        "message": "the user stopped the chat before this call finished",
        "suggestion": "ask the user before running it again",
    },
}


def repair_tool_name(name: str, known: set[str]) -> str | None:
    """Return the real tool name when `name` is one with junk in front.

    Seen 2026-11-03 with Ling-3.0-tiny through Ollama 0.40: asked about
    error codes, the call came back as ``alb_describealb_describe_errors``
    (the call id doubled too), on both the streaming and the plain API.
    The front part is always the beginning of the real name, as if a
    partial name and the full name were joined.  So the rule is narrow:
    `name` = P + K, where K is a known tool and K starts with P.  A name
    that is merely unknown is left alone and fails as TOOL_NOT_FOUND.
    Returns None when nothing needs fixing.
    """
    if not known or name in known:
        return None
    best: str | None = None
    for k in known:
        glued = len(name) > len(k) and name.endswith(k) and k.startswith(name[: -len(k)])
        if glued and (best is None or len(k) > len(best)):
            best = k
    return best


def _tool_message(tc: ToolCall, content: str) -> Message:
    return Message(role="tool", content=content, tool_call_id=tc.id, name=tc.name)


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
