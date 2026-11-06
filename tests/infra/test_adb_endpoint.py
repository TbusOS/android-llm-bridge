"""adb endpoint resolution + conflict detection (ADR-057).

The bug being fenced in: an adb client pointed at the wrong server does not
fail, it reports zero devices. So the tests that matter are not "does it
resolve" but "does it stay quiet when it should" and "does it speak up in the
one case that produced a lost day on 2026-08-10".
"""

from __future__ import annotations

from typing import Any

import pytest

from alb.infra import adb_endpoint as ae


@pytest.fixture(autouse=True)
def _no_ambient_state(monkeypatch):
    """Every test states its own world. Without this the developer's own
    ADB_SERVER_SOCKET (the very thing that caused the incident) leaks in and
    the suite passes or fails by machine."""
    monkeypatch.delenv("ADB_SERVER_SOCKET", raising=False)
    monkeypatch.delenv("ALB_API_URL", raising=False)
    monkeypatch.delenv("ALB_API_HOST", raising=False)
    monkeypatch.delenv("ALB_API_PORT", raising=False)
    monkeypatch.setattr(ae, "_IN_HUB", False)
    ae.reset_hub_cache()
    yield
    ae.reset_hub_cache()


def _hub(port=15037, bound=True, devices=("1000000001",)):
    return ae.HubAdbView(port=port, bound=bound, devices=tuple(devices))


def _no_hub(monkeypatch):
    monkeypatch.setattr(ae, "hub_adb_view", lambda **_: None)


def _with_hub(monkeypatch, view):
    monkeypatch.setattr(ae, "hub_adb_view", lambda **_: view)


# ── precedence ─────────────────────────────────────────────────────


def test_config_wins_over_everything(monkeypatch):
    monkeypatch.setenv("ADB_SERVER_SOCKET", "tcp:localhost:5037")
    _with_hub(monkeypatch, _hub())
    ep = ae.resolve_endpoint("tcp:127.0.0.1:9999")
    assert (ep.socket, ep.source) == ("tcp:127.0.0.1:9999", "config")


def test_env_wins_over_hub_discovery(monkeypatch):
    """Deliberate, and the most likely thing a future reader will want to
    'fix': ADB_SERVER_SOCKET steers every adb client in the shell, so alb
    silently overriding it would make `alb devices` and `adb devices` disagree
    in the same terminal. Louder diagnosis, not a quiet override."""
    monkeypatch.setenv("ADB_SERVER_SOCKET", "tcp:localhost:5037")
    _with_hub(monkeypatch, _hub())
    ep = ae.resolve_endpoint(None)
    assert (ep.socket, ep.source) == ("tcp:localhost:5037", "env")


def test_hub_is_used_when_nothing_is_configured(monkeypatch):
    _with_hub(monkeypatch, _hub(port=15037))
    ep = ae.resolve_endpoint(None)
    assert (ep.socket, ep.source) == ("tcp:127.0.0.1:15037", "hub")


def test_falls_back_to_adb_default_with_no_hub(monkeypatch):
    _no_hub(monkeypatch)
    ep = ae.resolve_endpoint(None)
    assert (ep.socket, ep.source) == (None, "default")


def test_unbound_forwarder_is_not_an_endpoint(monkeypatch):
    """A hub that is up but whose forwarder never bound has no port to offer;
    inventing one would point every command at a closed socket."""
    _with_hub(monkeypatch, _hub(bound=False))
    assert ae.resolve_endpoint(None).source == "default"


def test_allow_hub_false_never_probes(monkeypatch):
    def explode(**_):
        raise AssertionError("must not probe the hub")

    monkeypatch.setattr(ae, "hub_adb_view", explode)
    assert ae.resolve_endpoint(None, allow_hub=False).source == "default"


# ── conflict detection: silence is the default ─────────────────────


def test_no_conflict_when_we_are_already_on_the_forwarder(monkeypatch):
    _with_hub(monkeypatch, _hub())
    assert ae.endpoint_conflict(ae.AdbEndpoint("tcp:127.0.0.1:15037", "hub")) == ""


def test_no_conflict_when_ports_agree(monkeypatch):
    _with_hub(monkeypatch, _hub(port=5037))
    assert ae.endpoint_conflict(ae.AdbEndpoint("tcp:localhost:5037", "env")) == ""


def test_no_conflict_without_a_hub(monkeypatch):
    _no_hub(monkeypatch)
    assert ae.endpoint_conflict(ae.AdbEndpoint("tcp:localhost:5037", "env")) == ""


def test_no_conflict_when_the_bench_has_no_devices(monkeypatch):
    """Two adb servers on one machine is a normal setup. It only becomes a
    problem when the one we are NOT using is holding the board — otherwise
    this fires on every developer laptop and gets tuned out."""
    _with_hub(monkeypatch, _hub(devices=()))
    assert ae.endpoint_conflict(ae.AdbEndpoint("tcp:localhost:5037", "env")) == ""


def test_unix_socket_is_not_comparable(monkeypatch):
    """No port to compare. Reporting a mismatch here would be a fabricated
    conflict, which costs more trust than the missed hint saves."""
    _with_hub(monkeypatch, _hub())
    assert ae.endpoint_conflict(ae.AdbEndpoint("unix:/tmp/adb.sock", "env")) == ""


# ── conflict detection: the case worth interrupting for ────────────


def test_the_2026_08_10_case(monkeypatch):
    """Stale ADB_SERVER_SOCKET on 5037, forwarder on 15037, board on the
    bench. The message must name both ports, the source, and the fix —
    'check your tunnel' is what the old code said and it was not enough."""
    _with_hub(monkeypatch, _hub(port=15037, devices=("1000000001",)))
    msg = ae.endpoint_conflict(ae.AdbEndpoint("tcp:localhost:5037", "env"))
    assert "5037" in msg and "15037" in msg
    assert "ADB_SERVER_SOCKET" in msg
    assert "1000000001" in msg
    assert "tcp:127.0.0.1:15037" in msg


def test_default_source_is_compared_against_5037(monkeypatch):
    """Nothing set at all still lands on adb's 5037, so it can conflict too."""
    _with_hub(monkeypatch, _hub(port=15037))
    msg = ae.endpoint_conflict(ae.AdbEndpoint(None, "default"))
    assert "15037" in msg and "5037" in msg


def test_config_source_is_named_in_the_message(monkeypatch):
    _with_hub(monkeypatch, _hub(port=15037))
    msg = ae.endpoint_conflict(ae.AdbEndpoint("tcp:127.0.0.1:5037", "config"))
    assert "config.toml" in msg


# ── spec parsing ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("spec", "port"),
    [
        ("tcp:5037", 5037),
        ("tcp:localhost:5037", 5037),
        ("tcp:127.0.0.1:15037", 15037),
        ("unix:/tmp/x", None),
        ("", None),
        (None, None),
        ("tcp:", None),
        ("tcp:localhost:notanumber", None),
    ],
)
def test_socket_port_parsing(spec, port):
    assert ae._socket_port(spec) == port


# ── the hub probe must never become a liability ────────────────────


def test_remote_hub_is_not_probed_for_a_local_port(monkeypatch):
    """A forwarder binds 127.0.0.1 on the HUB's machine. Borrowing that port
    number here would aim every command at whatever happens to be listening
    locally — a wrong answer that looks like a right one."""
    monkeypatch.setenv("ALB_API_URL", "http://10.1.2.3:8765")
    monkeypatch.setattr(ae, "_in_process_view", lambda: None)

    def explode(*_a, **_k):
        raise AssertionError("must not issue HTTP to a remote hub")

    monkeypatch.setattr("httpx.get", explode)
    assert ae.hub_adb_view() is None


def test_probe_failure_is_not_an_error(monkeypatch):
    """Discovery runs on the way to ordinary commands. A hub that is down,
    wedged, or returning garbage must cost a missed hint and nothing else."""
    monkeypatch.setattr(ae, "_in_process_view", lambda: None)

    def boom(*_a, **_k):
        raise OSError("connection refused")

    monkeypatch.setattr("httpx.get", boom)
    assert ae.hub_adb_view() is None


def _record_http(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def get(url: object, *_a: object, **_k: object) -> object:
        calls.append(str(url))
        raise OSError("no HTTP in this test")

    monkeypatch.setattr("httpx.get", get)
    return calls


class _Status:
    """A /agent/status answer whose adb forwarder is bound or not."""

    status_code = 200

    def __init__(self, bound: bool) -> None:
        self._bound = bound

    def json(self) -> dict[str, Any]:
        return {"forwarders": {"adb": {"bound": self._bound, "port": 15037}}, "agents": []}


def _answers(monkeypatch: pytest.MonkeyPatch, *answers: object) -> list[int]:
    """httpx.get returns these in turn (an exception instance is raised)."""
    calls: list[int] = []
    left = list(answers)

    def get(*_a: object, **_k: object) -> object:
        calls.append(1)
        a = left.pop(0) if len(left) > 1 else left[0]
        if isinstance(a, Exception):
            raise a
        return a

    monkeypatch.setattr(ae, "_in_process_view", lambda: None)
    monkeypatch.setattr("httpx.get", get)
    return calls


def test_a_bound_forwarder_is_memoised(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _answers(monkeypatch, _Status(bound=True))
    ae.hub_adb_view()
    ae.hub_adb_view()
    ae.hub_adb_view()
    assert len(calls) == 1, "one probe per process once a forwarder is found"
    ae.hub_adb_view(refresh=True)
    assert len(calls) == 2, "refresh must actually re-probe"


def test_no_hub_is_not_memoised(monkeypatch: pytest.MonkeyPatch) -> None:
    """A long-lived process (an MCP server) that probed while the hub was
    restarting must find it once it is back, not stay on adb's default server
    — zero devices and no error — until it is restarted itself (2026-11-06)."""
    _answers(monkeypatch, OSError("connection refused"), _Status(bound=True))
    assert ae.resolve_endpoint().source == "default"
    assert ae.resolve_endpoint().socket == "tcp:127.0.0.1:15037"


def test_an_unbound_forwarder_is_not_memoised(monkeypatch: pytest.MonkeyPatch) -> None:
    """The forwarder binds when the bench agent connects, seconds after the
    hub starts; a probe in between sees it unbound."""
    _answers(monkeypatch, _Status(bound=False), _Status(bound=True))
    assert ae.resolve_endpoint().source == "default"
    assert ae.resolve_endpoint().socket == "tcp:127.0.0.1:15037"


def test_inside_the_hub_the_forwarder_is_read_every_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """No memo and no HTTP to itself: the forwarder singleton is the answer."""
    import alb.remote.forwarder as fw

    adb = {"bound": False, "port": 15037}
    monkeypatch.setattr(fw, "forwarder_status", lambda: {"adb": dict(adb)})
    # Recorded, not raised: the probe swallows exceptions by design.
    http = _record_http(monkeypatch)
    ae.mark_hub_process(True)
    try:
        assert ae.resolve_endpoint().source == "default"
        adb["bound"] = True
        assert ae.resolve_endpoint().socket == "tcp:127.0.0.1:15037"
    finally:
        ae.mark_hub_process(False)
    assert http == [], "the hub must not issue HTTP to itself"


def test_a_devices_request_before_the_agent_connects_does_not_stick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 2026-11-06 15:39 restart: a page on the bench host asked the new hub
    for /devices before the bench agent was back, and the hub showed zero
    devices until it was restarted again. alb-api marks itself as the hub at
    startup, so the forwarder that binds later is found."""
    from fastapi.testclient import TestClient

    import alb.remote.forwarder as fw
    from alb.api.server import create_app

    monkeypatch.setenv("ALB_ADB_FORWARD_PORT", "0")
    adb = {"bound": False, "port": 15037}
    monkeypatch.setattr(fw, "forwarder_status", lambda: {"adb": dict(adb)})
    # Also keeps this test off a real hub that may be listening on 8765.
    http = _record_http(monkeypatch)
    with TestClient(create_app()) as c:
        assert ae._IN_HUB is True, "startup marks the hub"
        c.get("/devices")  # the early request
        assert ae.resolve_endpoint().source == "default"
        adb["bound"] = True  # the agent connected
        assert ae.resolve_endpoint().socket == "tcp:127.0.0.1:15037"
    assert ae._IN_HUB is False, "shutdown clears the mark"
    assert http == [], "the hub must not issue HTTP to itself"


def test_status_payload_is_parsed_from_the_real_shape():
    """Pinned to the exact JSON /agent/status serves — the parse is the seam
    where a rename on the hub side would silently disable discovery."""
    view = ae._view_from_status(
        {
            "agents": [
                {"agent_id": "other", "adb_devices": ["ignored"], "current": False},
                {"agent_id": "win-x", "adb_devices": ["1000000001"], "current": True},
            ],
            "forwarders": {
                "adb": {"bound": True, "port": 15037},
                "serial": {"bound": True, "port": 9001},
            },
        }
    )
    assert view.port == 15037
    assert view.bound is True
    assert view.devices == ("1000000001",)
    assert view.socket == "tcp:127.0.0.1:15037"
