"""CLI: alb playground {chat, models, backends}."""

from __future__ import annotations

import asyncio
import json
import sys

import typer
from rich.console import Console
from rich.table import Table

from alb.agent.backends import get_backend
from alb.agent.playground import (
    PlaygroundParams,
    list_backend_models,
    playground_chat,
    playground_stream,
)
from alb.cli.common import run_async
from alb.infra.registry import BACKENDS

app = typer.Typer(help="Raw model playground (bypasses agent loop).")
console = Console()


@app.command("backends")
def cmd_backends(ctx: typer.Context) -> None:
    """List registered LLM backends with their declared capabilities."""
    if (ctx.obj or {}).get("json"):
        print(json.dumps([
            {
                "name": b.name, "status": b.status,
                "supports_tool_calls": b.supports_tool_calls,
                # ADR-027: replaces `runs_on_cpu: bool` with three-state.
                "host_compute_type": b.host_compute_type,
                "requires": list(b.requires),
                "description": b.description,
            }
            for b in BACKENDS
        ], indent=2))
        return

    table = Table(title="alb playground backends")
    table.add_column("name")
    table.add_column("status")
    table.add_column("host")  # cpu / gpu / remote
    table.add_column("tools?")
    table.add_column("description")
    for b in BACKENDS:
        table.add_row(
            b.name, b.status,
            b.host_compute_type,
            "✓" if b.supports_tool_calls else "-",
            b.description[:80],
        )
    console.print(table)


@app.command("models")
def cmd_models(
    ctx: typer.Context,
    backend: str = typer.Option("ollama", "--backend", "-b"),
    base_url: str | None = typer.Option(None, "--base-url"),
) -> None:
    """Show models installed on a backend (Ollama only for now)."""
    kwargs = {"base_url": base_url} if base_url else {}
    try:
        b = get_backend(backend, **kwargs)
    except (ValueError, ImportError) as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(code=1)

    models = run_async(list_backend_models(b))
    if (ctx.obj or {}).get("json"):
        print(json.dumps(models, indent=2))
        return

    if not models:
        console.print(f"[yellow]No models reported by '{backend}'.[/]")
        return
    table = Table(title=f"{backend} models")
    table.add_column("name")
    table.add_column("size", justify="right")
    table.add_column("modified")
    for m in models:
        size = m.get("size", 0)
        size_h = f"{size / 1_000_000_000:.1f} GB" if size else "?"
        table.add_row(
            str(m.get("name", "?")),
            size_h,
            str(m.get("modified_at", ""))[:19],
        )
    console.print(table)


@app.command("chat")
def cmd_chat(
    ctx: typer.Context,
    message: str = typer.Argument(..., help="Prompt to send."),
    backend: str = typer.Option("ollama", "--backend", "-b"),
    model: str | None = typer.Option(None, "--model", "-m"),
    base_url: str | None = typer.Option(None, "--base-url"),
    system: str | None = typer.Option(None, "--system"),
    temperature: float | None = typer.Option(None, "--temperature"),
    top_p: float | None = typer.Option(None, "--top-p"),
    top_k: int | None = typer.Option(None, "--top-k"),
    repeat_penalty: float | None = typer.Option(None, "--repeat-penalty"),
    seed: int | None = typer.Option(None, "--seed"),
    stop: str | None = typer.Option(None, "--stop", help="Comma-separated stop strings."),
    num_ctx: int | None = typer.Option(None, "--num-ctx"),
    num_predict: int | None = typer.Option(None, "--num-predict"),
    think: bool | None = typer.Option(None, "--think/--no-think"),
    stream: bool = typer.Option(True, "--stream/--no-stream"),
) -> None:
    """One-shot chat with full sampling control. Stdout = model reply, stderr = metrics."""
    backend_kwargs = {}
    if model:
        backend_kwargs["model"] = model
    if base_url:
        backend_kwargs["base_url"] = base_url
    try:
        b = get_backend(backend, **backend_kwargs)
    except (ValueError, ImportError) as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(code=1)

    params = PlaygroundParams(
        temperature=temperature, top_p=top_p, top_k=top_k,
        repeat_penalty=repeat_penalty, seed=seed,
        stop=[s.strip() for s in stop.split(",")] if stop else None,
        num_ctx=num_ctx, num_predict=num_predict, think=think,
    )
    msgs = [{"role": "user", "content": message}]

    if not stream:
        result = run_async(playground_chat(b, msgs, params=params, system=system))
        if (ctx.obj or {}).get("json"):
            print(json.dumps(result.to_dict(), indent=2))
            return
        if not result.ok:
            console.print(f"[red]{result.error}[/]")
            raise typer.Exit(code=1)
        print(result.content)
        _print_metrics(result.metrics.to_dict(), result.model, result.backend)
        return

    async def _run() -> None:
        async for ev in playground_stream(b, msgs, params=params, system=system):
            if ev.get("type") == "token":
                sys.stdout.write(ev.get("delta", ""))
                sys.stdout.flush()
            elif ev.get("type") == "done":
                sys.stdout.write("\n")
                if not ev.get("ok"):
                    console.print(f"[red]{ev.get('error')}[/]")
                    return
                _print_metrics(ev.get("metrics", {}), ev.get("model", ""), ev.get("backend", ""))

    run_async(_run())


def _print_metrics(m: dict, model: str, backend: str) -> None:
    rate = m.get("tokens_per_second") or 0
    console.print(
        f"\n[dim]{backend}/{model} · "
        f"{m.get('output_tokens', 0)} tok @ {rate:.1f} tok/s · "
        f"eval {m.get('eval_duration_ms', 0)} ms · "
        f"prompt {m.get('prompt_eval_duration_ms', 0)} ms · "
        f"total {m.get('total_duration_ms', 0)} ms[/]",
    )
