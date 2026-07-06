"""Status surface of the standalone dial-home agent (clients/windows-agent).

The agent is a standalone script (not part of the alb package), so it's loaded
by path. These cover the pure, thread-safe parts — the AgentStatus state
machine, the JSON snapshot (which must never leak the token), and the HTML
renderer (which must escape user-controlled fields). The websockets/pyserial
I/O is not exercised here.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_AGENT_PATH = Path(__file__).resolve().parents[2] / "clients" / "windows-agent" / "alb_agent.py"


def _load_agent():
    spec = importlib.util.spec_from_file_location("alb_agent_under_test", _AGENT_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # Register BEFORE exec: dataclasses + `from __future__ import annotations`
    # resolve KW_ONLY via sys.modules[cls.__module__], which must exist.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


agent = _load_agent()


def _fresh():
    s = agent.AgentStatus()
    s.init(hub_url="wss://hub.example/agent/connect", agent_id="abcd1234ef", name="bench-01")
    return s


def test_initial_snapshot_is_disconnected():
    snap = _fresh().snapshot()
    assert snap["connected"] is False
    assert snap["name"] == "bench-01"
    assert snap["hub_url"] == "wss://hub.example/agent/connect"
    assert snap["active_channels"] == []
    assert snap["channels_total"] == 0


def test_connect_disconnect_transitions():
    s = _fresh()
    s.on_connected()
    assert s.snapshot()["connected"] is True
    s.on_disconnected("OSError: connection refused")
    snap = s.snapshot()
    assert snap["connected"] is False
    assert snap["last_error"] == "OSError: connection refused"
    assert snap["connected_for_s"] == 0


def test_reconnect_counter_increments():
    s = _fresh()
    s.on_reconnect_scheduled("boom")
    s.on_reconnect_scheduled()
    assert s.snapshot()["reconnects"] == 2


def test_channel_open_close_and_total():
    s = _fresh()
    s.channel_opened("cid-aaaa1111", "adb", "127.0.0.1:5037")
    s.channel_opened("cid-bbbb2222", "serial", "COM27 @ 1500000")
    snap = s.snapshot()
    assert snap["channels_total"] == 2
    kinds = {c["kind"] for c in snap["active_channels"]}
    assert kinds == {"adb", "serial"}
    # cid is truncated to 8 chars in the snapshot
    assert all(len(c["cid"]) <= 8 for c in snap["active_channels"])

    s.channel_closed("cid-aaaa1111")
    snap2 = s.snapshot()
    assert len(snap2["active_channels"]) == 1
    assert snap2["channels_total"] == 2  # total never decrements


def test_disconnect_clears_active_channels():
    s = _fresh()
    s.channel_opened("cid-1", "adb", "127.0.0.1:5037")
    s.on_disconnected()
    assert s.snapshot()["active_channels"] == []


def test_device_caches_round_trip():
    s = _fresh()
    s.set_adb_devices(["serial-1", "serial-2"])
    s.set_com_ports([{"port": "COM27", "desc": "USB serial"}])
    snap = s.snapshot()
    assert snap["adb_devices"] == ["serial-1", "serial-2"]
    assert snap["com_ports"][0]["port"] == "COM27"


def test_snapshot_never_contains_token():
    """The token is a separate CLI arg and must never reach the status surface."""
    s = _fresh()
    s.on_connected()
    s.channel_opened("cid-1", "adb", "127.0.0.1:5037")
    blob = json.dumps(s.snapshot()).lower()
    assert "token" not in blob
    assert "secret" not in blob


def test_snapshot_is_json_serializable():
    json.dumps(_fresh().snapshot())  # must not raise


def test_render_html_escapes_user_fields_and_shows_state():
    s = agent.AgentStatus()
    s.init(hub_url="wss://h/agent/connect", agent_id="dead", name="<script>x</script>")
    s.on_connected()
    html = agent._render_status_html(s.snapshot())
    assert "connected" in html
    # the malicious name must be escaped, not injected raw
    assert "<script>x</script>" not in html
    assert "&lt;script&gt;" in html


def test_render_html_disconnected_state():
    html = agent._render_status_html(_fresh().snapshot())
    assert "disconnected" in html


def test_render_html_lists_active_channels():
    s = _fresh()
    s.channel_opened("cid-aaaa1111", "serial", "COM27 @ 1500000")
    html = agent._render_status_html(s.snapshot())
    assert "COM27 @ 1500000" in html
    assert "serial" in html


@pytest.mark.parametrize("path", ["/status.json", "/"])
def test_status_handler_paths_exist(path):
    """Smoke: the handler class routes /status.json and / (no real socket)."""
    assert hasattr(agent, "_StatusHandler")
    assert hasattr(agent, "_start_status_server")


def test_status_server_serves_json_and_html_over_a_real_socket(monkeypatch):
    """End-to-end: bind an ephemeral port, serve /status.json + / for real.

    Uses monkeypatch to swap the module-global _STATUS the handler reads, so the
    real-socket test never pollutes the singleton for other (order-independent)
    tests."""
    import urllib.error
    import urllib.request

    fresh = agent.AgentStatus()
    fresh.init(hub_url="wss://h/agent/connect", agent_id="deadbeef", name="smoke")
    fresh.on_connected()
    monkeypatch.setattr(agent, "_STATUS", fresh)

    httpd = agent._start_status_server("127.0.0.1", 0)
    assert httpd is not None
    try:
        port = httpd.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/status.json", timeout=3) as r:
            data = json.loads(r.read())
        assert data["connected"] is True
        assert data["name"] == "smoke"
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3) as r:
            html = r.read().decode()
        assert "alb device agent" in html
        # unknown path → 404
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/nope", timeout=3)
        assert ei.value.code == 404
    finally:
        httpd.shutdown()


def test_status_server_bind_failure_returns_none():
    """A taken port must degrade gracefully — _start_status_server returns None
    and never raises, so the agent keeps running without a status page."""
    import socket

    occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupied.bind(("127.0.0.1", 0))
    occupied.listen(1)
    port = occupied.getsockname()[1]
    try:
        assert agent._start_status_server("127.0.0.1", port) is None
    finally:
        occupied.close()


def test_web_ui_url_derivation():
    assert agent._web_ui_url("ws://192.0.2.1:8765/agent/connect") == "http://192.0.2.1:8765/app/"
    assert agent._web_ui_url("wss://hub.example/agent/connect") == "https://hub.example/app/"
    assert agent._web_ui_url("nonsense") == ""


def test_status_page_links_web_console():
    snap = _fresh().snapshot()
    assert snap["web_ui"] == "https://hub.example/app/"
    html = agent._render_status_html(snap)
    assert '<a href="https://hub.example/app/">' in html
