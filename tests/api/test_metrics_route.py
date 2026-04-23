"""Tests for the /metrics/stream WebSocket route.

Uses FastAPI's TestClient WS support and a fake transport that returns
deterministic /proc-style stdout, so the test runs without a device.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from alb.api.server import create_app
from alb.capabilities.metrics import shutdown_all_streamers
from alb.transport.base import ShellResult


_SAMPLE_STDOUT = (
    "__ALB_STAT__\ncpu  100 50 30 600 10 0 5\n"
    "__ALB_MEM__\nMemTotal: 8000000 kB\nMemAvailable: 3000000 kB\n"
    "SwapTotal: 0 kB\nSwapFree: 0 kB\n"
    "__ALB_NET__\n"
    "Inter-| junk\n face | junk\n"
    "  eth0: 5000 1 0 0 0 0 0 0 3000 1 0 0 0 0 0 0\n"
    "__ALB_FREQ__\n1800000\n2200000\n"
    "__ALB_THERM__\n/sys/class/thermal/thermal_zone0:\ncpu-big\n55000\n"
    "__ALB_GPU__\n/sys/class/devfreq/x.gpu:\nmali\n800000000\n"
    "__ALB_GPUUTIL__\n29\n"
    "__ALB_DISK__\n100 0 200 50 80 0 160 30 0 5 5\n"
    "__ALB_BAT__\n  present: false\n  temperature: 0\n"
)


class _FakeTransport:
    name = "adb"

    async def shell(self, cmd: str, timeout: int = 30) -> ShellResult:
        # Tiny await so the asyncio loop interleaves with the test
        await asyncio.sleep(0)
        return ShellResult(
            ok=True, exit_code=0, stdout=_SAMPLE_STDOUT, duration_ms=5,
        )


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    # Replace transport_factory's build_transport so the WS route gets
    # our fake instead of trying to talk to a real device.
    monkeypatch.setattr(
        "alb.api.metrics_route.build_transport",
        lambda **kwargs: _FakeTransport(),
    )
    app = create_app()
    with TestClient(app) as c:
        yield c
    # Best-effort cleanup; ok if nothing to stop.
    try:
        asyncio.get_event_loop().run_until_complete(shutdown_all_streamers())
    except Exception:
        pass


def test_metrics_ws_handshake_history_then_sample(client) -> None:
    with client.websocket_connect("/metrics/stream") as ws:
        ws.send_json({"device": None, "history_seconds": 5})
        first = ws.receive_json()
        assert first["type"] == "history"
        assert "samples" in first
        assert "interval_s" in first

        # Now wait for at least one live sample. Cap the wait so the
        # test can never hang.
        sample_msg: dict[str, Any] | None = None
        for _ in range(80):  # ~8s ceiling
            msg = ws.receive_json()
            if msg.get("type") == "sample":
                sample_msg = msg
                break
        assert sample_msg is not None
        d = sample_msg["data"]
        assert d["mem_total_kb"] == 8000000
        assert "cpu_pct_total" in d


def test_metrics_ws_control_pause_resume(client) -> None:
    with client.websocket_connect("/metrics/stream") as ws:
        ws.send_json({})
        ws.receive_json()  # history
        ws.send_json({"type": "control", "action": "pause"})
        ack = None
        # Drain any in-flight samples until we see the ack
        for _ in range(50):
            m = ws.receive_json()
            if m.get("type") == "control_ack":
                ack = m
                break
        assert ack is not None
        assert ack["action"] == "pause"
        assert ack["paused"] is True


def test_metrics_ws_set_interval(client) -> None:
    with client.websocket_connect("/metrics/stream") as ws:
        ws.send_json({})
        ws.receive_json()  # history
        ws.send_json({
            "type": "control",
            "action": "set_interval",
            "value_s": 0.25,
        })
        for _ in range(50):
            m = ws.receive_json()
            if m.get("type") == "control_ack" and m.get("action") == "set_interval":
                assert m["interval_s"] == 0.25
                return
        raise AssertionError("never received set_interval ack")
