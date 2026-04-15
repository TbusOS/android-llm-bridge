"""Tests for rsync_sync capability."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from alb.capabilities.filesync import rsync_sync
from alb.infra.permissions import PermissionResult
from alb.transport.base import ShellResult


def _mk_ssh_transport(
    rsync_ok: bool = True,
    stdout: str = "sent 42 files\n",
    stderr: str = "",
) -> AsyncMock:
    t = AsyncMock()
    t.name = "ssh"
    t.check_permissions = AsyncMock(return_value=PermissionResult(behavior="allow"))
    t.rsync = AsyncMock(
        return_value=ShellResult(
            ok=rsync_ok,
            exit_code=0 if rsync_ok else 23,
            stdout=stdout,
            stderr=stderr if not rsync_ok else "",
            duration_ms=100,
            error_code=None if rsync_ok else "SSH_COMMAND_FAILED",
        )
    )
    return t


@pytest.mark.asyncio
async def test_rsync_requires_ssh_transport(tmp_path: Path) -> None:
    t = AsyncMock()
    t.name = "adb"
    r = await rsync_sync(t, tmp_path, "/data/dev")
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "TRANSPORT_NOT_SUPPORTED"


@pytest.mark.asyncio
async def test_rsync_missing_local(tmp_path: Path) -> None:
    t = _mk_ssh_transport()
    r = await rsync_sync(t, tmp_path / "does_not_exist", "/data/dev")
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "FILE_NOT_FOUND"


@pytest.mark.asyncio
async def test_rsync_permission_deny(tmp_path: Path) -> None:
    t = _mk_ssh_transport()
    t.check_permissions = AsyncMock(
        return_value=PermissionResult(behavior="deny", reason="big no-no")
    )
    r = await rsync_sync(t, tmp_path, "/data/dev")
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_rsync_happy_path(tmp_path: Path) -> None:
    t = _mk_ssh_transport()
    r = await rsync_sync(t, tmp_path, "/data/dev")
    assert r.ok
    assert r.data is not None
    assert r.data["remote_dir"] == "/data/dev"
    assert "stdout_tail" in r.data


@pytest.mark.asyncio
async def test_rsync_surfaces_failure(tmp_path: Path) -> None:
    t = _mk_ssh_transport(rsync_ok=False, stderr="rsync: failed: bla")
    r = await rsync_sync(t, tmp_path, "/data/dev")
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "SSH_COMMAND_FAILED"
    assert "rsync: failed" in (r.error.message or "")
