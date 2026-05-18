"""Doctor capability — async-native environment health check.

Re-exposes the CLI's `alb doctor` logic as a reusable capability so the
Web `GET /api/doctor` endpoint can render the same six-layer health
report without forking a subprocess. The CLI (`alb doctor`) and the
REST route share this module verbatim — there is no second source of
truth for "what counts as healthy".

Layers (in order, same as the CLI's text render):
    1. env       — ALB_WORKSPACE / ADB_SERVER_SOCKET / ALB_CONFIG / ALB_PROFILE
    2. binaries  — adb (required) / picocom / socat (optional)
    3. config    — global config.toml + active profile load
    4. adb       — server reachable + visible devices
    5. serial    — TCP endpoint listening + transport.health()
    6. ssh       — only if ALB_SSH_HOST is set

Each probe returns a :class:`Layer` aggregating named :class:`CheckResult`s
with one of ``ok`` / ``warn`` / ``err`` / ``skip``. ``skip`` is informational
("not configured, fine"), never red.

Pure-sync probes (env / binaries / config / ssh) run in a thread pool
via ``asyncio.to_thread`` per L-033 so the FastAPI event loop isn't
blocked. Adb / serial probes are async-native because their underlying
``Transport.health()`` is async.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import asdict, dataclass, field
from typing import Any

from alb.cli._probes import check_binary, check_tcp_listen
from alb.infra.config import global_config_path, load_active
from alb.transport.adb import AdbTransport
from alb.transport.serial import SerialTransport


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


# ─── Per-layer probes (sync) ───────────────────────────────────────
def probe_env() -> Layer:
    layer = Layer("env")
    for var in ("ALB_WORKSPACE", "ADB_SERVER_SOCKET", "ALB_CONFIG", "ALB_PROFILE"):
        val = os.environ.get(var, "")
        if val:
            layer.add(var, "ok", val)
        else:
            # All four are optional; missing is informational, not an error.
            layer.add(var, "skip", "(unset)")
    return layer


def probe_binaries() -> Layer:
    layer = Layer("binaries")
    # adb is required; picocom/socat are nice-to-have for serial.
    ok, detail = check_binary("adb")
    layer.add("adb", "ok" if ok else "err", detail)
    for binary in ("picocom", "socat"):
        ok, detail = check_binary(binary)
        layer.add(
            binary,
            "ok" if ok else "skip",
            detail if ok else "(optional; only needed for `picocom` / `socat` paths)",
        )
    return layer


def probe_config() -> Layer:
    layer = Layer("config")
    path = global_config_path()
    if path.exists():
        layer.add("global config", "ok", str(path))
    else:
        layer.add(
            "global config", "skip", f"(no {path}; defaults will be used)"
        )
    try:
        active = load_active()
        layer.add(
            "profile",
            "ok",
            f"{active.profile.name} ({len(active.profile.devices)} device(s))",
        )
    except Exception as e:  # noqa: BLE001 — surface bad config as err finding
        layer.add("profile", "err", f"load failed: {e}")
    return layer


def probe_ssh() -> Layer:
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


# ─── Per-layer probes (async) ──────────────────────────────────────
async def probe_adb_async() -> Layer:
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
        health = await t.health()
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
            layer.add(
                "devices", "warn", "0 visible — plug in / authorize / check tunnel"
            )
    except Exception as e:  # noqa: BLE001
        layer.add("probe", "err", str(e))
    return layer


async def probe_serial_async() -> Layer:
    layer = Layer("serial")
    try:
        settings = load_active()
    except Exception as e:  # noqa: BLE001 — surface bad config as err finding
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
            t = SerialTransport(
                tcp_host=host, tcp_port=port, baud=cfg.default_baud
            )
            info = await t.health()
            connected = bool(info.get("connected"))
            layer.add(
                "endpoint open",
                "ok" if connected else "warn",
                "" if connected else str(info.get("error", "")),
            )
        except Exception as e:  # noqa: BLE001
            layer.add("endpoint open", "err", str(e))
    return layer


# ─── Orchestrator ──────────────────────────────────────────────────
async def run_doctor() -> dict[str, Any]:
    """Run all six probes concurrently and return the JSON payload.

    Sync probes (env / binaries / config / ssh) execute in a thread pool
    so they don't block the event loop (L-033). Async probes use
    ``Transport.health()`` directly.

    Layer order in the output mirrors the CLI's render order so that a
    front-end iterating ``payload["layers"]`` doesn't need its own sort.
    """
    env, binaries, config, ssh, adb, serial = await asyncio.gather(
        asyncio.to_thread(probe_env),
        asyncio.to_thread(probe_binaries),
        asyncio.to_thread(probe_config),
        asyncio.to_thread(probe_ssh),
        probe_adb_async(),
        probe_serial_async(),
    )
    layers = [env, binaries, config, adb, serial, ssh]
    payload: dict[str, Any] = {
        "layers": [l.to_dict() for l in layers],
    }
    summary = {"ok": 0, "warn": 0, "err": 0, "skip": 0}
    for layer in layers:
        worst = layer.worst
        summary[worst] = summary.get(worst, 0) + 1
    payload["summary"] = summary
    return payload


def compute_exit_code(layers: list[Layer]) -> int:
    """0 if no err in any layer, 1 if at least one err."""
    return 1 if any(l.worst == "err" for l in layers) else 0
