"""Tests for GET /audit — driven by workspace/events.jsonl."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from alb.api.server import create_app


@pytest.fixture
def workspace(monkeypatch, tmp_path) -> Path:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    return tmp_path


@pytest.fixture
def client(workspace):
    app = create_app()
    with TestClient(app) as c:
        yield c


def _write_events(workspace: Path, events: list[dict[str, Any]]) -> Path:
    """Append events to workspace/events.jsonl, creating it if needed."""
    path = workspace / "events.jsonl"
    with path.open("a", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
    return path


def _now() -> datetime:
    return datetime.now(timezone.utc)


def test_empty_log_returns_no_events(client) -> None:
    r = client.get("/audit")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["events"] == []
    assert body["since"] < body["until"]


def test_window_filters_old_events(client, workspace) -> None:
    now = _now()
    in_window = (now - timedelta(minutes=5)).isoformat()
    out_of_window = (now - timedelta(hours=2)).isoformat()
    _write_events(
        workspace,
        [
            {"ts": in_window, "session_id": "s1", "source": "terminal",
             "kind": "command", "summary": "$ ls /data"},
            {"ts": out_of_window, "session_id": "s1", "source": "terminal",
             "kind": "command", "summary": "$ rm /tmp/x"},
            {"ts": in_window, "session_id": "s1", "source": "terminal",
             "kind": "deny", "summary": "deny: rm -rf / (fs_destructive)"},
        ],
    )
    body = client.get("/audit").json()
    summaries = [e["summary"] for e in body["events"]]
    assert any("ls /data" in s for s in summaries)
    assert any("rm -rf /" in s for s in summaries)
    assert not any("rm /tmp/x" in s for s in summaries)
    assert all(e["ts_approx"] is False for e in body["events"])


def test_chat_and_terminal_events_coexist(client, workspace) -> None:
    now = _now()
    ts = (now - timedelta(minutes=2)).isoformat()
    _write_events(
        workspace,
        [
            {"ts": ts, "session_id": "chat-1", "source": "chat",
             "kind": "user", "summary": "find battery drain"},
            {"ts": ts, "session_id": "chat-1", "source": "chat",
             "kind": "tool_call_start", "summary": "tool_calls: alb_top"},
            {"ts": ts, "session_id": "term-1", "source": "terminal",
             "kind": "command", "summary": "$ uptime"},
        ],
    )
    body = client.get("/audit").json()
    assert len(body["events"]) == 3
    sources = {e["source"] for e in body["events"]}
    assert sources == {"chat", "terminal"}


def test_minutes_param_widens_window(client, workspace) -> None:
    now = _now()
    old = (now - timedelta(hours=4)).isoformat()
    _write_events(
        workspace,
        [
            {"ts": old, "session_id": "s1", "source": "chat",
             "kind": "user", "summary": "still recent enough"},
        ],
    )
    short = client.get("/audit?minutes=30").json()["events"]
    long = client.get("/audit?minutes=300").json()["events"]
    assert short == []
    assert any("still recent enough" in e["summary"] for e in long)


def test_events_sorted_newest_first(client, workspace) -> None:
    now = _now()
    _write_events(
        workspace,
        [
            {"ts": (now - timedelta(minutes=2)).isoformat(),
             "session_id": "s1", "source": "terminal",
             "kind": "command", "summary": "newer"},
            {"ts": (now - timedelta(minutes=20)).isoformat(),
             "session_id": "s1", "source": "terminal",
             "kind": "command", "summary": "older"},
        ],
    )
    events = client.get("/audit").json()["events"]
    assert events[0]["summary"] == "newer"
    assert events[1]["summary"] == "older"
    assert events[0]["ts"] > events[1]["ts"]


def test_malformed_rows_skipped(client, workspace) -> None:
    """A bad JSONL row or a row with missing/unparseable ts is dropped."""
    path = workspace / "events.jsonl"
    now = _now()
    valid = json.dumps({
        "ts": (now - timedelta(minutes=5)).isoformat(),
        "session_id": "s1", "source": "terminal",
        "kind": "command", "summary": "good",
    })
    no_ts = json.dumps({
        "session_id": "s1", "source": "terminal",
        "kind": "command", "summary": "no ts",
    })
    bad_ts = json.dumps({
        "ts": "not-a-date", "session_id": "s1", "source": "terminal",
        "kind": "command", "summary": "bad ts",
    })
    path.write_text(f"not-json\n{valid}\n{no_ts}\n{bad_ts}\n")
    events = client.get("/audit").json()["events"]
    assert len(events) == 1
    assert events[0]["summary"] == "good"


def test_limit_truncates(client, workspace) -> None:
    now = _now()
    _write_events(
        workspace,
        [
            {"ts": (now - timedelta(minutes=i)).isoformat(),
             "session_id": "s1", "source": "terminal",
             "kind": "command", "summary": f"cmd{i}"}
            for i in range(1, 11)
        ],
    )
    events = client.get("/audit?limit=3").json()["events"]
    assert len(events) == 3


def test_param_bounds(client) -> None:
    assert client.get("/audit?minutes=0").status_code == 422
    assert client.get("/audit?minutes=99999").status_code == 422
    assert client.get("/audit?limit=0").status_code == 422
    assert client.get("/audit?limit=9999").status_code == 422


def test_default_fields_filled_in(client, workspace) -> None:
    """A minimal row that only has ts still produces a usable event with
    sensible defaults for missing keys."""
    now = _now()
    _write_events(
        workspace,
        [
            {"ts": (now - timedelta(minutes=1)).isoformat()},
        ],
    )
    body = client.get("/audit").json()
    assert len(body["events"]) == 1
    e = body["events"][0]
    assert e["session_id"] == ""
    assert e["source"] == "system"
    assert e["kind"] == "unknown"
    assert e["summary"] == ""
    assert e["ts_approx"] is False


def test_endpoint_listed_in_schema(client) -> None:
    paths = [(e["method"], e["path"]) for e in client.get("/api/version").json()["rest"]]
    assert ("GET", "/audit") in paths


def test_get_audit_filters_metric_kinds_by_default(client, workspace) -> None:
    """tps_sample (metric kind) should be hidden by default; opt-in via
    ?include_metrics=true."""
    now = _now()
    ts = (now - timedelta(minutes=2)).isoformat()
    _write_events(workspace, [
        {"ts": ts, "session_id": "s1", "source": "chat",
         "kind": "user", "summary": "hi"},
        {"ts": ts, "session_id": "s1", "source": "chat",
         "kind": "tps_sample", "summary": "100 tok/s"},
        {"ts": ts, "session_id": "s1", "source": "chat",
         "kind": "done", "summary": "agent done"},
    ])

    default = client.get("/audit").json()["events"]
    kinds = [e["kind"] for e in default]
    assert "tps_sample" not in kinds
    assert {"user", "done"} <= set(kinds)

    opt_in = client.get("/audit?include_metrics=true").json()["events"]
    kinds_in = [e["kind"] for e in opt_in]
    assert "tps_sample" in kinds_in


def test_ws_stream_listed_in_schema(client) -> None:
    ws_paths = [w["path"] for w in client.get("/api/version").json()["ws"]]
    assert "/audit/stream" in ws_paths


# ─── WS /audit/stream ──────────────────────────────────────────────


def test_ws_stream_default_snapshot(client, workspace) -> None:
    """Connect with no first message → 30 min window snapshot."""
    now = _now()
    _write_events(workspace, [
        {"ts": (now - timedelta(minutes=2)).isoformat(),
         "session_id": "s1", "source": "chat", "kind": "user",
         "summary": "hello"},
        {"ts": (now - timedelta(hours=2)).isoformat(),
         "session_id": "s1", "source": "chat", "kind": "user",
         "summary": "old"},
    ])
    with client.websocket_connect("/audit/stream") as ws:
        snap = ws.receive_json()
    assert snap["type"] == "snapshot"
    assert snap["since"] < snap["until"]
    summaries = [e["summary"] for e in snap["events"]]
    assert "hello" in summaries
    assert "old" not in summaries  # outside the 30-min window


def test_ws_stream_minutes_override(client, workspace) -> None:
    """Sending {minutes: 300} should pull older events into the snapshot."""
    now = _now()
    _write_events(workspace, [
        {"ts": (now - timedelta(hours=2)).isoformat(),
         "session_id": "s1", "source": "chat", "kind": "user",
         "summary": "two-hour-old"},
    ])
    with client.websocket_connect("/audit/stream") as ws:
        ws.send_json({"minutes": 300})
        snap = ws.receive_json()
    summaries = [e["summary"] for e in snap["events"]]
    assert "two-hour-old" in summaries


def test_ws_stream_pause_then_resume_acks(client, workspace) -> None:
    """Pause → ack with paused=true; resume → ack with paused=false."""
    with client.websocket_connect("/audit/stream") as ws:
        ws.receive_json()  # snapshot

        ws.send_json({"type": "control", "action": "pause"})
        ack1 = ws.receive_json()
        assert ack1["type"] == "control_ack"
        assert ack1["action"] == "pause"
        assert ack1["paused"] is True

        ws.send_json({"type": "control", "action": "resume"})
        ack2 = ws.receive_json()
        assert ack2["type"] == "control_ack"
        assert ack2["action"] == "resume"
        assert ack2["paused"] is False


def test_ws_stream_unknown_control_does_not_change_paused(client, workspace) -> None:
    """An unknown action is acked but state stays the same."""
    with client.websocket_connect("/audit/stream") as ws:
        ws.receive_json()  # snapshot
        ws.send_json({"type": "control", "action": "pause"})
        ws.receive_json()  # paused=true
        ws.send_json({"type": "control", "action": "wat"})
        ack = ws.receive_json()
        assert ack["type"] == "control_ack"
        assert ack["action"] == "wat"
        assert ack["paused"] is True  # unchanged


def test_ws_stream_non_control_messages_ignored(client, workspace) -> None:
    """A non-control message must not crash the handler — it's silently
    dropped and the next valid control still works."""
    with client.websocket_connect("/audit/stream") as ws:
        ws.receive_json()  # snapshot
        ws.send_json({"type": "garbage", "foo": "bar"})  # ignored
        ws.send_json({"type": "control", "action": "pause"})
        ack = ws.receive_json()
        assert ack["paused"] is True


def test_ws_stream_publishes_live_events_to_subscriber(client, workspace) -> None:
    """Verify the subscriber path: directly call get_bus().publish()
    inside the same event loop as the WS handler.

    Trick: TestClient's WebSocketSession exposes a `portal` (anyio
    blocking portal) that the test can use to call coroutines on the
    server's loop. This avoids spinning up a separate asyncio loop
    that the bus's asyncio.Lock would refuse to talk to.
    """
    from alb.infra.event_bus import get_bus, make_event

    with client.websocket_connect("/audit/stream") as ws:
        ws.receive_json()  # snapshot

        async def _emit() -> None:
            await get_bus().publish(make_event(
                session_id="live-1",
                source="chat",
                kind="tool_call_start",
                summary="tool_call: alb_top",
            ))

        ws.portal.call(_emit)

        msg = ws.receive_json()
        assert msg["type"] == "event"
        assert msg["data"]["session_id"] == "live-1"
        assert msg["data"]["summary"] == "tool_call: alb_top"


def test_ws_stream_filters_metric_kinds_by_default(client, workspace) -> None:
    """Default WS stream drops tps_sample; first-message
    `include_metrics:true` opts in."""
    from alb.infra.event_bus import get_bus, make_event

    # Default — no opt-in
    with client.websocket_connect("/audit/stream") as ws:
        ws.receive_json()  # snapshot

        async def emit() -> None:
            await get_bus().publish(make_event(
                session_id="s", source="chat", kind="tps_sample",
                summary="50 tok/s",
            ))
            await get_bus().publish(make_event(
                session_id="s", source="chat", kind="user", summary="hi",
            ))
        ws.portal.call(emit)

        live = ws.receive_json()
        assert live["type"] == "event"
        # The metric event must be dropped; only the user event should arrive
        assert live["data"]["kind"] == "user"


def test_ws_stream_include_metrics_passes_tps_sample(client, workspace) -> None:
    from alb.infra.event_bus import get_bus, make_event

    with client.websocket_connect("/audit/stream") as ws:
        ws.send_json({"include_metrics": True})
        ws.receive_json()  # snapshot

        async def emit() -> None:
            await get_bus().publish(make_event(
                session_id="s", source="chat", kind="tps_sample",
                summary="50 tok/s",
            ))
        ws.portal.call(emit)

        live = ws.receive_json()
        assert live["type"] == "event"
        assert live["data"]["kind"] == "tps_sample"


def test_ws_stream_paused_drops_live_events(client, workspace) -> None:
    """While paused, incoming bus events are dropped (not queued)."""
    from alb.infra.event_bus import get_bus, make_event

    with client.websocket_connect("/audit/stream") as ws:
        ws.receive_json()  # snapshot

        ws.send_json({"type": "control", "action": "pause"})
        ack = ws.receive_json()
        assert ack["paused"] is True

        async def _emit_two() -> None:
            for i in range(2):
                await get_bus().publish(make_event(
                    session_id="x", source="chat", kind="user",
                    summary=f"dropped-{i}",
                ))

        ws.portal.call(_emit_two)

        # Resume; subsequently published events should arrive but the
        # two we sent while paused should not (no queue catch-up).
        ws.send_json({"type": "control", "action": "resume"})
        resumed = ws.receive_json()
        assert resumed["paused"] is False

        async def _emit_after_resume() -> None:
            await get_bus().publish(make_event(
                session_id="x", source="chat", kind="user",
                summary="kept",
            ))

        ws.portal.call(_emit_after_resume)
        live = ws.receive_json()
        assert live["type"] == "event"
        assert live["data"]["summary"] == "kept"
