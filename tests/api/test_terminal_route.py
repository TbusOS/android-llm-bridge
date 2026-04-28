"""Tests for /terminal/ws — uses an in-process fake transport that
returns a real PTY-attached `cat` so we exercise the full byte path
without needing a real device."""

from __future__ import annotations

import asyncio
import sys
from typing import Any

import pytest
from fastapi.testclient import TestClient

from alb.api.server import create_app
from alb.transport.base import ShellResult, Transport
from alb.transport.interactive import open_pty_subprocess


pytestmark = pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="PTY tests need Unix",
)


class _PtyFakeTransport(Transport):
    """Stub transport whose interactive_shell returns a `cat` PTY."""

    name = "adb"

    async def shell(self, cmd: str, *, timeout: int = 30) -> ShellResult:
        return ShellResult(ok=True)

    async def stream_read(self, source: str, **kwargs: Any):  # noqa: ANN001
        if False:
            yield b""

    async def push(self, local, remote):  # noqa: ANN001
        return ShellResult(ok=True)

    async def pull(self, remote, local):  # noqa: ANN001
        return ShellResult(ok=True)

    async def reboot(self, mode: str = "normal") -> ShellResult:
        return ShellResult(ok=True)

    async def health(self) -> dict[str, Any]:
        return {"ok": True}

    async def interactive_shell(self, *, rows: int = 24, cols: int = 80):
        return await open_pty_subprocess("cat", rows=rows, cols=cols)


class _NoPtyTransport(_PtyFakeTransport):
    async def interactive_shell(self, *, rows: int = 24, cols: int = 80):
        raise NotImplementedError("serial does not support interactive_shell()")


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    from alb.infra.event_bus import reset_bus
    reset_bus()
    monkeypatch.setattr(
        "alb.api.terminal_route.build_transport",
        lambda **kwargs: _PtyFakeTransport(),
    )
    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture
def no_pty_client(monkeypatch, tmp_path):
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    from alb.infra.event_bus import reset_bus
    reset_bus()
    monkeypatch.setattr(
        "alb.api.terminal_route.build_transport",
        lambda **kwargs: _NoPtyTransport(),
    )
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_terminal_ws_ready_then_echo_via_text_input(client) -> None:
    with client.websocket_connect("/terminal/ws") as ws:
        ws.send_json({"device": None, "rows": 24, "cols": 80})
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        # Send via JSON `input` form so the test is encoding-agnostic
        ws.send_json({"type": "input", "data": "hello\n"})

        seen = b""
        for _ in range(50):
            msg = ws.receive()
            if "bytes" in msg and msg["bytes"]:
                seen += msg["bytes"]
                if b"hello" in seen:
                    break
            elif "text" in msg and msg["text"]:
                # If a closed/control message arrives, parse and bail
                pass
        assert b"hello" in seen


def test_terminal_ws_resize_no_crash(client) -> None:
    with client.websocket_connect("/terminal/ws") as ws:
        ws.send_json({})
        ws.receive_json()  # ready
        ws.send_json({"type": "resize", "rows": 40, "cols": 120})
        # Subsequent input still works after resize.
        ws.send_json({"type": "input", "data": "ok\n"})
        seen = b""
        for _ in range(40):
            msg = ws.receive()
            if "bytes" in msg and msg["bytes"]:
                seen += msg["bytes"]
                if b"ok" in seen:
                    break
        assert b"ok" in seen


def test_terminal_ws_close_control_terminates(client) -> None:
    with client.websocket_connect("/terminal/ws") as ws:
        ws.send_json({})
        ws.receive_json()  # ready
        ws.send_json({"type": "control", "action": "close"})
        # We expect a final closed message (text frame, not bytes).
        for _ in range(30):
            msg = ws.receive()
            text = msg.get("text") if isinstance(msg, dict) else None
            if text:
                import json
                obj = json.loads(text)
                if obj.get("type") == "closed":
                    return
        pytest.fail("did not receive `closed` event after control:close")


def test_terminal_ws_unsupported_transport(no_pty_client) -> None:
    with no_pty_client.websocket_connect("/terminal/ws") as ws:
        ws.send_json({})
        ev = ws.receive_json()
        assert ev["type"] == "closed"
        assert ev["error"]["code"] == "TRANSPORT_NO_PTY"


def test_terminal_ws_hitl_blocks_dangerous_command(client) -> None:
    """Type a dangerous command — the WS should send a hitl_request
    instead of forwarding it to the shell."""
    with client.websocket_connect("/terminal/ws") as ws:
        ws.send_json({"device": None, "rows": 24, "cols": 80})
        ws.receive_json()  # ready

        # Type the line via JSON so the bytes flow through the guard.
        ws.send_json({"type": "input", "data": "rm -rf /system/x\n"})

        # Drain frames; expect a hitl_request text frame.
        for _ in range(40):
            msg = ws.receive()
            text = msg.get("text") if isinstance(msg, dict) else None
            if text:
                import json
                obj = json.loads(text)
                if obj.get("type") == "hitl_request":
                    assert "rm -rf" in obj["command"]
                    assert obj["rule"]
                    return
        import pytest
        pytest.fail("never received hitl_request")


def test_terminal_ws_hitl_deny_then_safe_command_works(client) -> None:
    """After denying a dangerous command, the next safe command should
    still flow through the shell."""
    with client.websocket_connect("/terminal/ws") as ws:
        ws.send_json({})
        ws.receive_json()  # ready
        ws.send_json({"type": "input", "data": "reboot\n"})

        # Wait for hitl_request, then deny.
        for _ in range(40):
            msg = ws.receive()
            text = msg.get("text") if isinstance(msg, dict) else None
            if text:
                import json
                obj = json.loads(text)
                if obj.get("type") == "hitl_request":
                    ws.send_json({"type": "hitl_response", "approve": False})
                    break

        # Now send a safe command, expect to see its echo come back.
        ws.send_json({"type": "input", "data": "ok\n"})
        seen = b""
        for _ in range(60):
            msg = ws.receive()
            data = msg.get("bytes") if isinstance(msg, dict) else None
            if data:
                seen += data
                if b"ok" in seen:
                    return
        import pytest
        pytest.fail("post-deny safe command never echoed back")


def test_terminal_ws_set_read_only_ack(client) -> None:
    with client.websocket_connect("/terminal/ws") as ws:
        ws.send_json({})
        ws.receive_json()  # ready
        ws.send_json({"type": "set_read_only", "value": True})
        for _ in range(20):
            msg = ws.receive()
            text = msg.get("text") if isinstance(msg, dict) else None
            if text:
                import json
                obj = json.loads(text)
                if obj.get("type") == "control_ack" and obj.get("action") == "set_read_only":
                    assert obj["read_only"] is True
                    return
        import pytest
        pytest.fail("never received set_read_only ack")


# ─── Audit bus integration ──────────────────────────────────────────


def _read_events(workspace_root) -> list[dict]:
    import json
    from pathlib import Path

    p = Path(workspace_root) / "events.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def test_terminal_command_publishes_to_event_bus(client, tmp_path) -> None:
    """A regular terminal command should land in workspace/events.jsonl
    as a `terminal/command` event."""
    with client.websocket_connect("/terminal/ws") as ws:
        ws.send_json({"device": None, "rows": 24, "cols": 80})
        ready = ws.receive_json()
        sid = ready["session_id"]
        ws.send_json({"type": "input", "data": "uptime\n"})

        # Wait for the echo to be sure on_audit fired.
        seen = b""
        for _ in range(50):
            msg = ws.receive()
            if "bytes" in msg and msg["bytes"]:
                seen += msg["bytes"]
                if b"uptime" in seen:
                    break

    events = _read_events(tmp_path)
    cmd_events = [e for e in events
                  if e["source"] == "terminal" and e["kind"] == "command"]
    assert cmd_events, f"no terminal/command event in {events!r}"
    assert any("uptime" in e["summary"] for e in cmd_events)
    assert any(e["session_id"] == sid for e in cmd_events)


def test_terminal_deny_publishes_to_event_bus(client, tmp_path) -> None:
    """A denied (HITL) command should land as `terminal/<event>` —
    actual kind depends on TerminalGuard's audit payload schema."""
    with client.websocket_connect("/terminal/ws") as ws:
        ws.send_json({"device": None, "rows": 24, "cols": 80})
        ws.receive_json()
        ws.send_json({"type": "input", "data": "rm -rf /system/x\n"})

        # Drain until we see the hitl_request, then deny.
        for _ in range(40):
            msg = ws.receive()
            text = msg.get("text") if isinstance(msg, dict) else None
            if text:
                import json
                obj = json.loads(text)
                if obj.get("type") == "hitl_request":
                    ws.send_json({"type": "hitl_response", "approve": False})
                    break

        # Give the bus a beat to flush.
        for _ in range(5):
            try:
                ws.receive(timeout=0.05)
            except Exception:
                break

    events = _read_events(tmp_path)
    terminal_events = [e for e in events if e["source"] == "terminal"]
    # Either a deny event (intercepted) or hitl_deny (after response) —
    # both are valid signals that the guard fired.
    assert terminal_events, f"no terminal events in {events!r}"
    assert any("rm -rf" in e["summary"] for e in terminal_events)
