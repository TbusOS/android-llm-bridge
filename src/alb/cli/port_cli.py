"""CLI: `alb port` — reach a TCP port on the device from this machine.

    alb port open <device_port> [--local <port>] [--serial S] [--bind 127.0.0.1]
    alb port check <device_port> [--serial S]

Not `adb forward`: on a remote bench the adb server lives on the host the
board is plugged into, so a forward opened through it listens there, out of
reach. This command opens device streams through the adb server's own
protocol instead (see alb.transport.adb_socket), which works through alb's
adb forwarder exactly as `adb shell` does.

Typical use: `gdbserver :5039 --attach <pid>` on the device, then
`alb port open 5039` here and `target remote 127.0.0.1:5039` in gdb.
"""

from __future__ import annotations

import asyncio
from typing import NoReturn

import typer

from alb.infra.adb_endpoint import AdbEndpoint, resolve_endpoint
from alb.infra.workspace import is_safe_device
from alb.remote.port_channel import BindRefused, LocalPortBusy, PortChannel, PortChannelError
from alb.transport.adb_socket import (
    AdbEndpointUnsupported,
    AdbRefused,
    AdbServerUnreachable,
    AdbSocketError,
    AmbiguousDevice,
    TcpEndpoint,
    open_device_stream,
    parse_endpoint,
    resolve_target,
)

app = typer.Typer(help="Reach a TCP port on the device through the adb server (no adb forward).")

# Plain typer.echo, as in flash_cli: lines here carry text the adb server or
# the device produced, and rich would eat anything in square brackets.

# Exit codes, shared by both commands:
#   0  the device accepted the stream / the listener ran until Ctrl-C
#   1  the device refused the port (check) — the device is reachable, the
#      port is not
#   2  could not even ask: bad endpoint, no adb server, ambiguous device,
#      refused bind, local port busy
_EXIT_REFUSED = 1
_EXIT_CANNOT_ASK = 2

# Codes for the error catalog (alb.infra.errors); printed here so the
# `[fail] CODE: message` shape matches every other alb command.
CODE_DEVICE_REFUSED = "PORT_DEVICE_REFUSED"
CODE_DEVICE_AMBIGUOUS = "PORT_DEVICE_AMBIGUOUS"
CODE_BIND_NOT_LOOPBACK = "PORT_BIND_NOT_LOOPBACK"
CODE_LOCAL_BUSY = "PORT_LOCAL_BUSY"
CODE_ENDPOINT_UNSUPPORTED = "ADB_ENDPOINT_UNSUPPORTED"
CODE_SERVER_UNREACHABLE = "ADB_SERVER_UNREACHABLE"
CODE_PROTOCOL = "ADB_PROTOCOL_ERROR"


def _config_socket() -> str | None:
    """``[transport.adb] server_socket`` from the active config, if set.
    Separate so tests can stand it in without loading a config at all."""
    from alb.transport.factory import active_settings

    return active_settings().config.adb.server_socket


def _endpoint() -> tuple[TcpEndpoint, AdbEndpoint]:
    """Which adb server to talk to — always through the resolver (ADR-057),
    so `alb port` and `alb devices` can never disagree about it."""
    resolved = resolve_endpoint(_config_socket())
    try:
        return parse_endpoint(resolved.socket), resolved
    except AdbEndpointUnsupported as e:
        _fail(
            CODE_ENDPOINT_UNSUPPORTED, str(e), "set ADB_SERVER_SOCKET to a tcp:<host>:<port> value"
        )


def _fail(
    code: str, message: str, advice: str = "", *, exit_code: int = _EXIT_CANNOT_ASK
) -> NoReturn:
    typer.echo(f"[fail] {code}: {message}", err=True)
    if advice:
        typer.echo(f"  try: {advice}", err=True)
    raise typer.Exit(code=exit_code)


def _check_serial(serial: str | None) -> None:
    if serial is not None and not is_safe_device(serial):
        raise typer.BadParameter(f"malformed device serial {serial!r}", param_hint="--serial")


def _check_port(port: int, name: str) -> None:
    if not 1 <= port <= 65535:
        raise typer.BadParameter(f"{name} must be 1-65535, got {port}", param_hint=name)


def _fail_for(e: Exception) -> NoReturn:
    """Map a startup failure to one `[fail]` line with a remedy."""
    if isinstance(e, AmbiguousDevice):
        _fail(CODE_DEVICE_AMBIGUOUS, str(e), "pass --serial <one of them>")
    if isinstance(e, AdbServerUnreachable):
        _fail(
            CODE_SERVER_UNREACHABLE,
            str(e),
            "start the hub (alb-api) or the local adb server; `alb status` shows which is expected",
        )
    if isinstance(e, BindRefused):
        _fail(CODE_BIND_NOT_LOOPBACK, str(e))
    if isinstance(e, LocalPortBusy):
        _fail(CODE_LOCAL_BUSY, str(e))
    if isinstance(e, AdbSocketError | PortChannelError):
        _fail(CODE_PROTOCOL, str(e), "check that the endpoint is an adb server: `alb status`")
    raise e


# ── open ───────────────────────────────────────────────────────────────


async def _serve(channel: PortChannel) -> None:
    """Runs until cancelled (Ctrl-C). Separate so tests can stand it in."""
    await channel.serve_forever()


async def _listen(channel: PortChannel, resolved: AdbEndpoint) -> None:
    port = await channel.start()
    assert channel.target is not None
    typer.echo(f"adb server: {resolved.described}")
    typer.echo(f"device: {channel.target.described}")
    note = ""
    if channel.moved_from_device_port:
        note = f"  ({channel.device_port} is in use here; --local picks another)"
    typer.echo(f"listening on {channel.bind}:{port} -> device tcp:{channel.device_port}{note}")
    typer.echo(f"  e.g. gdb: target remote {channel.bind}:{port}   (Ctrl-C to stop)")
    try:
        await _serve(channel)
    finally:
        await channel.close()


@app.command("open")
def cmd_open(
    device_port: int = typer.Argument(..., help="TCP port on the device (its localhost)"),
    local: int | None = typer.Option(
        None, "--local", help="local port to listen on (default: the device port if free)"
    ),
    serial: str | None = typer.Option(
        None, "--serial", help="device serial; required when more than one device is attached"
    ),
    bind: str = typer.Option("127.0.0.1", "--bind", help="address to listen on"),
    allow_remote: bool = typer.Option(
        False, "--allow-remote", help="permit a non-loopback --bind (exposes the device port)"
    ),
) -> None:
    """Listen locally and bridge every connection to a port on the device.

    Each accepted connection gets its own stream through the adb server, so
    gdb, curl and friends see exactly one device-side socket per connection.
    Runs until Ctrl-C; prints one line per connection open / close.

    adb has no half-close: a client that shuts its write side still gets the
    device's reply, but the device never sees that EOF. After it, the
    connection ends when the device closes or has been silent for 2 s — so a
    reply that starts later than that is cut, and a closed client's line
    appears up to 2 s late.
    """
    _check_port(device_port, "device_port")
    if local is not None:
        _check_port(local, "--local")
    _check_serial(serial)
    endpoint, resolved = _endpoint()
    channel = PortChannel(
        endpoint=endpoint,
        device_port=device_port,
        serial=serial,
        bind=bind,
        local_port=local,
        allow_remote=allow_remote,
        on_event=typer.echo,
    )
    try:
        asyncio.run(_listen(channel, resolved))
    except KeyboardInterrupt:
        pass
    except (AdbSocketError, PortChannelError) as e:
        _fail_for(e)
    typer.echo(
        f"stopped · {channel.connections} connection(s), {channel.refused} refused · "
        f"{channel.bytes_to_device} B to device · {channel.bytes_from_device} B from device"
    )


# ── check ──────────────────────────────────────────────────────────────


async def _probe(endpoint: TcpEndpoint, device_port: int, serial: str | None) -> str:
    """Open the stream once and close it. Returns the device's description."""
    target = await resolve_target(endpoint, serial)
    stream = await open_device_stream(endpoint, target, device_port)
    await stream.close()
    return target.described


@app.command("check")
def cmd_check(
    device_port: int = typer.Argument(..., help="TCP port on the device (its localhost)"),
    serial: str | None = typer.Option(
        None, "--serial", help="device serial; required when more than one device is attached"
    ),
) -> None:
    """One shot: does the device accept a connection to that port?

    Exit 0 when it does, 1 when the device refuses (nothing listening there),
    2 when the question could not be asked (no adb server, ambiguous device).

    The probe really connects and hangs up. A server that takes a single
    client and then exits (`gdbserver :PORT --attach PID` does) is used up by
    it — skip the check for those and go straight to `alb port open`.
    """
    _check_port(device_port, "device_port")
    _check_serial(serial)
    endpoint, resolved = _endpoint()
    typer.echo(f"adb server: {resolved.described}")
    try:
        described = asyncio.run(_probe(endpoint, device_port, serial))
    except AdbRefused as e:
        _fail(
            CODE_DEVICE_REFUSED,
            f"device refused tcp:{device_port}: {e.message or '(no message)'}",
            f"start the server on the device first, e.g. `gdbserver :{device_port} --attach <pid>`",
            exit_code=_EXIT_REFUSED,
        )
    except AdbSocketError as e:
        _fail_for(e)
    typer.echo(f"[ok] device {described} accepted tcp:{device_port}")
