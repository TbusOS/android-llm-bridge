"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from alb.infra.event_bus import reset_bus
from alb.infra.metric_store import reset_metric_store


@pytest.fixture(autouse=True)
def _reset_event_infra():
    """Reset the event-bus + metric-store singletons around every test
    (ADR-049).

    Paired + ordered (store first, bus second) so the MetricStore never
    holds a reference to a stale bus, and a per-test `create_app()`
    lifespan can't leak listeners across tests (which would double-count
    every `tps_sample`). Supersedes the manual `reset_bus()` calls
    scattered across individual test files — those remain harmless."""
    reset_metric_store()
    reset_bus()
    yield
    reset_metric_store()
    reset_bus()


@pytest.fixture
def dummy_transport():
    """Placeholder for a mocked Transport. Real fixture lands in M1 tests."""
    return None
