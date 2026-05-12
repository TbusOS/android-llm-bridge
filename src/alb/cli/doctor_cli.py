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
import os
import socket
from dataclasses import asdict, dataclass, field
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from alb.cli._probes import check_binary, check_tcp_listen
from alb.cli.common import run_async
from alb.infra.config import global_config_path, load_active
from alb.transport.adb import AdbTransport
from alb.transport.serial import SerialTransport

console = Console()


# ─── Status model ──────────────────────────────────────────────────
@dataclass
class CheckResult:
    """Single named check result. Status is one of {ok, warn, err, skip}."""

    name: str
    status: str  # ok / warn / err / skip
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class Layer:
    name: str
    checks: list[CheckResult] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str = "") -> None:
        self.checks.append(CheckResult(name=name, status=status, detail=detail))

    @property
    def worst(self) -> str:
        # Severity order: skip < ok < warn < err.
        # `skip` is informational ("not configured, that's fine"), so a layer
        # with one ok + many skips should render as ok, not skip.
        order = {"skip": 0, "ok": 1, "warn": 2, "err": 3}
        if not self.checks:
            return "skip"
        return max(self.checks, key=lambda c: order.get(c.status, 4)).status

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.worst,
            "checks": [c.to_dict() for c in self.checks],
        }


# ─── Per-layer probes ──────────────────────────────────────────────
def _probe_env() -> Layer:
    layer = Layer("env")
    for var in ("ALB_WORKSPACE", "ADB_SERVER_SOCKET", "ALB_CONFIG", "ALB_PROFILE"):
        val = os.environ.get(var, "")
        if val:
            layer.add(var, "ok", val)
        else:
            # All four are optional; missing is informational, not an error.
            layer.add(var, "skip", "(unset)")
    return layer


def _probe_binaries() -> Layer:
    layer = Layer("binaries")
    # adb is required; picocom/socat are nice-to-have for serial.
    ok, detail = check_binary("adb")
    layer.add("adb", "ok" if ok else "err", detail)
    for binary in ("picocom", "socat"):
        ok, detail = check_binary(binary)
        layer.add(binary, "ok" if ok else "skip",
                  detail if ok else "(optional; only needed for `picocom` / `socat` paths)")
    return layer


def _probe_config() -> Layer:
    layer = Layer("config")
    path = global_config_path()
    if path.exists():
        layer.add("global config", "ok", str(path))
    else:
        layer.add("global config", "skip",
                  f"(no {path}; defaults will be used)")

    try:
        active = load_active()
        layer.add(
            "profile",
            "ok",
            f"{active.profile.name} ({len(active.profile.devices)} device(s))",
        )
    except Exception as e:  # noqa: BLE001
        layer.add("profile", "err", f"load failed: {e}")
    return layer


def _probe_adb() -> Layer:
    layer = Layer("adb")
    ok_bin, _ = check_binary("adb")
    if not ok_bin:
        layer.add("transport", "skip", "(adb binary missing — see binaries layer)")
        return layer
    try:
        settings = load_active()
        t = AdbTransport(
            bin_path=settings.config.adb.bin_path,
            server_socket=(
                settings.config.adb.server_socket
                or os.environ.get("ADB_SERVER_SOCKET")
                or None
            ),
        )
        health = run_async(t.health())
        reachable = bool(health.get("server_reachable"))
        layer.add(
            "server reachable",
            "ok" if reachable else "err",
            "" if reachable else "adb server not running; try `adb start-server`",
        )
        devs = health.get("devices", []) or []
        if devs:
            online = [d for d in devs if d.state == "device"]
            detail = ", ".join(f"{d.serial}({d.state})" for d in devs[:5])
            if len(devs) > 5:
                detail += f", +{len(devs) - 5} more"
            layer.add(
                f"{len(devs)} device(s) visible",
                "ok" if online else "warn",
                detail,
            )
        else:
            layer.add("devices", "warn", "0 visible — plug in / authorize / check tunnel")
    except Exception as e:  # noqa: BLE001
        layer.add("probe", "err", str(e))
    return layer


def _probe_serial() -> Layer:
    layer = Layer("serial")
    try:
        settings = load_active()
    except Exception as e:  # noqa: BLE001 — surface bad config as err, don't crash
        layer.add("config load", "err", str(e))
        return layer
    cfg = settings.config.serial
    host, port = cfg.default_tcp_host, cfg.default_tcp_port
    listening = check_tcp_listen(host, port, timeout=1.5)
    layer.add(
        f"tcp {host}:{port} listening",
        "ok" if listening else "warn",
        "" if listening else "ser2net not running, or Xshell tunnel inactive",
    )
    if listening:
        try:
            t = SerialTransport(tcp_host=host, tcp_port=port, baud=cfg.default_baud)
            info = run_async(t.health())
            connected = bool(info.get("connected"))
            layer.add(
                "endpoint open",
                "ok" if connected else "warn",
                "" if connected else str(info.get("error", "")),
            )
        except Exception as e:  # noqa: BLE001
            layer.add("endpoint open", "err", str(e))
    return layer


def _probe_ssh() -> Layer:
    layer = Layer("ssh")
    host = os.environ.get("ALB_SSH_HOST")
    if not host:
        layer.add("ssh", "skip", "(ALB_SSH_HOST unset; skipping)")
        return layer
    user = os.environ.get("ALB_SSH_USER", "root")
    port = int(os.environ.get("ALB_SSH_PORT", "22"))
    listening = check_tcp_listen(host, port, timeout=1.5)
    layer.add(
        f"tcp {host}:{port} listening",
        "ok" if listening else "err",
        f"as user {user}",
    )
    return layer


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


def _exit_code(layers: list[Layer]) -> int:
    """0 if no err, 1 if any err."""
    return 1 if any(l.worst == "err" for l in layers) else 0


# ─── Command ───────────────────────────────────────────────────────
def run_doctor(ctx: typer.Context) -> None:
    """Diagnose alb's environment / config / transports in one shot.

    Exit codes: 0 = no errors (warns OK), 1 = at least one error.
    """
    layers = [
        _probe_env(),
        _probe_binaries(),
        _probe_config(),
        _probe_adb(),
        _probe_serial(),
        _probe_ssh(),
    ]

    if (ctx.obj or {}).get("json"):
        payload = {"layers": [l.to_dict() for l in layers]}
        # Compute summary too for easy consumption
        summary = {"ok": 0, "warn": 0, "err": 0, "skip": 0}
        for l in layers:
            summary[l.worst] = summary.get(l.worst, 0) + 1
        payload["summary"] = summary
        print(json.dumps(payload, indent=2))
    else:
        _render_text(layers)

    raise typer.Exit(_exit_code(layers))
