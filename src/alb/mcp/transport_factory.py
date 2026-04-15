"""Shared transport factory used by both CLI and MCP layers.

Keeps the get_transport() logic in one place so CLI and MCP stay aligned.
"""

from __future__ import annotations

import os

from alb.infra.config import ActiveSettings, load_active
from alb.transport.adb import AdbTransport
from alb.transport.base import Transport
from alb.transport.serial import SerialTransport


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
    """
    settings = active_settings()
    which = override or os.environ.get("ALB_TRANSPORT") or settings.primary_transport

    if which == "adb":
        return AdbTransport(
            serial=device_serial,
            bin_path=settings.config.adb.bin_path,
            server_socket=settings.config.adb.server_socket,
        )
    if which == "ssh":
        raise NotImplementedError("ssh transport not yet implemented (M1 WIP)")
    if which == "serial":
        sc = settings.config.serial
        # device_serial overrides the tcp port for local / tcp picks.
        # Environment overrides: ALB_SERIAL_DEVICE / ALB_SERIAL_TCP
        env_dev = os.environ.get("ALB_SERIAL_DEVICE")
        env_tcp = os.environ.get("ALB_SERIAL_TCP")  # "host:port"
        if env_dev:
            return SerialTransport(device=env_dev, baud=sc.default_baud)
        if env_tcp and ":" in env_tcp:
            host, _, port = env_tcp.partition(":")
            return SerialTransport(
                tcp_host=host or sc.default_tcp_host,
                tcp_port=int(port),
                baud=sc.default_baud,
            )
        return SerialTransport(
            tcp_host=sc.default_tcp_host,
            tcp_port=sc.default_tcp_port,
            baud=sc.default_baud,
        )
    raise ValueError(f"Unknown transport: {which}")
