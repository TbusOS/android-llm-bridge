"""Token throughput sampler — periodic 1Hz aggregation onto the event bus.

Owns a per-session counter and a periodic flush task. Every interval it
publishes a `tps_sample` event onto the EventBroadcaster so the UI
LiveSessionCard can render a real spark and the KPI's LLM throughput
can compute a windowed mean.

Why a sampler instead of broadcasting every token event:
    Token events arrive at 50-200 Hz during streaming. Fan-out to every
    `/audit/stream` subscriber would saturate queues and force complex
    backpressure. ADR-019 (decisions.md) decided token events stay
    inside the chat WS only; aggregated `tps_sample` is the public
    metric stream (ADR-021 formalises this as the "metric kind" class).

Module placement: lives under `infra/` because event_bus is here too —
it is a periodic publisher to the bus, not part of the LLM agent
abstraction. Future terminal-rate / push-byte-rate samplers can fit
the same pattern.

Resource safety contract:
    - `start()` is idempotent
    - `close()` is idempotent and ALWAYS cancels the timer
    - `close()` flushes any unflushed tokens before stopping (only when
      already started; pre-start observe()s are dropped to keep the
      "started lifecycle" honest — see ADR-020 / review-feedback)
    - publish failure is swallowed and logged at WARNING — sampler
      must never break chat

Configuration:
    - default interval = 1.0 s, override via env `ALB_TPS_SAMPLE_INTERVAL_S`
    - per-observe count is capped at OBSERVE_MAX to prevent a malformed
      backend chunk from injecting spurious throughput
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from typing import TYPE_CHECKING

from alb.infra.event_bus import get_bus, make_event

if TYPE_CHECKING:
    from alb.infra.event_bus import EventBroadcaster


_log = logging.getLogger(__name__)

DEFAULT_INTERVAL_S = 1.0
OBSERVE_MAX = 10_000  # max tokens accepted in a single observe() call


def _interval_from_env() -> float:
    raw = os.environ.get("ALB_TPS_SAMPLE_INTERVAL_S")
    if not raw:
        return DEFAULT_INTERVAL_S
    try:
        v = float(raw)
        return v if v > 0 else DEFAULT_INTERVAL_S
    except ValueError:
        return DEFAULT_INTERVAL_S


class TokenSampler:
    """One sampler per chat session."""

    def __init__(
        self,
        *,
        session_id: str,
        bus: "EventBroadcaster | None" = None,
        interval_s: float | None = None,
    ) -> None:
        chosen = interval_s if interval_s is not None else _interval_from_env()
        if chosen <= 0:
            raise ValueError(f"interval_s must be > 0, got {chosen}")
        self._session_id = session_id
        self._bus = bus  # if None, resolved at flush time via get_bus()
        self._interval_s = chosen
        self._tokens_in_window = 0
        self._total_tokens = 0
        self._task: asyncio.Task[None] | None = None
        self._closed = False
        self._started = False

    @property
    def total_tokens(self) -> int:
        return self._total_tokens

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        """Launch the periodic flush task. Idempotent."""
        if self._closed or self.is_running:
            return
        self._started = True
        self._task = asyncio.create_task(self._loop())

    def observe(self, n: int = 1) -> None:
        """Record `n` tokens (sync, cheap).

        Pre-start observes are dropped — sampler must be started to
        accept input. This keeps the lifecycle contract simple: events
        only flow once start() has run.
        """
        if self._closed or n <= 0 or not self._started:
            return
        # Cap a single observe to prevent a runaway delta from poisoning
        # the throughput counter (defensive vs malformed backends).
        capped = min(n, OBSERVE_MAX)
        self._tokens_in_window += capped
        self._total_tokens += capped

    async def close(self) -> None:
        """Flush remaining tokens, cancel the timer, mark closed.

        Idempotent. If never started, a no-op (no flush, no events)."""
        if self._closed:
            return
        self._closed = True
        if not self._started:
            return

        # Final flush — emits even when window is 0 so consumers see
        # the "session went quiet" signal at end-of-stream.
        await self._flush(force=True)

        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        while not self._closed:
            await asyncio.sleep(self._interval_s)
            if self._closed:
                break
            await self._flush()

    async def _flush(self, *, force: bool = False) -> None:
        n = self._tokens_in_window
        self._tokens_in_window = 0
        # Skip writing zero-token samples during periodic flush — saves
        # disk + WS noise during quiet stretches. Final close() flushes
        # with force=True so the consumer sees the session ended.
        if n == 0 and not force:
            return
        rate = int(n / self._interval_s) if self._interval_s > 0 else n
        try:
            bus = self._bus or get_bus()
            await bus.publish(
                make_event(
                    session_id=self._session_id,
                    source="chat",
                    kind="tps_sample",
                    summary=f"{rate} tok/s",
                    data={
                        "tokens_window": n,
                        "window_s": self._interval_s,
                        "total_tokens": self._total_tokens,
                        "rate_per_s": rate,
                    },
                )
            )
        except Exception as e:  # noqa: BLE001 — bus is best-effort
            _log.warning("tps_sample publish failed: %s", e)
