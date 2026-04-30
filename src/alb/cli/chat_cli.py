"""`alb chat` — interactive Agent REPL powered by a local LLM backend.

One-shot mode:
    alb chat "帮我看下设备连通性"

Interactive REPL:
    alb chat
    > 列出已安装的 app
    > /quit

Backend & model selection (priority high → low):
    CLI flags  >  env vars (ALB_OLLAMA_URL / ALB_OLLAMA_MODEL)  >  library default

The command only engages an LLM when invoked — MCP/CLI/Web users who drive
alb through Claude Code / Cursor don't pay the cost.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel

from alb.agent.backends import get_backend
from alb.agent.loop import AgentLoop
from alb.agent.session import ChatSession
from alb.cli.common import run_async
from alb.infra.prompt_builder import default_agent_prompt

app = typer.Typer(
    name="chat",
    help="Interactive LLM agent REPL (MCP tools dispatched locally).",
    no_args_is_help=False,
    invoke_without_command=True,
)
console = Console()


@app.callback(invoke_without_command=True)
def chat(
    ctx: typer.Context,
    message: list[str] = typer.Argument(
        None,
        help="Single-turn message. Omit for interactive REPL.",
    ),
    backend: str = typer.Option(
        "ollama",
        "--backend",
        envvar="ALB_AGENT_BACKEND",
        help="LLM backend: ollama | openai-compat | llama-cpp | anthropic.",
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        envvar="ALB_OLLAMA_MODEL",
        help="Model tag (e.g. gemma4:26b, gpt-oss:20b). Default: backend's default.",
    ),
    ollama_url: str | None = typer.Option(
        None,
        "--ollama-url",
        envvar="ALB_OLLAMA_URL",
        help="Ollama daemon URL (e.g. http://localhost:11434).",
    ),
    openai_url: str | None = typer.Option(
        None,
        "--openai-url",
        envvar="ALB_OPENAI_COMPAT_URL",
        help=(
            "OpenAI-compatible server URL up to /v1 "
            "(e.g. http://localhost:8080/v1 for vLLM, http://localhost:1234/v1 for LM Studio)."
        ),
    ),
    api_key: str | None = typer.Option(
        None,
        "--api-key",
        envvar=["ALB_OPENAI_COMPAT_KEY", "OPENAI_API_KEY"],
        help="Bearer token for openai-compat backends; omit for self-hosted servers.",
    ),
    fast: bool = typer.Option(
        False,
        "--fast",
        help=(
            "Shorthand: use gemma4:e4b (smaller, ~9G, same speed as 26b). "
            "Ignored if --model is given or ALB_OLLAMA_MODEL is set in env."
        ),
    ),
    session_id: str | None = typer.Option(
        None,
        "--session",
        help="Resume an existing session; otherwise a new one is created.",
    ),
    max_turns: int = typer.Option(8, "--max-turns", help="Agent loop safety cap."),
    no_tools: bool = typer.Option(
        False,
        "--no-tools",
        help="Chat without exposing MCP tools — plain conversation only.",
    ),
) -> None:
    """Run `alb chat` — one-shot if MESSAGE given, otherwise REPL."""
    # --fast is sugar for `--model gemma4:e4b`
    if fast and model is None:
        model = "gemma4:e4b"

    backend_kwargs: dict = {}
    if model:
        backend_kwargs["model"] = model
    if backend == "openai-compat":
        if openai_url:
            backend_kwargs["base_url"] = openai_url
        if api_key:
            backend_kwargs["api_key"] = api_key
    if ollama_url and backend == "ollama":
        backend_kwargs["base_url"] = ollama_url

    try:
        llm = get_backend(backend, **backend_kwargs)
    except (ValueError, ImportError) as e:
        console.print(f"[red]✗ backend init failed:[/] {e}")
        raise typer.Exit(code=1) from e

    specs, executor = run_async(_load_tools(no_tools))
    prompt = default_agent_prompt(
        device_serial=None,
        transport_name="auto",
        workspace_root=Path.cwd() / "workspace",
        tool_count=len(specs),
    )

    loop = AgentLoop(
        backend=llm,
        tools=specs,
        tool_executor=executor,
        max_turns=max_turns,
        system_prompt=prompt.as_text(),
    )

    if session_id:
        session = ChatSession.load(session_id)
        if not session.meta_file.exists():
            console.print(f"[red]✗ session not found: {session_id}[/]")
            raise typer.Exit(code=1)
    else:
        session = ChatSession.create(backend=llm.name, model=llm.model)

    _print_banner(llm, session, len(specs), no_tools)

    # One-shot mode
    if message:
        user_input = " ".join(message).strip()
        if user_input:
            _run_turn(loop, user_input, session)
        return

    # Interactive REPL
    try:
        while True:
            try:
                user_input = console.input("[bold cyan]you »[/] ").strip()
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]bye[/]")
                break
            if not user_input:
                continue
            if user_input in {"/quit", "/exit", "/q"}:
                console.print("[dim]bye[/]")
                break
            if user_input == "/help":
                _print_help()
                continue
            if user_input == "/session":
                console.print(f"session: [bold]{session.session_id}[/]")
                console.print(f"path:    {session.path}")
                continue
            _run_turn(loop, user_input, session)
    finally:
        console.print(f"[dim]session saved → {session.path}[/]")


async def _load_tools(no_tools: bool):
    if no_tools:
        async def _empty_exec(name, args):
            return {"ok": False, "error": {"code": "TOOL_CALL_FAILED", "message": "--no-tools mode"}}
        return [], _empty_exec
    from alb.mcp.executor import make_agent_tools
    return await make_agent_tools()


def _run_turn(loop: AgentLoop, user_input: str, session: ChatSession) -> None:
    """Stream one turn: live token output + tool-call progress markers."""
    if loop.backend.supports_streaming:
        run_async(_stream_turn(loop, user_input, session))
    else:
        # Fallback for backends that don't support streaming yet
        with console.status("[dim]thinking…[/]", spinner="dots"):
            result = run_async(loop.run(user_input, session=session))
        _render_nonstream_result(result)


async def _stream_turn(loop: AgentLoop, user_input: str, session: ChatSession) -> None:
    """Drive AgentLoop.run_stream and render events live."""
    printed_prefix = False
    async for ev in loop.run_stream(user_input, session=session):
        etype = ev.get("type")
        if etype == "token":
            if not printed_prefix:
                console.print("[bold green]alb »[/] ", end="")
                printed_prefix = True
            console.print(ev.get("delta", ""), end="", highlight=False)
        elif etype == "tool_call_start":
            if printed_prefix:
                console.print()
                printed_prefix = False
            args_preview = _shorten(ev.get("arguments", {}), 80)
            console.print(f"[dim cyan]→ tool[/] [bold]{ev['name']}[/] {args_preview}")
        elif etype == "tool_call_end":
            result = ev.get("result", {})
            ok = result.get("ok")
            if ok is True:
                console.print(f"[dim green]  ← ok[/]")
            elif ok is False:
                err = (result.get("error") or {}).get("code", "FAIL")
                console.print(f"[dim red]  ← {err}[/]")
        elif etype == "done":
            if printed_prefix:
                console.print()  # close token line
            if not ev.get("ok", True):
                err = ev.get("error") or {}
                console.print(f"[red]✗ {err.get('code', 'ERROR')}[/] — {err.get('message', '')}")
                if err.get("suggestion"):
                    console.print(f"[yellow]suggestion:[/] {err['suggestion']}")
                return
            artifacts = ev.get("artifacts") or []
            if artifacts:
                console.print("[dim]artifacts:[/]")
                for a in artifacts:
                    console.print(f"  • {a}")
            timing = ev.get("timing_ms", 0)
            if timing:
                console.print(f"[dim]{timing / 1000:.1f}s[/]")


def _render_nonstream_result(result) -> None:
    if result.ok:
        console.print(Panel(result.data or "[dim](empty reply)[/]", title="alb", border_style="green"))
        if result.artifacts:
            console.print("[dim]artifacts:[/]")
            for a in result.artifacts:
                console.print(f"  • {a}")
        if result.timing_ms:
            console.print(f"[dim]{result.timing_ms / 1000:.1f}s[/]")
    else:
        err = result.error
        console.print(f"[red]✗ {err.code}[/] — {err.message}")
        if err.suggestion:
            console.print(f"[yellow]suggestion:[/] {err.suggestion}")


def _shorten(obj: dict, limit: int) -> str:
    """Compact single-line preview of tool arguments."""
    import json

    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _print_banner(llm, session: ChatSession, tool_count: int, no_tools: bool) -> None:
    tools_line = "no tools" if no_tools else f"{tool_count} tools"
    console.print(
        Panel.fit(
            f"[bold]alb chat[/]  ·  {llm.name}:{llm.model}  ·  {tools_line}\n"
            f"session [dim]{session.session_id}[/]  ·  /help /session /quit",
            border_style="blue",
        )
    )


def _print_help() -> None:
    console.print(
        "[bold]Commands:[/]\n"
        "  /help      — show this help\n"
        "  /session   — print session id + path\n"
        "  /quit      — exit (Ctrl-D / Ctrl-C also work)\n"
        "\n[bold]Tips:[/]\n"
        "  • Ask in plain Chinese or English; the agent picks MCP tools as needed\n"
        "  • Session history is auto-saved under workspace/sessions/<id>/"
    )
