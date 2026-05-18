"""CLI: `alb doctor` — one-shot environment health check.

Replaces the manual cocktail of `ss -tln | grep` / `adb devices` /
`alb serial health` / `cat ~/.config/alb/config.toml` that users used
to run when something felt off.

Design:
    - Read-only. Does not mutate config or kill processes.
    - Quick (< 5s). Each TCP probe has a 1.5s timeout.
    - Six layers, in order:
        1. env       — ALB_WORKSPACE / ADB_SERVER_SOCKET / ALB_CONFIG
        2. binaries  — adb / picocom / socat
        3. config    — global config.toml + active profile
        4. adb       — server reachable + visible devices
        5. serial    — TCP endpoint listening + transport.health()
        6. ssh       — only if ALB_SSH_HOST is set
    - Returns exit code 0 (all green / yellow), 1 (≥1 red), 2 (internal
      error). The shell-friendly contract lets CI / oncall scripts wrap
      it.
    - Supports `--json` (inherited from main) for machine consumption.

Skipped layers (e.g. ssh when ALB_SSH_HOST is unset) are reported with
a neutral "skip" status, never red — absence of optional config is not
an error.
"""

from __future__ import annotations

import json
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel

from alb.capabilities.doctor import (
    CheckResult,  # re-exported for backwards-compat / test imports
    Layer,
    compute_exit_code,
    run_doctor as _run_doctor_async,
)
from alb.cli.common import run_async

__all__ = ["CheckResult", "Layer", "run_doctor"]

console = Console()


# ─── Render ────────────────────────────────────────────────────────
_STATUS_MARK = {
    "ok": "[green]✓[/]",
    "warn": "[yellow]![/]",
    "err": "[red]✗[/]",
    "skip": "[dim]○[/]",
}


def _render_text(layers: list[Layer]) -> None:
    summary = {"ok": 0, "warn": 0, "err": 0, "skip": 0}
    for layer in layers:
        worst = layer.worst
        summary[worst] = summary.get(worst, 0) + 1
        header = f"{_STATUS_MARK[worst]} [bold]{layer.name}[/]"
        console.print(header)
        for c in layer.checks:
            mark = _STATUS_MARK.get(c.status, "?")
            line = f"   {mark} {c.name}"
            if c.detail:
                line += f" [dim]— {c.detail}[/]"
            console.print(line)

    total = sum(summary.values())
    msg = (
        f"{summary['err']} err / {summary['warn']} warn / "
        f"{summary['ok']} ok / {summary['skip']} skip "
        f"(across {total} layers)"
    )
    border = (
        "red" if summary["err"]
        else "yellow" if summary["warn"]
        else "green"
    )
    console.print()
    console.print(Panel.fit(msg, title="alb doctor summary", border_style=border))


# ─── Command ───────────────────────────────────────────────────────
def run_doctor(ctx: typer.Context) -> None:
    """Diagnose alb's environment / config / transports in one shot.

    Exit codes: 0 = no errors (warns OK), 1 = at least one error.
    All six probes share the implementation in :mod:`alb.capabilities.doctor`
    with the web ``GET /api/doctor`` route — one source of truth.
    """
    payload = run_async(_run_doctor_async())
    layers = [
        Layer(name=ld["name"], checks=[CheckResult(**c) for c in ld["checks"]])
        for ld in payload["layers"]
    ]

    if (ctx.obj or {}).get("json"):
        print(json.dumps(payload, indent=2))
    else:
        _render_text(layers)

    raise typer.Exit(compute_exit_code(layers))
