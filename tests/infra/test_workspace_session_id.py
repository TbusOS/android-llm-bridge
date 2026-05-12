"""Path-traversal hardening for `infra.workspace.session_path` /
`workspace_path` (L-035).

Locks the contract that user-supplied identifiers (session_id, device
serial) cannot escape `<workspace>/`. CLI / Web UI / MCP tools all
funnel through `session_path` and `workspace_path`, so root-layer
validation is the single chokepoint.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from alb.infra.workspace import (
    InvalidDeviceSerial,
    InvalidSessionId,
    session_path,
    workspace_path,
)


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


# ─── workspace_path device hardening (L-035 root-layer · same lesson) ───


@pytest.mark.parametrize(
    "bad_device",
    [
        ".",                 # single dot (matches old regex! leading char rule blocks)
        "..",                # parent — traversal
        "../etc",            # parent with target
        "../../tmp",         # double parent
        "subdir/file",       # forward slash
        r"subdir\file",      # backslash
        "/abs/path",         # absolute
        "name with space",   # whitespace
        "name\x00null",      # NUL byte
        "ümlaut",             # non-ASCII (also fails leading-alnum)
        "a" * 100,           # > 64 char limit
        ".hidden",           # leading dot
        "-leading-dash",     # leading dash
        "_leading-uscore",   # leading underscore
        ":colon-led",        # leading colon
    ],
)
def test_workspace_path_rejects_traversal_in_device(
    monkeypatch, tmp_path: Path, bad_device: str
) -> None:
    """Bad device serial must raise before any FS side-effect."""
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    with pytest.raises(InvalidDeviceSerial):
        workspace_path("logs", "x.txt", device=bad_device)
    # No directory created at the rejected target
    assert not (tmp_path / "devices" / bad_device / "logs").exists()


@pytest.mark.parametrize(
    "good_device",
    [
        "abc123",             # plain alnum
        "7bcb17848a177476",   # typical adb serial
        "192.168.1.5:5555",   # adb tcp form
        "device-A.local",     # ssh host
        "a",                  # single char
        "a" * 64,             # at limit
        "ttyUSB0",            # local serial port basename
        "COM27",              # windows serial
    ],
)
def test_workspace_path_accepts_real_world_devices(
    monkeypatch, tmp_path: Path, good_device: str
) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    p = workspace_path("logs", "x.txt", device=good_device)
    assert (tmp_path / "devices" / good_device / "logs" / "x.txt") == p
    assert p.parent.is_dir()


def test_workspace_path_no_device_skips_validation(
    monkeypatch, tmp_path: Path
) -> None:
    """device=None / "" is fine — falls back to global workspace dir."""
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    p = workspace_path("logs", "x.txt")
    assert p == tmp_path / "logs" / "x.txt"


def test_workspace_path_empty_device_string_treated_as_no_device(
    monkeypatch, tmp_path: Path
) -> None:
    """device="" matches `if device:` falsy check → skip devices/ prefix,
    no validation needed (caller didn't request device-scoped path).
    Documents the boundary so future readers don't accidentally tighten this."""
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    p = workspace_path("logs", "x.txt", device="")
    assert p == tmp_path / "logs" / "x.txt"


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
