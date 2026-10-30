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
def guarded_client(monkeypatch, client):
    """`client` with confirm rules installed. The default rule set is empty
    (board-side commands are not restricted), so the HITL path needs some.
    TerminalGuard reads the module default when the WS connects."""
    from alb.transport.terminal_guard import _r

    monkeypatch.setattr(
        "alb.transport.terminal_guard.DEFAULT_DANGER_RULES",
        (
            _r("rm-system", r"^\s*rm\s+(-\S+\s+)*/system(/|$)", "rm under /system"),
            _r("reboot", r"^\s*reboot(\s|$)", "reboot ends the session"),
        ),
    )
    return client


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


def test_terminal_ws_invalid_session_id_structured_close(client) -> None:
    """L-035: bad client-supplied session_id → structured `closed` frame
    with INVALID_SESSION_ID, not abrupt 1011 close. Locks meta-audit fix."""
    with client.websocket_connect("/terminal/ws") as ws:
        ws.send_json({"session_id": "../etc"})
        ev = ws.receive_json()
        assert ev["type"] == "closed"
        assert ev["error"]["code"] == "INVALID_SESSION_ID"


def test_terminal_ws_board_command_not_held_by_default(client) -> None:
    """2026-10-23: with the default (empty) rule set a board command goes
    straight to the shell — no hitl_request, and the shell echoes it."""
    import json

    with client.websocket_connect("/terminal/ws") as ws:
        ws.send_json({"device": None, "rows": 24, "cols": 80})
        ws.receive_json()  # ready
        ws.send_json({"type": "input", "data": "reboot bootloader\n"})

        seen = b""
        for _ in range(80):
            msg = ws.receive()
            text = msg.get("text") if isinstance(msg, dict) else None
            if text and json.loads(text).get("type") == "hitl_request":
                pytest.fail("board command was held for confirmation")
            data = msg.get("bytes") if isinstance(msg, dict) else None
            if data:
                seen += data
                # One copy is the guard's own keystroke echo; a second can
                # only come from the shell, i.e. the line was forwarded.
                if seen.count(b"reboot bootloader") >= 2:
                    return
        pytest.fail(f"command never reached the shell: {seen!r}")


def test_terminal_ws_hitl_blocks_dangerous_command(guarded_client) -> None:
    """Type a command matching a confirm rule — the WS should send a
    hitl_request instead of forwarding it to the shell."""
    with guarded_client.websocket_connect("/terminal/ws") as ws:
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


def test_terminal_ws_hitl_deny_then_safe_command_works(guarded_client) -> None:
    """After denying a held command, the next safe command should
    still flow through the shell."""
    with guarded_client.websocket_connect("/terminal/ws") as ws:
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


def test_terminal_deny_publishes_to_event_bus(guarded_client, tmp_path) -> None:
    """A denied (HITL) command should land as `terminal/<event>` —
    actual kind depends on TerminalGuard's audit payload schema."""
    with guarded_client.websocket_connect("/terminal/ws") as ws:
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


# ─── MID-5 retroactive verification (functional audit 2026-05-02) ───
# Audit claim: "Shell PTY spawn that succeeds but shell exits
# immediately (e.g. su denied) → terminal silently shows exit_code 1,
# no rationale string from stdout buffer."
# 2026-05-05 retroactive verify: stdout bytes ARE forwarded to the
# client via ws.send_bytes BEFORE the close-frame is sent, because
# _send_loop drains the master fd until EOF (which only happens after
# the shell process exits AND its stdout buffer has been drained).
# The user sees the rationale in xterm AND gets exit_code in the
# close-frame. The audit's "no rationale" phrasing was misleading —
# the rationale just lives in xterm output, not duplicated in JSON.
# These tests lock the order in (bytes then close-frame, exit code
# preserved).


class _ExitOneTransport(_PtyFakeTransport):
    """PTY runs `sh -c 'echo "Permission denied" 1>&2; exit 7'` —
    shell writes to stderr (PTY merges into stdout) then exits with
    code 7. Models the "su denied" scenario from the audit."""

    async def interactive_shell(self, *, rows: int = 24, cols: int = 80):
        return await open_pty_subprocess(
            "sh", "-c",
            "printf 'Permission denied\\n' 1>&2; exit 7",
            rows=rows, cols=cols,
        )


@pytest.fixture
def exit_one_client(monkeypatch, tmp_path):
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    from alb.infra.event_bus import reset_bus
    reset_bus()
    monkeypatch.setattr(
        "alb.api.terminal_route.build_transport",
        lambda **kwargs: _ExitOneTransport(),
    )
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_pty_immediate_exit_forwards_stdout_then_close_with_exit_code(
    exit_one_client,
) -> None:
    """When PTY spawns OK but exits immediately:
    1. stdout bytes (the rationale) reach the client via send_bytes
    2. The close-frame carries the non-zero exit_code
    3. Order is bytes → close-frame (so xterm renders before close)."""
    import json as _json

    saw_bytes = b""
    closed_msg = None

    with exit_one_client.websocket_connect("/terminal/ws") as ws:
        ws.send_json({"device": None, "rows": 24, "cols": 80})
        ws.receive_json()  # ready

        # Drain frames until we see the close-frame. ws.receive() blocks
        # until the server sends a frame or closes the socket; on
        # WebSocketDisconnect we exit the loop. The shell exits
        # ~immediately so this drains in O(ms).
        for _ in range(60):
            try:
                msg = ws.receive()
            except Exception:
                break
            if not isinstance(msg, dict):
                continue
            if msg.get("type") == "websocket.disconnect":
                break
            data = msg.get("bytes")
            if data:
                saw_bytes += data
                continue
            text = msg.get("text")
            if text:
                obj = _json.loads(text)
                if obj.get("type") == "closed":
                    closed_msg = obj
                    break

    assert closed_msg is not None, "never received closed frame"
    assert closed_msg.get("exit_code") == 7, closed_msg
    # The rationale bytes must have arrived (audit's "silently shows
    # exit_code 1, no rationale" was a misread — rationale comes via
    # stdout, just not in the close-frame JSON).
    assert b"Permission denied" in saw_bytes, (
        f"expected stderr bytes in stream, got {saw_bytes!r}"
    )


# ─── round11 SEC-2: malformed serial rejected before build_transport ──
def test_terminal_bad_device_rejected(client) -> None:
    with client.websocket_connect("/terminal/ws") as ws:
        ws.send_json({"device": "bad; reboot", "rows": 24, "cols": 80})
        msg = ws.receive_json()
        assert msg["type"] == "closed"
        assert msg["error"]["code"] == "INVALID_DEVICE"


# ── per-command exit codes (transport/exit_marks) ─────────────────────


class _BashTransport(_PtyFakeTransport):
    """A real interactive bash: SETUP has to work on it end to end."""

    async def interactive_shell(self, *, rows: int = 24, cols: int = 80) -> Any:
        import os

        env = {k: v for k, v in os.environ.items() if k not in ("ENV", "PROMPT_COMMAND")}
        env["PS1"] = "probe$ "
        return await open_pty_subprocess(
            "bash", "--norc", "--noprofile", "-i", env=env, rows=rows, cols=cols
        )


@pytest.fixture
def bash_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> Any:
    import shutil

    if not shutil.which("bash"):
        pytest.skip("needs bash")
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    from alb.infra.event_bus import reset_bus

    reset_bus()
    monkeypatch.setattr("alb.api.terminal_route.build_transport", lambda **kwargs: _BashTransport())
    with TestClient(create_app()) as c:
        yield c


def test_a_typed_command_reports_its_exit_code(bash_client: Any, tmp_path: Any) -> None:
    exits: list[dict[str, Any]] = []
    shown = b""
    with bash_client.websocket_connect("/terminal/ws") as ws:
        ws.send_json({"device": None, "session_id": "term-exit-test"})
        assert ws.receive_json()["type"] == "ready"
        for cmd in ("false", "sh -c 'exit 3'", "true"):
            ws.send_json({"type": "input", "data": cmd + "\r"})
            for _ in range(200):
                msg = ws.receive()
                if msg.get("bytes"):
                    shown += msg["bytes"]
                elif msg.get("text"):
                    import json as _json

                    obj = _json.loads(msg["text"])
                    if obj.get("type") == "command_exit":
                        exits.append(obj)
                        break
    assert [(e["line"], e["exit_code"]) for e in exits] == [
        ("false", 1),
        ("sh -c 'exit 3'", 3),
        ("true", 0),
    ]
    assert b"133;D" not in shown, "markers and the SETUP echo stay out of the terminal"
    events = [e for e in _read_events(tmp_path) if e.get("kind") == "command_exit"]
    assert [e["summary"] for e in events] == ["exit 1: false", "exit 3: sh -c 'exit 3'", "exit 0: true"]


def test_exit_codes_can_be_turned_off(bash_client: Any) -> None:
    shown = b""
    with bash_client.websocket_connect("/terminal/ws") as ws:
        ws.send_json({"device": None, "exit_codes": False})
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "input", "data": "echo marker-off-check\r"})
        for _ in range(200):
            msg = ws.receive()
            if msg.get("bytes"):
                shown += msg["bytes"]
                if shown.count(b"marker-off-check") >= 2:
                    break
    assert b"133;D" not in shown and b"KSH_VERSION" not in shown, "no SETUP was sent"
