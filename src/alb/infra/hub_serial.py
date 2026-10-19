"""Why the local hub's serial channel last failed to open, if it did.

## The gap this closes

On the hub path the board's UART is on another machine. When the agent there
cannot open the COM port, the hub's serial forwarder has nothing to put on
the local socket except a hang-up — a raw byte stream has no room for a
reason. So a local client (``alb serial shell`` / ``capture``, a timeline)
sees "the endpoint closed without a byte" and cannot say why, while the
agent DID say why: its ``channel_error`` text, e.g. Windows' access-denied
for a port that another program holds. The hub keeps the latest one in
``/agent/status`` (``forwarders.serial.last_open_error``); this asks for it.

Bench, 2026-10-19: COM27 held by another program on the bench host; every
serial command said only SERIAL_LINK_REFUSED, and ``alb serial capture``
reported 0 lines and success.

## Only when it is that hub's port

The answer is used only when the hub on THIS machine owns the endpoint the
client used (same loopback port as its serial forwarder) and the failure is
recent. Another bridge on another port, or a failure from long ago, must not
be blamed for this hang-up.
"""

from __future__ import annotations

from typing import Any

from alb.infra.adb_endpoint import _HUB_TIMEOUT_S, _LOCAL_HOSTS, _hub_base_url, _hub_is_local


def _serial_view() -> dict[str, Any] | None:
    """The hub's serial forwarder snapshot: read in-process when this IS the
    hub (an HTTP request to ourselves from the event loop could deadlock —
    see adb_endpoint._in_process_view), else from a hub on this machine."""
    try:
        from alb.remote import forwarder

        if forwarder._SERIAL_FORWARDER is not None:
            return dict(forwarder.forwarder_status().get("serial") or {})
    except Exception:  # pragma: no cover - alb-api extras absent
        pass
    url = _hub_base_url()
    if not _hub_is_local(url):
        return None
    try:
        import httpx

        r = httpx.get(f"{url}/agent/status", timeout=_HUB_TIMEOUT_S)
        if r.status_code != 200:
            return None
        return dict((r.json().get("forwarders") or {}).get("serial") or {})
    except Exception:  # no hub here is the normal case, not an error
        return None


def hub_serial_open_failure(host: str | None, port: int | None, *, within_s: float) -> str | None:
    """The agent's reason for the last failed COM open, or None.

    None unless ``host:port`` is the local hub's serial forwarder and the
    failure is at most ``within_s`` seconds old. Blocks for at most the hub
    timeout; call it from a thread in async code. Never raises."""
    if not host or port is None or host not in _LOCAL_HOSTS:
        return None
    view = _serial_view()
    if not view or view.get("port") != port:
        return None
    err = view.get("last_open_error")
    if not isinstance(err, dict):
        return None
    reason, age = err.get("reason"), err.get("age_s")
    if not isinstance(reason, str) or not reason or not isinstance(age, int | float):
        return None
    return reason if age <= within_s else None
