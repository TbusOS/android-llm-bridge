"""Deprecated shim — the transport factory moved to :mod:`alb.transport.factory`.

Kept so existing ``alb.mcp.transport_factory`` imports keep working;
new code should import from ``alb.transport.factory`` directly.
"""

from __future__ import annotations

from alb.transport.factory import active_settings, build_transport

__all__ = ["active_settings", "build_transport"]
