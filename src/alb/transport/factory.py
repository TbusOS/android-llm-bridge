"""Shared transport factory used by the CLI, MCP, and API layers.

Keeps the get_transport() logic in one place so all entry points stay
aligned. Lives in the transport package so upper layers (api / cli /
mcp) depend on `alb.transport`, not on each other.
"""

from __future__ import annotations

import os

from alb.infra.adb_endpoint import resolve_endpoint
from alb.infra.config import ActiveSettings, load_active
from alb.infra.workspace import InvalidDeviceSerial, is_safe_device
from alb.transport.adb import AdbTransport
from alb.transport.base import Transport
from alb.transport.serial import SerialTransport
from alb.transport.ssh import SshTransport

_cached_settings: ActiveSettings | None = None


def active_settings(force_reload: bool = False) -> ActiveSettings:
    global _cached_settings
    if _cached_settings is None or force_reload:
        _cached_settings = load_active()
    return _cached_settings


def build_transport(
    *,
    override: str | None = None,
    device_serial: str | None = None,
) -> Transport:
    """Build a transport using current settings + optional overrides.

    Precedence: explicit `override` > ALB_TRANSPORT env > profile.primary_transport.

    A non-empty ``device_serial`` is gated through ``is_safe_device`` here
    so every entry point (api / cli / mcp) is protected at the root — a
    malformed serial never reaches a transport's argv. ``None`` (the
    env-default device) is always allowed.
    """
    if device_serial is not None and not is_safe_device(device_serial):
        raise InvalidDeviceSerial(f"unsafe device serial: {device_serial!r}")

    settings = active_settings()
    which = override or os.environ.get("ALB_TRANSPORT") or settings.primary_transport

    if which == "adb":
        # ADR-057: the endpoint is resolved here, not left to whatever the
        # shell happens to export. This is the single door every entry point
        # (cli / api / mcp) walks through, so it is the one place where "which
        # adb server" can be answered consistently.
        endpoint = resolve_endpoint(settings.config.adb.server_socket)
        return AdbTransport(
            serial=device_serial,
            bin_path=settings.config.adb.bin_path,
            server_socket=endpoint.socket,
            server_socket_source=endpoint.source,
        )
    if which == "ssh":
        sc = settings.config.ssh
        host = os.environ.get("ALB_SSH_HOST")
        if not host:
            # Try to find a device entry in the active profile.
            for d in settings.profile.devices:
                if d.transport == "ssh" and d.ssh_host:
                    host = d.ssh_host
                    break
        if not host:
            raise ValueError(
                "SSH transport needs a host. Set ALB_SSH_HOST or define a device "
                "with transport='ssh' in your profile (workspace/profiles/*.toml)."
            )
        port_env = os.environ.get("ALB_SSH_PORT")
        port = int(port_env) if port_env else sc.default_port
        user = os.environ.get("ALB_SSH_USER") or sc.default_user
        key = os.environ.get("ALB_SSH_KEY") or sc.key_path
        known_hosts = os.environ.get("ALB_SSH_KNOWN_HOSTS") or sc.known_hosts
        return SshTransport(
            host=host,
            port=port,
            user=user,
            key_path=key,
            known_hosts=known_hosts,
            connect_timeout=sc.connect_timeout,
        )
    if which == "serial":
        sc = settings.config.serial
        # Build the prompt-pattern set once and pass it to whichever
        # SerialTransport we end up constructing. Empty override dict →
        # falls back to built-in defaults.
        from alb.transport.serial_state import PatternSet

        patterns = (
            PatternSet.from_mapping(sc.prompts) if sc.prompts else PatternSet.default()
        )
        common = {
            "baud": sc.default_baud,
            "patterns": patterns,
            "handshake_timeout": sc.handshake_timeout,
        }

        # Environment overrides take precedence over config.
        env_dev = os.environ.get("ALB_SERIAL_DEVICE")
        env_tcp = os.environ.get("ALB_SERIAL_TCP")  # "host:port"
        if env_dev:
            return SerialTransport(device=env_dev, **common)
        if env_tcp and ":" in env_tcp:
            host, _, port = env_tcp.partition(":")
            return SerialTransport(
                tcp_host=host or sc.default_tcp_host,
                tcp_port=int(port),
                **common,
            )
        return SerialTransport(
            tcp_host=sc.default_tcp_host,
            tcp_port=sc.default_tcp_port,
            **common,
        )
    raise ValueError(f"Unknown transport: {which}")
