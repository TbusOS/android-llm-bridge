"""Shared transport factory used by both CLI and MCP layers.

Keeps the get_transport() logic in one place so CLI and MCP stay aligned.
"""

from __future__ import annotations

import os

from alb.infra.config import ActiveSettings, load_active
from alb.transport.adb import AdbTransport
from alb.transport.base import Transport


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
        raise NotImplementedError("serial transport not yet implemented (M1 WIP)")
    raise ValueError(f"Unknown transport: {which}")
