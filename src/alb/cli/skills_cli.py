"""CLI: `alb skills {generate,show}` — SKILL.md management."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from alb.skills.generator import (
    default_output_path,
    dump_registry_json,
    generate,
    render,
)

app = typer.Typer(help="SKILL.md helpers (LLM-client-facing capability map).")
console = Console()


@app.command("generate")
def cmd_generate(
    output: Path = typer.Option(
        default_output_path(),
        "--output",
        "-o",
        help="Destination file (default: src/alb/skills/SKILL.md).",
    ),
    json_sidecar: bool = typer.Option(
        True, "--json/--no-json", help="Also write SKILL.json next to it."
    ),
) -> None:
    """(Re)generate SKILL.md from the live registry."""
    path = generate(output)
    console.print(f"[green]wrote[/] {path}")
    if json_sidecar:
        jpath = dump_registry_json(path.parent / "SKILL.json")
        console.print(f"[green]wrote[/] {jpath}")


@app.command("show")
def cmd_show() -> None:
    """Print the path of the bundled SKILL.md."""
    print(default_output_path())


@app.command("preview")
def cmd_preview() -> None:
    """Render SKILL.md to stdout without writing a file."""
    print(render())
