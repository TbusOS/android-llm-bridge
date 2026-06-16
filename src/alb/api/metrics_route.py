"""Web API: /metrics/stream WebSocket — live device telemetry.

Protocol:

    C → S (first message, optional):
        {"device": "<serial>", "history_seconds": 60}
    S → C (one shot):
        {"type": "history", "interval_s": 1.0, "samples": [MetricSample, ...]}
    S → C (live, every interval_s):
        {"type": "sample", "data": MetricSample}
    C → S (any time):
        {"type": "control", "action": "pause"}
        {"type": "control", "action": "resume"}
        {"type": "control", "action": "set_interval", "value_s": 0.5}
    S → C (after a control action):
        {"type": "control_ack", "action": "...", "interval_s": 1.0,
         "paused": false}

The streamer is shared across all clients of a given device, so opening
N WS clients does NOT multiply the shell load.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from alb.api.schema import API_VERSION
from alb.capabilities.metrics import (
    MetricSample,
    get_streamer,
)
from alb.infra.workspace import is_safe_device
from alb.transport.factory import build_transport

router = APIRouter()


class _ConnState:
    """Per-connection metrics control (functional audit MID-CODE-3).

    pause / set_interval are scoped to THIS websocket so one client can't
    blank or re-pace another client sharing the device's streamer. The
    shared sampler runs at the fastest (min) subscriber request; this
    object then downsamples that feed to the connection's own desired
    interval (and drops everything while paused).
    """

    def __init__(self, interval_s: float) -> None:
        # This connection's own desired interval (already clamped by the
        # streamer to [0.1, 60]).
        self.interval_s = interval_s
        self.paused = False
        self._since_emit = 0

    def should_forward(self, effective_interval_s: float) -> bool:
        if self.paused:
            return False
        base = effective_interval_s if effective_interval_s > 0 else self.interval_s
        # The shared feed is at `base` (≤ our desired); forward one in every
        # `stride` shared ticks to land on our own (slower-or-equal) rate.
        stride = max(1, round(self.interval_s / base))
        self._since_emit += 1
        if self._since_emit >= stride:
            self._since_emit = 0
            return True
        return False


@router.websocket("/metrics/stream")
async def metrics_stream(ws: WebSocket) -> None:
    await ws.accept()

    # Optional first message for device + replay window. Tolerate clients
    # that just send nothing (default device, default 60s history).
    device: str | None = None
    history_seconds = 60
    try:
        first = await asyncio.wait_for(ws.receive_json(), timeout=1.5)
        if isinstance(first, dict):
            device = first.get("device") or None
            try:
                history_seconds = max(0, int(first.get("history_seconds", 60)))
            except (TypeError, ValueError):
                history_seconds = 60
    except (asyncio.TimeoutError, WebSocketDisconnect):
        first = None

    # SEC-2: reject a malformed serial with a clean closed frame before it
    # reaches build_transport (matches the REST routes' is_safe_device gate).
    if device is not None and not is_safe_device(device):
        with contextlib.suppress(Exception):
            await ws.send_json({
                "type": "closed",
                "reason": "bad_device",
                "error": "invalid device serial",
            })
        with contextlib.suppress(Exception):
            await ws.close()
        return

    try:
        transport = build_transport(device_serial=device)
    except Exception as e:  # noqa: BLE001 — surface init failure then close
        with contextlib.suppress(Exception):
            await ws.send_json({
                "type": "closed",
                "reason": "init_failed",
                "error": f"{type(e).__name__}: {e}",
            })
        with contextlib.suppress(Exception):
            await ws.close()
        return

    streamer = get_streamer(transport, device_key=device or "default")

    # CODE-1 race fix: register this subscriber's queue BEFORE start(), so a
    # concurrent last-subscriber teardown (subscribe() finally → stop()) can
    # never stop the sampling loop we're about to (re)start out from under us.
    async with streamer.subscribe() as queue:
        await streamer.start()

        history_n = max(0, int(history_seconds / streamer.interval_s))
        history = streamer.history(history_n)
        await ws.send_json({
            "v": API_VERSION,
            "type": "history",
            "interval_s": streamer.interval_s,
            "samples": [s.to_dict() for s in history],
        })

        conn = _ConnState(streamer.base_interval_s)
        recv_task = asyncio.create_task(_recv_loop(ws, streamer, queue, conn))
        send_task = asyncio.create_task(_send_loop(ws, queue, conn, streamer))
        try:
            done, pending = await asyncio.wait(
                {recv_task, send_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await t
            # functional audit HIGH 9: if a task raised (not normal disconnect),
            # surface it as a `closed` JSON frame so the frontend can show a
            # reconnect prompt instead of falling silently to `ended`.
            for t in done:
                exc = t.exception() if not t.cancelled() else None
                if exc and not isinstance(exc, WebSocketDisconnect):
                    with contextlib.suppress(Exception):
                        await ws.send_json({
                            "type": "closed",
                            "reason": "server_error",
                            "error": f"{type(exc).__name__}: {exc}",
                        })
                    break
        finally:
            with contextlib.suppress(Exception):
                await ws.close()


async def _recv_loop(
    ws: WebSocket,
    streamer: Any,
    queue: asyncio.Queue[MetricSample],
    conn: _ConnState,
) -> None:
    try:
        while True:
            msg = await ws.receive_json()
            if not isinstance(msg, dict):
                continue
            if msg.get("type") != "control":
                continue
            action = msg.get("action")
            # CODE-3: control is per-connection — pause gates THIS conn's
            # send loop; set_interval updates THIS subscriber's desired rate
            # (streamer re-derives the shared rate from the fastest request),
            # so a control frame from one client never re-paces another.
            if action == "pause":
                conn.paused = True
            elif action == "resume":
                conn.paused = False
            requested: float | None = None
            if action == "set_interval":
                try:
                    requested = float(msg.get("value_s", 1.0))
                except (TypeError, ValueError):
                    requested = None
                if requested is not None and requested == requested:  # not NaN
                    clamped = streamer.set_subscriber_interval(queue, requested)
                    if clamped is not None:
                        conn.interval_s = clamped
            elif action not in ("pause", "resume"):
                continue
            ack: dict[str, Any] = {
                "type": "control_ack",
                "action": action,
                "interval_s": conn.interval_s,
                "paused": conn.paused,
            }
            # MID-7: surface clamp so the UI can warn users that pathological
            # interval values (negative, NaN, 1e9) were silently corrected.
            if (
                action == "set_interval"
                and requested is not None
                and requested == requested  # not NaN
                and abs(requested - conn.interval_s) > 1e-9
            ):
                ack["clamped"] = True
                ack["requested_s"] = requested
            await ws.send_json(ack)
    except WebSocketDisconnect:
        return
    except Exception:
        return


async def _send_loop(
    ws: WebSocket,
    queue: asyncio.Queue[MetricSample],
    conn: _ConnState,
    streamer: Any,
) -> None:
    # Outer try lets the exception propagate to the wait() above —
    # outer wrapper turns it into a `closed reason=server_error` frame
    # (functional audit HIGH 9). Don't swallow generic Exception here;
    # that's how the original silent-`ended` bug existed.
    while True:
        try:
            sample = await queue.get()
            # CODE-3: drop while this connection is paused / between its own
            # throttled ticks; the shared sampler keeps feeding other clients.
            if not conn.should_forward(streamer.interval_s):
                continue
            await ws.send_json({"type": "sample", "data": sample.to_dict()})
        except WebSocketDisconnect:
            return


def _payload_default() -> dict[str, Any]:
    """Helper for tests that need a control message body."""
    return {"type": "control", "action": "pause"}
