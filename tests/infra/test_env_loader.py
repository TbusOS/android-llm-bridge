"""Tests for alb.infra.env_loader."""

from __future__ import annotations

import os

from alb.infra.env_loader import load_env_files


def test_loads_env_local_into_environ(tmp_path, monkeypatch):
    (tmp_path / ".env.local").write_text(
        "ALB_TEST_KEY1=value1\nALB_TEST_KEY2=value2\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("ALB_TEST_KEY1", raising=False)
    monkeypatch.delenv("ALB_TEST_KEY2", raising=False)

    loaded = load_env_files(roots=[tmp_path])

    assert len(loaded) == 1
    assert loaded[0].name == ".env.local"
    assert os.environ["ALB_TEST_KEY1"] == "value1"
    assert os.environ["ALB_TEST_KEY2"] == "value2"


def test_shell_env_wins_over_file(tmp_path, monkeypatch):
    (tmp_path / ".env.local").write_text("ALB_PRECEDENCE=from_file\n", encoding="utf-8")
    monkeypatch.setenv("ALB_PRECEDENCE", "from_shell")

    load_env_files(roots=[tmp_path])

    assert os.environ["ALB_PRECEDENCE"] == "from_shell"


def test_env_local_takes_priority_over_env(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("ALB_TWO_FILE_KEY=from_env\n", encoding="utf-8")
    (tmp_path / ".env.local").write_text("ALB_TWO_FILE_KEY=from_local\n", encoding="utf-8")
    monkeypatch.delenv("ALB_TWO_FILE_KEY", raising=False)

    load_env_files(roots=[tmp_path])

    assert os.environ["ALB_TWO_FILE_KEY"] == "from_local"


def test_comments_and_blanks_ignored(tmp_path, monkeypatch):
    (tmp_path / ".env.local").write_text(
        "# full comment\n"
        "\n"
        "   \n"
        "ALB_REAL=real\n"
        "  # indented comment\n"
        "not a key because no equals sign\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("ALB_REAL", raising=False)

    load_env_files(roots=[tmp_path])

    assert os.environ["ALB_REAL"] == "real"


def test_quotes_stripped(tmp_path, monkeypatch):
    (tmp_path / ".env.local").write_text(
        'ALB_DQ="value with spaces"\n'
        "ALB_SQ='single'\n"
        "ALB_NQ=plain\n",
        encoding="utf-8",
    )
    for k in ("ALB_DQ", "ALB_SQ", "ALB_NQ"):
        monkeypatch.delenv(k, raising=False)

    load_env_files(roots=[tmp_path])

    assert os.environ["ALB_DQ"] == "value with spaces"
    assert os.environ["ALB_SQ"] == "single"
    assert os.environ["ALB_NQ"] == "plain"


def test_export_prefix_allowed(tmp_path, monkeypatch):
    (tmp_path / ".env.local").write_text(
        "export ALB_WITH_EXPORT=ok\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("ALB_WITH_EXPORT", raising=False)

    load_env_files(roots=[tmp_path])

    assert os.environ["ALB_WITH_EXPORT"] == "ok"


def test_no_files_returns_empty(tmp_path):
    loaded = load_env_files(roots=[tmp_path])
    assert loaded == []
