"""Tests for the capture_uart capability (serial transport only)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from alb.capabilities.logging import capture_uart
from alb.infra.permissions import PermissionResult


async def _stream(chunks: list[bytes]) -> AsyncIterator[bytes]:
    for c in chunks:
        yield c


def _mk_serial_mock(chunks: list[bytes]) -> AsyncMock:
    t = AsyncMock()
    t.name = "serial"
    t.check_permissions = AsyncMock(return_value=PermissionResult(behavior="allow"))
    t.stream_read = lambda *a, **kw: _stream(chunks)
    return t


@pytest.mark.asyncio
async def test_capture_uart_refuses_non_serial() -> None:
    t = AsyncMock()
    t.name = "adb"
    r = await capture_uart(t, duration=1)
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "TRANSPORT_NOT_SUPPORTED"
    assert "serial" in (r.error.suggestion or "").lower()


@pytest.mark.asyncio
async def test_capture_uart_invalid_duration() -> None:
    t = _mk_serial_mock([])
    r = await capture_uart(t, duration=0)
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "INVALID_DURATION"


@pytest.mark.asyncio
async def test_capture_uart_happy_path(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    chunks = [
        b"[    0.000000] Booting Linux on CPU 0x0\n",
        b"[    1.234567] kernel BUG: unable to handle\n",
        b"[    2.345678] panic: sync\n",
    ]
    t = _mk_serial_mock(chunks)
    r = await capture_uart(t, duration=1, device="abc")
    assert r.ok
    assert r.data is not None
    assert r.data.lines == 3
    assert r.data.errors >= 2
    assert len(r.artifacts) == 1
    # Artifact should land under devices/abc/logs/
    assert "devices/abc/logs" in str(r.artifacts[0])
    assert r.artifacts[0].exists()
