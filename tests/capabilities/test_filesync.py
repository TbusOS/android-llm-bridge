"""Tests for filesync capability."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from alb.capabilities.filesync import pull, push
from alb.infra.permissions import PermissionResult
from alb.transport.base import ShellResult


def _mk_transport(
    perm_behavior: str = "allow",
    push_ok: bool = True,
    pull_ok: bool = True,
) -> AsyncMock:
    t = AsyncMock()
    t.check_permissions = AsyncMock(
        return_value=PermissionResult(behavior=perm_behavior, reason="x", suggestion="y")
    )
    t.push = AsyncMock(
        return_value=ShellResult(ok=push_ok, exit_code=0 if push_ok else 1,
                                 stdout="", stderr="failed" if not push_ok else "",
                                 duration_ms=10)
    )
    t.pull = AsyncMock(
        return_value=ShellResult(ok=pull_ok, exit_code=0 if pull_ok else 1,
                                 stdout="", stderr="", duration_ms=10)
    )
    return t


@pytest.mark.asyncio
async def test_push_missing_local(tmp_path: Path) -> None:
    t = _mk_transport()
    r = await push(t, tmp_path / "nope.txt", "/data/local/tmp/x")
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "FILE_NOT_FOUND"


@pytest.mark.asyncio
async def test_push_deny(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("hi")
    t = _mk_transport(perm_behavior="deny")
    r = await push(t, f, "/system/priv-app/a.apk")
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_push_ask_without_flag(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("hi")
    t = _mk_transport(perm_behavior="ask")
    r = await push(t, f, "/system/app/a.apk")
    assert not r.ok
    assert r.error is not None
    assert r.error.details.get("behavior") == "ask"


@pytest.mark.asyncio
async def test_push_happy(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_bytes(b"data")
    t = _mk_transport()
    r = await push(t, f, "/data/local/tmp/a.txt")
    assert r.ok
    assert r.data is not None
    assert r.data.bytes_transferred == 4


@pytest.mark.asyncio
async def test_pull_default_local(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    t = _mk_transport()
    r = await pull(t, "/data/tombstones/tombstone_00", device="abc")
    assert r.ok
    assert len(r.artifacts) == 1
    assert "pulls" in str(r.artifacts[0])
    assert "tombstone_00" in str(r.artifacts[0])


@pytest.mark.asyncio
async def test_pull_error_surfaced(tmp_path) -> None:
    t = _mk_transport(pull_ok=False)
    r = await pull(t, "/nope", tmp_path / "dest")
    assert not r.ok
    assert r.error is not None
