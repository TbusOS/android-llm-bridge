"""Tests for `alb chat` CLI subcommand (M2 step 2)."""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from alb.agent.backend import ChatResponse, LLMBackend, Message
from alb.cli.main import app

runner = CliRunner()


class _FakeBackend(LLMBackend):
    name = "fake"
    supports_tool_calls = True

    def __init__(self, reply: str = "ok") -> None:
        self.model = "fake-model"
        self._reply = reply

    async def chat(self, messages: list[Message], **kwargs: Any) -> ChatResponse:
        return ChatResponse(content=self._reply, finish_reason="stop", model=self.model)


def test_chat_help():
    r = runner.invoke(app, ["chat", "--help"])
    assert r.exit_code == 0, r.output
    assert "Interactive LLM agent REPL" in r.output
    assert "--fast" in r.output
    assert "--model" in r.output
    assert "--ollama-url" in r.output


def test_chat_one_shot(monkeypatch, tmp_path):
    """One-shot: `alb chat 'hi'` should print the reply and exit cleanly."""
    monkeypatch.chdir(tmp_path)

    def _fake_factory(name: str, **kwargs: Any) -> LLMBackend:
        return _FakeBackend(reply="device connected")

    monkeypatch.setattr("alb.cli.chat_cli.get_backend", _fake_factory)

    r = runner.invoke(app, ["chat", "--no-tools", "你好"])
    assert r.exit_code == 0, r.output
    assert "device connected" in r.output
    # banner should show backend:model
    assert "fake:fake-model" in r.output


def test_chat_fast_shortcut(monkeypatch, tmp_path):
    """--fast should translate to model=gemma4:e4b when no --model given."""
    monkeypatch.chdir(tmp_path)

    captured: dict = {}

    def _spy_factory(name: str, **kwargs: Any) -> LLMBackend:
        captured.update(kwargs)
        return _FakeBackend(reply="ok")

    monkeypatch.setattr("alb.cli.chat_cli.get_backend", _spy_factory)
    r = runner.invoke(app, ["chat", "--no-tools", "--fast", "hi"])
    assert r.exit_code == 0, r.output
    assert captured.get("model") == "gemma4:e4b"


def test_chat_env_var_model(monkeypatch, tmp_path):
    """ALB_OLLAMA_MODEL env var should populate --model default."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALB_OLLAMA_MODEL", "gemma4:26b")

    captured: dict = {}

    def _spy_factory(name: str, **kwargs: Any) -> LLMBackend:
        captured.update(kwargs)
        return _FakeBackend(reply="ok")

    monkeypatch.setattr("alb.cli.chat_cli.get_backend", _spy_factory)
    r = runner.invoke(app, ["chat", "--no-tools", "hi"])
    assert r.exit_code == 0, r.output
    assert captured.get("model") == "gemma4:26b"


def test_chat_unknown_backend(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    r = runner.invoke(app, ["chat", "--no-tools", "--backend", "no-such", "hi"])
    assert r.exit_code == 1
    assert "backend init failed" in r.output.lower() or "unknown backend" in r.output.lower()
