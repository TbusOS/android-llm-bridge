"""Tests for the logging capability."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from alb.capabilities.logging import (
    collect_dmesg,
    collect_logcat,
    search_logs,
    tail_log,
)
from alb.infra.permissions import PermissionResult


async def _stream(chunks: list[bytes]) -> AsyncIterator[bytes]:
    for c in chunks:
        yield c


def _mk_transport(
    perm_behavior: str = "allow",
    stream_chunks: list[bytes] | None = None,
) -> AsyncMock:
    t = AsyncMock()
    t.check_permissions = AsyncMock(
        return_value=PermissionResult(behavior=perm_behavior)
    )
    # stream_read is a regular async generator, not AsyncMock
    chunks = stream_chunks or []
    t.stream_read = lambda *a, **kw: _stream(chunks)
    return t


@pytest.mark.asyncio
async def test_collect_logcat_invalid_duration() -> None:
    r = await collect_logcat(_mk_transport(), duration=0)
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "INVALID_DURATION"


@pytest.mark.asyncio
async def test_collect_logcat_permission_denied() -> None:
    r = await collect_logcat(_mk_transport(perm_behavior="deny"), duration=5)
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_collect_logcat_happy_path(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    chunks = [
        b"04-15 10:30:00.123 1000 1001 I ActivityManager: started app\n",
        b"04-15 10:30:01.456 1000 1002 E ActivityManager: crash!\n",
        b"04-15 10:30:02.789 1000 1003 W WindowManager: something\n",
    ]
    t = _mk_transport(stream_chunks=chunks)

    r = await collect_logcat(t, duration=1)
    assert r.ok
    assert r.data is not None
    assert r.data.lines == 3
    assert r.data.errors == 1
    assert r.data.warnings == 1
    tags = dict(r.data.top_tags)
    assert tags.get("ActivityManager") == 2
    assert len(r.artifacts) == 1
    assert r.artifacts[0].exists()


@pytest.mark.asyncio
async def test_collect_dmesg_counts_errors(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    chunks = [
        b"[    0.123] normal boot message\n",
        b"[    1.234] kernel BUG: NULL pointer\n",
        b"[    2.345] panic: something failed\n",
    ]
    t = _mk_transport(stream_chunks=chunks)
    r = await collect_dmesg(t, duration=1)
    assert r.ok
    assert r.data is not None
    assert r.data.lines == 3
    assert r.data.errors >= 2  # "BUG:" + "panic" + "failed" pattern


@pytest.mark.asyncio
async def test_search_logs_rejects_bad_regex() -> None:
    r = await search_logs(pattern="[unclosed")
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "INVALID_FILTER"


@pytest.mark.asyncio
async def test_search_logs_finds_matches(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    log_dir = tmp_path / "devices" / "abc" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "one.txt").write_text("foo\nbar FATAL thing\nbaz\n")
    (log_dir / "two.txt").write_text("FATAL nothing here\nhello\n")

    r = await search_logs("FATAL")
    assert r.ok
    assert r.data is not None
    assert len(r.data.matches) == 2


@pytest.mark.asyncio
async def test_tail_log_rejects_path_traversal(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    outside = tmp_path.parent / "secret.txt"
    outside.write_text("confidential\n")
    r = await tail_log(outside)
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "REMOTE_PATH_INVALID"


@pytest.mark.asyncio
async def test_tail_log_returns_last_n(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    p = tmp_path / "devices" / "abc" / "logs" / "a.txt"
    p.parent.mkdir(parents=True)
    p.write_text("".join(f"line {i}\n" for i in range(1, 11)))
    r = await tail_log(p, lines=3)
    assert r.ok
    assert r.data is not None
    assert r.data.strip().splitlines() == ["line 8", "line 9", "line 10"]


@pytest.mark.asyncio
async def test_tail_log_range(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    p = tmp_path / "devices" / "abc" / "logs" / "b.txt"
    p.parent.mkdir(parents=True)
    p.write_text("".join(f"line {i}\n" for i in range(1, 11)))
    r = await tail_log(p, from_line=3, to_line=5)
    assert r.ok
    assert r.data is not None
    assert r.data.strip().splitlines() == ["line 3", "line 4", "line 5"]
