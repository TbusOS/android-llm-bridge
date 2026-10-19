"""hub_serial_open_failure: the bench agent's reason, and only when it applies.

A wrong attribution is worse than none: a reason from another bridge, or
from an hour ago, would send someone to fix a problem that is not this one.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from alb.infra import hub_serial

REASON = (
    "cannot open COM27 @ 1500000: could not open port 'COM27': "
    "PermissionError(13, '拒绝访问。', None, 5)"
)


@pytest.fixture
def view(monkeypatch) -> dict[str, Any]:
    v: dict[str, Any] = {"port": 9001, "last_open_error": {"reason": REASON, "age_s": 1.2}}
    monkeypatch.setattr(hub_serial, "_serial_view", lambda: v)
    return v


def test_the_reason_for_this_hubs_port_is_returned(view):
    assert hub_serial.hub_serial_open_failure("127.0.0.1", 9001, within_s=10) == REASON


@pytest.mark.parametrize(
    "host, port",
    [("127.0.0.1", 9002), ("192.0.2.10", 9001), (None, 9001), ("127.0.0.1", None)],
)
def test_another_endpoint_gets_nothing(view, host, port):
    """A different local port is another bridge; a remote host is not where
    this hub's forwarder listens."""
    assert hub_serial.hub_serial_open_failure(host, port, within_s=10) is None


def test_an_old_failure_is_not_this_one(view):
    view["last_open_error"]["age_s"] = 30.0
    assert hub_serial.hub_serial_open_failure("127.0.0.1", 9001, within_s=10) is None


@pytest.mark.parametrize(
    "err",
    [None, "text", {"reason": ""}, {"reason": REASON}, {"age_s": 1}, {"reason": 3, "age_s": 1}],
)
def test_a_missing_or_malformed_record_is_nothing(view, err):
    view["last_open_error"] = err
    assert hub_serial.hub_serial_open_failure("127.0.0.1", 9001, within_s=10) is None


def test_no_hub_here_is_nothing(monkeypatch):
    monkeypatch.setattr(hub_serial, "_serial_view", lambda: None)
    assert hub_serial.hub_serial_open_failure("127.0.0.1", 9001, within_s=10) is None


def test_inside_the_hub_the_forwarder_is_read_without_a_request(monkeypatch):
    """In alb-api itself an HTTP GET to its own /agent/status could deadlock
    the event loop serving it; the singleton is the same fact."""
    from alb.remote import forwarder

    class _Fwd:
        _server = None  # forwarder.reset_forwarders() reads it on teardown
        is_bound = True
        port = 9001
        session_refs = 0
        last_open_error: ClassVar[dict[str, Any]] = {"reason": REASON, "age_s": 0.5}

    monkeypatch.setattr(forwarder, "_SERIAL_FORWARDER", _Fwd())

    def _no_http(*_a, **_kw):
        raise AssertionError("must not request itself")

    monkeypatch.setattr("httpx.get", _no_http)
    assert hub_serial.hub_serial_open_failure("127.0.0.1", 9001, within_s=10) == REASON
