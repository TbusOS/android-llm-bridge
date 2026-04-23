"""CLI: alb metrics {sample, watch}."""

from __future__ import annotations

import asyncio
import json

import typer
from rich.console import Console
from rich.live import Live
from rich.table import Table

from alb.capabilities.metrics import MetricSample, MetricSampler
from alb.cli.common import get_transport, run_async

app = typer.Typer(help="Live device telemetry (CPU / mem / temp / IO / net).")
console = Console()


@app.command("sample")
def cmd_sample(
    ctx: typer.Context,
    device: str | None = typer.Option(None, "--device", "-d"),
) -> None:
    """Take ONE sample. CPU / net / disk percentages will be 0 (need 2 samples
    for deltas) — use `alb metrics watch` for a stream."""
    t = get_transport(ctx, device_serial=device)
    sampler = MetricSampler(t)
    r = run_async(sampler.sample())
    if (ctx.obj or {}).get("json") or not r.ok:
        print(json.dumps(r.to_dict(), indent=2))
        return
    _print_sample_table(r.data)


@app.command("watch")
def cmd_watch(
    ctx: typer.Context,
    device: str | None = typer.Option(None, "--device", "-d"),
    interval: float = typer.Option(1.0, "--interval", "-i", help="Seconds between samples (>=0.1)."),
    count: int = typer.Option(0, "--count", "-n", help="Stop after N samples (0 = forever)."),
) -> None:
    """Stream samples in a Rich live-updating table. Ctrl-C to stop."""
    t = get_transport(ctx, device_serial=device)
    sampler = MetricSampler(t)
    interval = max(0.1, interval)
    json_mode = (ctx.obj or {}).get("json", False)

    async def loop() -> None:
        seen = 0
        if json_mode:
            while True:
                r = await sampler.sample()
                print(json.dumps(r.to_dict(), separators=(",", ":")))
                seen += 1
                if count and seen >= count:
                    break
                await asyncio.sleep(interval)
            return

        # Rich live-updating single-row table
        table = _empty_table()
        with Live(table, console=console, refresh_per_second=4) as live:
            while True:
                r = await sampler.sample()
                if r.ok and r.data is not None:
                    live.update(_table_for(r.data, seen + 1))
                else:
                    live.update(_table_error(r))
                seen += 1
                if count and seen >= count:
                    break
                await asyncio.sleep(interval)

    try:
        run_async(loop())
    except KeyboardInterrupt:
        console.print("\n[dim]stopped.[/]")


# ─── Render helpers ────────────────────────────────────────────────


def _print_sample_table(s: MetricSample | None) -> None:
    if s is None:
        console.print("[red]no sample[/]")
        return
    console.print(_table_for(s, 1))


def _empty_table() -> Table:
    t = Table(title="alb metrics")
    t.add_column("#")
    t.add_column("cpu%", justify="right")
    t.add_column("temp°C", justify="right")
    t.add_column("mem", justify="right")
    t.add_column("net rx/tx KB/s", justify="right")
    t.add_column("disk r/w KB/s", justify="right")
    t.add_column("gpu freq MHz", justify="right")
    t.add_column("gpu util%", justify="right")
    t.add_column("batt°C", justify="right")
    return t


def _table_for(s: MetricSample, n: int) -> Table:
    t = _empty_table()
    mem_used_mb = s.mem_used_kb // 1024
    mem_total_mb = max(1, s.mem_total_kb // 1024)
    mem_pct = round(100.0 * s.mem_used_kb / max(1, s.mem_total_kb), 1)
    t.add_row(
        str(n),
        f"{s.cpu_pct_total:.1f}",
        f"{s.cpu_temp_c:.1f}",
        f"{mem_used_mb}/{mem_total_mb} MB ({mem_pct}%)",
        f"{s.net_rx_bytes_per_s // 1024}/{s.net_tx_bytes_per_s // 1024}",
        f"{s.disk_read_kb_per_s}/{s.disk_write_kb_per_s}",
        f"{s.gpu_freq_hz // 1_000_000}" if s.gpu_freq_hz else "-",
        str(s.gpu_util_pct) if s.gpu_util_pct >= 0 else "-",
        f"{s.battery_temp_c:.1f}" if s.battery_temp_c else "-",
    )
    return t


def _table_error(result: object) -> Table:  # noqa: ANN001 — Result type
    t = _empty_table()
    t.add_row("?", "[red]err[/]", "-", "-", "-", "-", "-", "-", "-")
    return t
