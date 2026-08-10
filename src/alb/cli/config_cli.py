"""`alb config` — find and read the board's config partition.

Read-only by design. Editing config from here would carry a flash's
destructive power without a flash's protections; that path stays in
`alb flash`.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from alb.capabilities import board_config
from alb.cli.common import get_transport, print_result, run_async

app = typer.Typer(help="Read the board's config partition (needs root, board in Android).")
console = Console()


@app.command("scan")
def cmd_scan(ctx: typer.Context) -> None:
    """Find config partitions BY SHAPE — the partitions whose head parses as
    `KEY="VALUE"`.

    Not by name: the by-name label differs per product, so a name that works
    on one board silently finds nothing on the next. Content survives a
    relabel; a name does not.
    """
    transport = get_transport(ctx)
    result = run_async(board_config.scan(transport))
    if (ctx.obj or {}).get("json") or not result.ok:
        print_result(ctx, result)
        return

    cands = (result.data or {}).get("candidates") or []
    if not cands:
        console.print("[yellow]No config-shaped partition found.[/]")
        # The overwhelmingly common cause, said plainly instead of leaving the
        # operator to conclude "this board has none".
        console.print((result.data or {}).get("hint", ""))
        return
    table = Table(title="config-shaped partitions")
    table.add_column("by-name")
    table.add_column("node")
    table.add_column("KV lines in head", justify="right")
    for c in cands:
        table.add_row(c["name"], c["node"], str(c["lines"]))
    console.print(table)


@app.command("read")
def cmd_read(
    ctx: typer.Context,
    device: str = typer.Argument(..., help="by-name entry, e.g. from `alb config scan`"),
    limit: int = typer.Option(board_config.DEFAULT_READ_BYTES, "--bytes", "-n"),
) -> None:
    """Read the partition and show the parsed keys.

    This is the readback `fastboot flash` does not do: `Writing OKAY` means
    fastboot believes it wrote, not that the partition holds what you sent.
    """
    transport = get_transport(ctx)
    result = run_async(board_config.read(transport, device, limit=limit))
    if (ctx.obj or {}).get("json") or not result.ok:
        print_result(ctx, result)
        return

    d = result.data or {}
    if not d.get("parsed"):
        # Never an empty table: "no keys" and "wrong partition" look identical
        # in one, and the second is far more likely.
        console.print(f"[yellow]{d.get('node')} does not parse as KEY=\"VALUE\".[/]")
        console.print("First bytes as read:")
        console.print(d.get("raw", "")[:600])
        return

    table = Table(title=f"{d.get('node')}  ({d.get('size_bytes')} bytes)")
    table.add_column("key")
    table.add_column("value")
    for e in d.get("entries", []):
        table.add_row(e["key"], e["value"])
    console.print(table)
