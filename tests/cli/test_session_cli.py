"""Tests for `alb session list / show / replay`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from alb.cli.main import app

runner = CliRunner()


def _seed_session(
    workspace: Path,
    session_id: str,
    *,
    backend: str = "ollama",
    model: str = "qwen2.5:7b",
    created: str = "2026-05-09T00:00:00+00:00",
    messages: list[dict] | None = None,
) -> Path:
    """Write a fake meta.json + messages.jsonl under workspace/sessions/<id>/."""
    sdir = workspace / "sessions" / session_id
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "meta.json").write_text(
        json.dumps({
            "session_id": session_id,
            "created": created,
            "backend": backend,
            "model": model,
            "device": None,
        }),
    )
    if messages:
        with (sdir / "messages.jsonl").open("w") as f:
            for m in messages:
                json.dump(m, f, ensure_ascii=False)
                f.write("\n")
    return sdir


# ─── list ──────────────────────────────────────────────────────────


def test_session_list_empty(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    r = runner.invoke(app, ["session", "list"])
    assert r.exit_code == 0
    assert "No sessions found" in r.stdout


def test_session_list_sorted_newest_first(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    _seed_session(tmp_path, "20260101-aaaa", created="2026-01-01T00:00:00+00:00")
    _seed_session(tmp_path, "20260509-zzzz", created="2026-05-09T00:00:00+00:00")
    _seed_session(tmp_path, "20260301-mmmm", created="2026-03-01T00:00:00+00:00")
    r = runner.invoke(app, ["session", "list"])
    assert r.exit_code == 0
    # Find positions in output
    p_zzzz = r.stdout.find("20260509-zzzz")
    p_mmmm = r.stdout.find("20260301-mmmm")
    p_aaaa = r.stdout.find("20260101-aaaa")
    assert p_zzzz < p_mmmm < p_aaaa  # newest first


def test_session_list_respects_limit(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    for i in range(5):
        _seed_session(
            tmp_path,
            f"20260509-{i:04d}aaaa",
            created=f"2026-05-09T0{i}:00:00+00:00",
        )
    r = runner.invoke(app, ["session", "list", "--limit", "2"])
    assert r.exit_code == 0
    # Top 2 by created (descending) — i=4 and i=3
    assert "20260509-0004aaaa" in r.stdout
    assert "20260509-0003aaaa" in r.stdout
    assert "20260509-0000aaaa" not in r.stdout
    assert "20260509-0001aaaa" not in r.stdout


def test_session_list_json(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    _seed_session(
        tmp_path,
        "20260509-test",
        messages=[
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ],
    )
    r = runner.invoke(app, ["--json", "session", "list"])
    assert r.exit_code == 0
    payload = json.loads(r.stdout)
    assert payload["ok"] is True
    assert len(payload["sessions"]) == 1
    s = payload["sessions"][0]
    assert s["session_id"] == "20260509-test"
    assert s["turns"] == 2
    assert s["backend"] == "ollama"


# ─── show ──────────────────────────────────────────────────────────


def test_session_show_unknown_id(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    r = runner.invoke(app, ["session", "show", "nonexistent-id"])
    assert r.exit_code == 1
    assert "no session" in r.stdout
    assert "alb session list" in r.stdout  # hint shown


def test_session_show_renders_meta_and_messages(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    _seed_session(
        tmp_path,
        "20260509-show",
        messages=[
            {"role": "system", "content": "be helpful"},
            {"role": "user", "content": "ping"},
            {"role": "assistant", "content": "pong"},
        ],
    )
    r = runner.invoke(app, ["session", "show", "20260509-show"])
    assert r.exit_code == 0
    assert "20260509-show" in r.stdout
    assert "ollama" in r.stdout
    assert "ping" in r.stdout
    assert "pong" in r.stdout


def test_session_show_elides_middle_when_long(monkeypatch, tmp_path: Path) -> None:
    """≥11 messages → first 5 + ellipsis + last 5 (default; --full disables)."""
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    msgs = [
        {"role": "user", "content": f"msg-{i}"} for i in range(15)
    ]
    _seed_session(tmp_path, "20260509-long", messages=msgs)

    r = runner.invoke(app, ["session", "show", "20260509-long"])
    assert r.exit_code == 0
    # Head: msg-0..msg-4 visible
    assert "msg-0" in r.stdout
    assert "msg-4" in r.stdout
    # Tail: msg-10..msg-14 visible
    assert "msg-14" in r.stdout
    assert "msg-10" in r.stdout
    # Middle: msg-5..msg-9 elided
    assert "msg-7" not in r.stdout
    # Ellipsis line present
    assert "elided" in r.stdout


def test_session_show_full_disables_eliding(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    msgs = [{"role": "user", "content": f"msg-{i}"} for i in range(15)]
    _seed_session(tmp_path, "20260509-long", messages=msgs)

    r = runner.invoke(app, ["session", "show", "20260509-long", "--full"])
    assert r.exit_code == 0
    # Every message visible
    for i in range(15):
        assert f"msg-{i}" in r.stdout
    assert "elided" not in r.stdout


def test_session_show_json(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    _seed_session(
        tmp_path,
        "20260509-json",
        messages=[{"role": "user", "content": "hello"}],
    )
    r = runner.invoke(app, ["--json", "session", "show", "20260509-json"])
    assert r.exit_code == 0
    payload = json.loads(r.stdout)
    assert payload["ok"] is True
    assert payload["meta"]["session_id"] == "20260509-json"
    assert payload["turns"] == 1
    assert payload["messages"][0]["content"] == "hello"


def test_session_show_json_returns_all_messages_regardless_of_full(
    monkeypatch, tmp_path: Path
) -> None:
    """JSON output is always full (--full is human-render only).

    Locks the contract that programmatic consumers see every message
    regardless of whether the caller passed --full. Avoids a foot-gun
    where a script silently parses 10 of N messages.
    """
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    msgs = [{"role": "user", "content": f"msg-{i}"} for i in range(15)]
    _seed_session(tmp_path, "20260509-json-full", messages=msgs)

    # default (no --full) and --full should produce identical JSON in JSON mode
    r1 = runner.invoke(app, ["--json", "session", "show", "20260509-json-full"])
    r2 = runner.invoke(
        app, ["--json", "session", "show", "20260509-json-full", "--full"]
    )
    assert r1.exit_code == 0
    assert r2.exit_code == 0
    p1 = json.loads(r1.stdout)
    p2 = json.loads(r2.stdout)
    assert p1 == p2
    assert len(p1["messages"]) == 15  # all 15, never elided


# ─── replay ────────────────────────────────────────────────────────


def test_session_replay_streams_all_messages(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    msgs = [{"role": "user", "content": f"line-{i}"} for i in range(8)]
    _seed_session(tmp_path, "20260509-replay", messages=msgs)
    r = runner.invoke(app, ["session", "replay", "20260509-replay"])
    assert r.exit_code == 0
    for i in range(8):
        assert f"line-{i}" in r.stdout
    assert "elided" not in r.stdout  # replay never elides


def test_session_replay_unknown_id(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    r = runner.invoke(app, ["session", "replay", "missing"])
    assert r.exit_code == 1


def test_session_replay_renders_tool_calls(monkeypatch, tmp_path: Path) -> None:
    """Tool-call messages should show the tool name + arguments preview."""
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    _seed_session(
        tmp_path,
        "20260509-tools",
        messages=[
            {
                "role": "assistant",
                "content": "let me check",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "name": "alb_logcat",
                        "arguments": {"duration": 30, "device": "abc"},
                    }
                ],
            }
        ],
    )
    r = runner.invoke(app, ["session", "replay", "20260509-tools"])
    assert r.exit_code == 0
    assert "alb_logcat" in r.stdout
    assert "duration" in r.stdout
