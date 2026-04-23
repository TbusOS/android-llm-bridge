"""CLI: screenshot / ui dump (read-only diagnostics)."""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.tree import Tree

from alb.capabilities.ui import UINode, screenshot, ui_dump
from alb.cli.common import get_transport, print_result, run_async

app = typer.Typer(help="UI diagnostics: screenshot + uiautomator dump (read-only).")
console = Console()


@app.command("screenshot")
def cmd_screenshot(
    ctx: typer.Context,
    output: str | None = typer.Option(None, "--output", "-o", help="Local PNG path."),
    device: str | None = typer.Option(None, "--device", "-d"),
    thumbnail: bool = typer.Option(
        False,
        "--thumbnail",
        help="Also return a small base64 thumbnail (requires Pillow).",
    ),
) -> None:
    """Capture a PNG screenshot and save it to workspace (or --output)."""
    t = get_transport(ctx, device_serial=device)
    result = run_async(
        screenshot(t, device=device, output=output, include_thumbnail=thumbnail)
    )
    print_result(ctx, result)


@app.command("dump")
def cmd_ui_dump(
    ctx: typer.Context,
    output: str | None = typer.Option(None, "--output", "-o", help="Local XML path."),
    device: str | None = typer.Option(None, "--device", "-d"),
    fmt: str = typer.Option(
        "tree",
        "--format",
        "-f",
        help="Output format for human viewing: tree | flat | json",
    ),
) -> None:
    """Dump the current view hierarchy as structured JSON."""
    t = get_transport(ctx, device_serial=device)
    result = run_async(ui_dump(t, device=device, output=output))

    if (ctx.obj or {}).get("json") or fmt == "json":
        print_result(ctx, result)
        return

    if not result.ok or result.data is None:
        print_result(ctx, result)
        raise typer.Exit(code=1)

    data = result.data
    console.print(
        f"[green]{data.node_count} nodes[/] · "
        f"[cyan]{data.top_activity or '?'}[/] · "
        f"saved to [dim]{data.path}[/]"
    )
    if data.root is None:
        console.print("[yellow](empty hierarchy)[/]")
        return

    if fmt == "flat":
        for n in data.root.walk():
            console.print(_format_node_flat(n))
        return

    # default: tree
    tree = Tree(_format_node_label(data.root))
    for c in data.root.children:
        _build_rich_tree(c, tree)
    console.print(tree)


# ─── Render helpers ────────────────────────────────────────────────


def _format_node_label(n: UINode) -> str:
    bits = [f"[bold]{n.class_name.split('.')[-1] or '?'}[/]"]
    if n.resource_id:
        bits.append(f"[dim]#{n.resource_id.split('/')[-1]}[/]")
    if n.text:
        bits.append(f"'{n.text[:40]}'")
    elif n.content_desc:
        bits.append(f"[italic]desc='{n.content_desc[:40]}'[/]")
    flags = []
    if n.clickable:
        flags.append("clickable")
    if n.focused:
        flags.append("focused")
    if not n.enabled:
        flags.append("disabled")
    if flags:
        bits.append(f"[yellow]({', '.join(flags)})[/]")
    return " ".join(bits)


def _build_rich_tree(node: UINode, parent: Tree) -> None:
    branch = parent.add(_format_node_label(node))
    for c in node.children:
        _build_rich_tree(c, branch)


def _format_node_flat(n: UINode) -> str:
    bx = n.bounds
    rid = n.resource_id.split("/")[-1] if n.resource_id else ""
    tag = n.class_name.split(".")[-1] or "?"
    text = n.text or n.content_desc or ""
    return (
        f"[{bx[0]:>4},{bx[1]:>4}]-[{bx[2]:>4},{bx[3]:>4}]  "
        f"{tag:<24}  #{rid:<20}  {text[:40]}"
    )
