"""Tests for ChatSession JSONL persistence."""

from __future__ import annotations

import json

import pytest

from alb.agent.backend import Message, ToolCall
from alb.agent.session import ChatSession, new_session_id


def test_new_session_id_format() -> None:
    sid = new_session_id()
    # YYYYMMDD-<8hex> = 8 + 1 + 8 = 17
    assert len(sid) == 17
    assert sid[8] == "-"
    assert sid[:8].isdigit()


def test_create_makes_dir_and_meta(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    s = ChatSession.create(backend="ollama", model="qwen2.5:3b", device="abc123")
    assert s.dir.is_dir()
    assert s.meta_file.exists()
    meta = json.loads(s.meta_file.read_text())
    assert meta["backend"] == "ollama"
    assert meta["model"] == "qwen2.5:3b"
    assert meta["device"] == "abc123"
    assert meta["session_id"] == s.session_id


def test_append_writes_jsonl_immediately(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    s = ChatSession.create(backend="ollama")
    s.append(Message(role="user", content="hello"))
    s.append(Message(role="assistant", content="hi"))

    lines = s.messages_file.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["content"] == "hello"
    assert json.loads(lines[1])["content"] == "hi"


def test_append_preserves_tool_calls(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    s = ChatSession.create()
    tc = ToolCall(id="tc_1", name="alb_logcat", arguments={"duration": 30})
    s.append(Message(role="assistant", content="", tool_calls=[tc]))

    line = s.messages_file.read_text().strip()
    d = json.loads(line)
    assert d["tool_calls"] == [
        {"id": "tc_1", "name": "alb_logcat", "arguments": {"duration": 30}}
    ]


def test_load_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    s1 = ChatSession.create(backend="ollama", model="qwen2.5:3b", device="dev")
    s1.append(Message(role="user", content="抓日志"))
    s1.append(
        Message(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="tc_1", name="alb_logcat", arguments={"duration": 30})],
        )
    )
    s1.append(
        Message(
            role="tool",
            content='{"ok": true}',
            tool_call_id="tc_1",
            name="alb_logcat",
        )
    )

    s2 = ChatSession.load(s1.session_id)
    assert s2.backend == "ollama"
    assert s2.model == "qwen2.5:3b"
    assert s2.device == "dev"

    msgs = s2.messages()
    assert len(msgs) == 3
    assert msgs[0].role == "user" and msgs[0].content == "抓日志"
    assert msgs[1].role == "assistant"
    assert msgs[1].tool_calls[0].name == "alb_logcat"
    assert msgs[1].tool_calls[0].arguments == {"duration": 30}
    assert msgs[2].role == "tool"
    assert msgs[2].tool_call_id == "tc_1"


def test_load_missing_session_returns_empty(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    s = ChatSession.load("20260101-deadbeef")
    assert s.messages() == []
    assert s.backend == ""


def test_load_skips_malformed_lines(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    s1 = ChatSession.create()
    s1.append(Message(role="user", content="good"))
    # Corrupt: append a malformed line
    with s1.messages_file.open("a") as f:
        f.write("{not json\n")
    s1.append(Message(role="assistant", content="also good"))

    s2 = ChatSession.load(s1.session_id)
    assert [m.content for m in s2.messages()] == ["good", "also good"]


def test_clear_resets_messages(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    s = ChatSession.create()
    s.append(Message(role="user", content="x"))
    s.append(Message(role="user", content="y"))
    s.clear()
    assert s.messages() == []
    assert not s.messages_file.exists()
    # new append should still work
    s.append(Message(role="user", content="z"))
    assert [m.content for m in s.messages()] == ["z"]
