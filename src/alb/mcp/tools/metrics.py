"""MCP tool: alb_metrics_snapshot."""

from __future__ import annotations

from typing import Any

from alb.capabilities.metrics import MetricSampler
from alb.mcp.transport_factory import build_transport


def register(mcp) -> None:  # noqa: ANN001
    @mcp.tool()
    async def alb_metrics_snapshot(device: str | None = None) -> dict[str, Any]:
        """One snapshot of CPU / memory / temp / I/O / GPU / battery.

        Note: cpu_pct_total / net_*_per_s / disk_*_per_s require TWO
        samples to compute (they're deltas). A single snapshot returns
        zeros for those fields. For continuous data, prefer the
        WebSocket /metrics/stream — it shares one sampling loop across
        all subscribers.

        Args:
            device: device serial (optional).
        """
        t = build_transport(device_serial=device)
        sampler = MetricSampler(t)
        r = await sampler.sample()
        return r.to_dict()
