"""Path-traversal hardening for `infra.workspace.session_path` (L-035).

Locks the contract that user-supplied session_ids cannot escape
`<workspace>/sessions/`. CLI / Web UI / MCP tools all funnel through
`session_path` (or `ChatSession.load` which uses it), so root-layer
validation is the single chokepoint.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from alb.infra.workspace import InvalidSessionId, session_path


@pytest.mark.parametrize(
    "bad_id",
    [
        "",                     # empty
        ".",                    # current dir
        "..",                   # parent — classic traversal
        "../etc",               # parent escape with target
        "../../tmp",            # double parent
        "subdir/file",          # path separator inside id
        r"subdir\file",         # backslash separator (Windows-style)
        "/absolute/path",       # absolute path
        r"C:\Windows",          # Windows absolute
        "name with space",      # whitespace not in safe charset
        "name\x00null",         # NUL byte injection
        "ümläut",               # non-ASCII (factory only emits ASCII hex)
        "a" * 200,              # > 128 char limit
        "-leading-dash",        # leading non-alnum
        "_leading-underscore",  # leading underscore (factory uses date prefix)
    ],
)
def test_session_path_rejects_traversal_and_unsafe_ids(
    monkeypatch, tmp_path: Path, bad_id: str
) -> None:
    """All these inputs must raise InvalidSessionId before touching FS."""
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    with pytest.raises(InvalidSessionId):
        session_path(bad_id)
    # No directory created at the rejected target either
    assert not (tmp_path / "sessions" / bad_id).exists()


@pytest.mark.parametrize(
    "good_id",
    [
        "20260509-aabbccdd",  # factory format
        "abcDEF123",          # plain alnum
        "a-b-c",              # internal dashes ok
        "a_b_c",              # internal underscores ok
        "x",                  # single char ok
        "a" * 128,            # exactly at limit
    ],
)
def test_session_path_accepts_valid_ids(
    monkeypatch, tmp_path: Path, good_id: str
) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    p = session_path(good_id, ensure_dir=False)
    assert p.parent == tmp_path / "sessions"
    assert p.name == good_id


def test_session_path_traversal_escape_via_resolved_symlink_blocked(
    monkeypatch, tmp_path: Path
) -> None:
    """Defence-in-depth: even if a regex-passing id resolves outside via
    a planted symlink, the .resolve()/is_relative_to check must catch it.
    """
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    target_outside = tmp_path / "elsewhere"
    target_outside.mkdir()
    # Plant a symlink: sessions/escape → ../elsewhere
    symlink = sessions_dir / "escape"
    try:
        symlink.symlink_to(target_outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported in this environment")
    # `escape` matches the safe-id regex but resolves outside sessions/
    with pytest.raises(InvalidSessionId, match="escapes sessions/"):
        session_path("escape")
