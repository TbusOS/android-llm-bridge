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

Status: SKELETON.  Instantiating raises NotImplementedError.  The shape
(constructor signature, `run()` contract) is the authoritative design that
downstream code (`alb chat`, `api/routers/chat.py`) should be written against.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from alb.agent.backend import LLMBackend, ToolSpec
from alb.agent.session import ChatSession
from alb.infra.result import Result

# A tool executor receives (tool_name, arguments_dict) and returns the
# serialisable result that will be fed back to the model as a "tool" message.
ToolExecutor = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


class AgentLoop:
    """Drive a conversation with tool calling.

    Example (future usage, not yet implemented)::

        from alb.infra.prompt_builder import default_agent_prompt

        backend = OllamaBackend(model="qwen2.5:3b")
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
            system_prompt=prompt.as_text(),  # or prompt.as_anthropic()
        )

        session = ChatSession.create()
        result = await loop.run("帮我抓 30 秒 logcat", session=session)
        print(result.data)  # final assistant text
    """

    def __init__(
        self,
        backend: LLMBackend,
        tools: list[ToolSpec],
        tool_executor: ToolExecutor,
        *,
        max_turns: int = 8,
        system_prompt: str | None = None,
    ) -> None:
        self.backend = backend
        self.tools = tools
        self.tool_executor = tool_executor
        self.max_turns = max_turns
        self.system_prompt = system_prompt
        raise NotImplementedError(
            "AgentLoop is an M3 feature — skeleton only.  "
            "See docs/agent.md for the implementation roadmap."
        )

    async def run(
        self,
        user_input: str,
        *,
        session: ChatSession | None = None,
    ) -> Result[str]:
        """Run the loop until the model stops or max_turns reached.

        Returns `Result[str]` where `data` is the final assistant text.
        Tool artifacts (logs, bugreports, etc.) land in `result.artifacts`
        via the tool executor's return envelope.
        """
        raise NotImplementedError


# System prompt composition lives in `alb.infra.prompt_builder`.
# M2 AgentLoop implementations should call `default_agent_prompt(...)` to
# build a cache-friendly prompt with an explicit static/dynamic boundary,
# then pass `prompt.as_anthropic()` or `prompt.as_text()` to the backend.
