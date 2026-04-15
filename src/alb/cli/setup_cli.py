"""CLI: `alb setup <method>` — guided setup for each transport.

Does NOT magically install system packages or auto-configure the device
side (that's inherently manual). What it does:

    1. Probe the host for required binaries / env vars.
    2. Print concrete, copy-pasteable next steps for the user.
    3. Run a verification probe and print the outcome.
    4. Point the user at the relevant docs/methods/*.md file.
"""

from __future__ import annotations

import os
import shutil
import socket
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from alb.cli.common import run_async
from alb.infra.config import load_active
from alb.transport.adb import AdbTransport
from alb.transport.serial import SerialTransport

app = typer.Typer(help="Guided setup for each transport method.")
console = Console()


# ─── Shared helpers ───────────────────────────────────────────────
def _check_binary(name: str) -> tuple[bool, str]:
    path = shutil.which(name)
    if path:
        return True, path
    return False, f"not found in PATH"


def _check_tcp_listen(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def _probe(label: str, ok: bool, detail: str = "") -> None:
    mark = "[green]✓[/]" if ok else "[red]✗[/]"
    line = f"  {mark} {label}"
    if detail:
        line += f" [dim]{detail}[/]"
    console.print(line)


# ─── adb ──────────────────────────────────────────────────────────
@app.command("adb")
def setup_adb() -> None:
    """Verify adb setup (USB, method A; optionally via SSH reverse tunnel)."""
    console.print(Panel.fit("Checking adb / method A setup", border_style="blue"))

    ok_bin, detail = _check_binary("adb")
    _probe("adb binary in PATH", ok_bin, detail)

    server_sock = os.environ.get("ADB_SERVER_SOCKET", "")
    if server_sock:
        _probe("ADB_SERVER_SOCKET set", True, server_sock)
        # Check tunnel target
        if server_sock.startswith("tcp:"):
            rest = server_sock.split(":", 1)[1]
            host_port = rest.split(":") if ":" in rest else [rest, "5037"]
            host, port = host_port[0] or "localhost", int(host_port[1])
            ok_tcp = _check_tcp_listen(host, port)
            _probe(f"tunnel {host}:{port} listening", ok_tcp,
                   "" if ok_tcp else "Xshell tunnel not active?")
    else:
        _probe("ADB_SERVER_SOCKET not set", False,
               "(fine for local USB; required for Xshell-tunnel scenario)")

    # Probe devices
    if ok_bin:
        try:
            settings = load_active()
            t = AdbTransport(
                bin_path=settings.config.adb.bin_path,
                server_socket=settings.config.adb.server_socket or server_sock or None,
            )
            health = run_async(t.health())
            devs = health.get("devices", [])
            _probe("adb reachable", bool(health.get("server_reachable")))
            _probe(f"{len(devs)} device(s) visible", bool(devs))
            if devs:
                table = Table(title="Devices")
                table.add_column("serial")
                table.add_column("state")
                table.add_column("model")
                for d in devs:
                    table.add_row(d.serial, d.state, d.model or "—")
                console.print(table)
        except Exception as e:  # noqa: BLE001
            _probe("probe", False, str(e))

    # Next steps
    console.print()
    console.print(
        Panel.fit(
            "[bold]Next steps if something is off:[/]\n\n"
            "  1. Install platform-tools: https://developer.android.com/tools/adb\n"
            "  2. On the device: Settings → Developer options → USB debugging\n"
            "  3. For Xshell reverse-tunnel scenario:\n"
            "     export ADB_SERVER_SOCKET=tcp:localhost:5037\n"
            "     Add the Remote tunnel in Xshell as per docs/methods/01-ssh-tunnel-adb.md\n\n"
            "[dim]Docs: docs/methods/01-ssh-tunnel-adb.md[/]",
            border_style="yellow",
        )
    )


# ─── wifi ─────────────────────────────────────────────────────────
@app.command("wifi")
def setup_wifi(
    host: str = typer.Argument(..., help="Device IP address"),
    port: int = typer.Option(5555, "--port"),
) -> None:
    """Switch a USB-connected device into TCP mode and connect over WiFi."""
    console.print(Panel.fit(f"Setting up adb-WiFi to {host}:{port}", border_style="blue"))

    ok_bin, _ = _check_binary("adb")
    _probe("adb binary", ok_bin)
    if not ok_bin:
        raise typer.Exit(1)

    settings = load_active()
    t = AdbTransport(
        bin_path=settings.config.adb.bin_path,
        server_socket=settings.config.adb.server_socket,
    )

    # 1. Make sure device is visible first (USB-connected presumably).
    health = run_async(t.health())
    if not health.get("devices"):
        console.print(
            "[red]✗ No USB device visible.[/]\n"
            "Connect a USB-authorised device first (method B requires one-time\n"
            "USB authorisation before going wireless)."
        )
        raise typer.Exit(1)
    _probe("USB device visible", True)

    # 2. Put the device into TCP mode.
    console.print(f"[blue]→[/] running: adb tcpip {port}")
    r = run_async(t._run(["tcpip", str(port)]))
    if not r.ok:
        _probe("adb tcpip", False, r.stderr.strip())
        raise typer.Exit(1)
    _probe("adb tcpip", True, r.stdout.strip() or "ok")

    # 3. Connect over the network.
    console.print(f"[blue]→[/] running: adb connect {host}:{port}")
    r = run_async(t._run(["connect", f"{host}:{port}"]))
    if not r.ok or "unable" in r.stdout.lower() or "failed" in r.stdout.lower():
        _probe("adb connect", False, r.stdout.strip() or r.stderr.strip())
        raise typer.Exit(1)
    _probe("adb connect", True, r.stdout.strip())

    console.print(
        Panel.fit(
            f"[bold green]Done.[/] Try:\n\n"
            f"  alb devices\n"
            f"  alb shell 'getprop ro.build.version.sdk' --device {host}:{port}\n\n"
            "[dim]Docs: docs/methods/02-adb-wifi.md[/]",
            border_style="green",
        )
    )


# ─── ssh ──────────────────────────────────────────────────────────
@app.command("ssh")
def setup_ssh(
    host: str = typer.Argument(..., help="Device host / IP"),
    port: int = typer.Option(22, "--port"),
    user: str = typer.Option("root", "--user"),
    key: str = typer.Option("~/.ssh/alb-device", "--key"),
) -> None:
    """Verify ssh reachability and print key-deployment instructions."""
    console.print(Panel.fit(f"Setting up ssh to {user}@{host}:{port}", border_style="blue"))

    for binary in ("ssh", "ssh-keygen", "scp"):
        ok, detail = _check_binary(binary)
        _probe(f"{binary} binary", ok, detail)

    key_path = Path(os.path.expanduser(key))
    if not key_path.exists():
        console.print(
            f"[yellow]key {key_path} not found. To generate:[/]\n"
            f"  ssh-keygen -t ed25519 -f {key_path} -C 'alb-android-bridge'\n"
        )
    else:
        _probe(f"key present at {key_path}", True)

    ok_tcp = _check_tcp_listen(host, port)
    _probe(f"TCP {host}:{port} reachable", ok_tcp)

    console.print()
    console.print(
        Panel.fit(
            "[bold]Next steps:[/]\n\n"
            "  1. Ensure dropbear / Termux openssh is running on the device.\n"
            f"  2. Copy the public key to the device (over adb or scp):\n"
            f"       adb push {key_path}.pub /data/local/tmp/auth\n"
            "       adb shell 'mkdir -p /data/local/ssh && "
            "mv /data/local/tmp/auth /data/local/ssh/authorized_keys && "
            "chmod 600 /data/local/ssh/authorized_keys'\n"
            "  3. Add an ssh profile entry in workspace/profiles/*.toml:\n"
            "       [[devices]]\n"
            f"       serial = \"{host}\"\n"
            "       transport = \"ssh\"\n"
            f"       ssh_host = \"{host}\"\n"
            f"       ssh_port = {port}\n"
            "  4. Try it:\n"
            f"       ALB_SSH_HOST={host} ALB_SSH_USER={user} "
            f"ALB_SSH_KEY={key} alb --transport ssh shell 'uname -a'\n\n"
            "[dim]Docs: docs/methods/03-android-sshd.md[/]",
            border_style="yellow",
        )
    )


# ─── serial ───────────────────────────────────────────────────────
@app.command("serial")
def setup_serial(
    tcp_host: str = typer.Option("localhost", "--tcp-host"),
    tcp_port: int = typer.Option(9001, "--tcp-port"),
    device: str | None = typer.Option(None, "--device", help="Local /dev/ttyUSB0 etc."),
    baud: int = typer.Option(115200, "--baud"),
) -> None:
    """Verify UART reachability (TCP via ser2net or local /dev/tty*)."""
    console.print(Panel.fit("Checking serial / method G setup", border_style="blue"))

    # Picocom / socat presence (optional but nice to have)
    for binary in ("picocom", "socat"):
        ok, detail = _check_binary(binary)
        _probe(f"{binary} binary", ok,
               "" if ok else "(optional; alb doesn't strictly need it)")

    if device:
        # Local mode
        ok_dev = Path(device).exists()
        _probe(f"device {device}", ok_dev)
        if not ok_dev:
            console.print(
                f"[yellow]Possible fixes:[/]\n"
                f"  - plug in the USB-to-serial cable\n"
                f"  - add yourself to the dialout group:\n"
                f"      sudo usermod -aG dialout $USER   (requires re-login)\n"
            )
            raise typer.Exit(1)
        t = SerialTransport(device=device, baud=baud)
    else:
        ok_tcp = _check_tcp_listen(tcp_host, tcp_port)
        _probe(f"tcp {tcp_host}:{tcp_port} listening", ok_tcp)
        if not ok_tcp:
            console.print(
                "[yellow]Possible fixes:[/]\n"
                "  - make sure ser2net is running on the Windows side\n"
                "  - confirm the Xshell reverse-tunnel rule (Remote, 9001→9001) is active\n"
            )
            raise typer.Exit(1)
        t = SerialTransport(tcp_host=tcp_host, tcp_port=tcp_port, baud=baud)

    info = run_async(t.health())
    _probe("serial endpoint open", bool(info.get("connected")))
    if not info.get("connected"):
        console.print(f"  [dim]error: {info.get('error')}[/]")

    console.print()
    console.print(
        Panel.fit(
            "[bold green]Serial ready.[/] Try:\n\n"
            "  alb serial capture --duration 30\n"
            "  alb serial shell 'dmesg | tail'\n"
            "  alb --transport serial logcat --duration 10   # maps to UART stream\n\n"
            "[dim]Docs: docs/methods/07-uart-serial.md[/]",
            border_style="green",
        )
    )
