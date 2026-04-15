"""MCP tools: alb_status, alb_devices, alb_describe, alb_describe_errors."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from alb import __version__
from alb.infra.errors import ERROR_CODES, lookup
from alb.infra.registry import CAPABILITIES, TRANSPORTS
from alb.mcp.transport_factory import build_transport


def register(mcp) -> None:  # noqa: ANN001
    @mcp.tool()
    async def alb_status() -> dict[str, Any]:
        """Snapshot of active transport, device(s), and server health.

        When to use:
            - First call in a new session to understand the environment
            - When any other tool returned TRANSPORT_* or DEVICE_* error

        Returns:
            Keys: transport, bin_found, server_reachable, devices, version, ok.
        """
        t = build_transport()
        return await t.health()

    @mcp.tool()
    async def alb_describe() -> dict[str, Any]:
        """Full schema dump: available transports, capabilities, versions.

        LLM: call this once per session to discover the tool surface.
        """
        return {
            "version": __version__,
            "transports": [asdict(t) for t in TRANSPORTS],
            "capabilities": [asdict(c) for c in CAPABILITIES],
        }

    @mcp.tool()
    async def alb_describe_errors(code: str | None = None) -> dict[str, Any]:
        """Look up error codes from the alb catalog.

        Args:
            code: optional exact code name (e.g. "DEVICE_OFFLINE").
                  If None, returns the full catalog.
        """
        if code:
            spec = lookup(code)
            if not spec:
                return {"ok": False, "reason": f"unknown code: {code}"}
            return {"ok": True, "code": spec.code, "category": spec.category,
                    "default_message": spec.default_message,
                    "default_suggestion": spec.default_suggestion}
        return {
            "ok": True,
            "codes": [asdict(s) for s in ERROR_CODES.values()],
        }

    @mcp.tool()
    async def alb_devices() -> dict[str, Any]:
        """List connected Android devices.

        Returns:
            { ok, data: { devices: [{serial, state, model, product, ...}] } }
        """
        t = build_transport()
        if not hasattr(t, "devices"):
            return {"ok": True, "data": {"devices": []}}
        devs = await t.devices()
        return {
            "ok": True,
            "data": {"devices": [asdict(d) for d in devs]},
        }
