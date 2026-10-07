"""Tests keep out of the real workspace (fixtures in tests/conftest.py).

Before the conftest fixtures, one full run of the suite left 4 flash job
records in the repo's workspace/flash/, 3 chat sessions in
~/.alb-workspace/sessions/ and appended to ~/.alb-workspace/events.jsonl.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from alb.infra.workspace import workspace_path, workspace_root


def test_each_test_gets_a_private_workspace(tmp_path_factory: pytest.TempPathFactory) -> None:
    root = workspace_root()
    assert root.is_relative_to(tmp_path_factory.getbasetemp()), root
    assert root != Path.cwd() / "workspace"
    assert root != Path.home() / ".alb-workspace"
    # A flash job record (FlashService → flash_timeline) lands under it.
    assert workspace_path("flash", "x").is_relative_to(root)


def test_falling_back_to_the_default_workspace_is_caught(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
    default_workspace_hits: list[str],
) -> None:
    monkeypatch.delenv("ALB_WORKSPACE")
    root = workspace_root()
    # Recorded (the fixture would fail this test at teardown) …
    assert default_workspace_hits == ["Path.cwd()", "Path.home()"]
    # … and answered with a scratch dir, not the real ~/.alb-workspace.
    assert root.is_relative_to(tmp_path_factory.getbasetemp()), root
    default_workspace_hits.clear()
