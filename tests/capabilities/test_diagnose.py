"""Tests for diagnose capability."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from alb.capabilities.diagnose import (
    _parse_bugreportz_output,
    _parse_getprop,
    anr_pull,
    devinfo,
)
from alb.infra.permissions import PermissionResult
from alb.transport.base import ShellResult


# ─── Parsers ───────────────────────────────────────────────────────
def test_parse_bugreportz_output_ok() -> None:
    out = "BEGIN\nPROGRESS:30/100\nOK:/sdcard/bugreports/bugreport.zip\n"
    assert (
        _parse_bugreportz_output(out)
        == "/sdcard/bugreports/bugreport.zip"
    )


def test_parse_bugreportz_output_fail() -> None:
    assert _parse_bugreportz_output("FAIL:no space\n") == ""


def test_parse_getprop() -> None:
    sample = """[ro.product.model]: [Pixel 7]
[ro.build.version.sdk]: [33]
[persist.something]: []
"""
    props = _parse_getprop(sample)
    assert props["ro.product.model"] == "Pixel 7"
    assert props["ro.build.version.sdk"] == "33"
    assert props["persist.something"] == ""


# ─── Mocked transport ──────────────────────────────────────────────
def _mk_transport(
    shell_responses: dict[str, ShellResult],
    transport_name: str = "adb",
) -> AsyncMock:
    t = AsyncMock()
    t.name = transport_name
    t.check_permissions = AsyncMock(return_value=PermissionResult(behavior="allow"))

    async def shell(cmd: str, timeout: int = 30) -> ShellResult:
        # Loose prefix match so parameterisation doesn't have to be exact
        for prefix, result in shell_responses.items():
            if cmd.startswith(prefix):
                return result
        return ShellResult(ok=False, exit_code=1, stderr="unhandled in test",
                           duration_ms=0, error_code="ADB_COMMAND_FAILED")

    t.shell = shell

    async def pull(remote: str, local):  # noqa: ANN001, ANN202
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text("stub")
        return ShellResult(ok=True, exit_code=0, stdout="", stderr="", duration_ms=5)

    t.pull = pull
    return t


@pytest.mark.asyncio
async def test_anr_pull_empty(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    t = _mk_transport({
        "ls /data/anr": ShellResult(ok=True, exit_code=0, stdout="", stderr="", duration_ms=1),
    })
    r = await anr_pull(t, device="abc")
    assert r.ok
    assert r.data is not None
    assert r.data.count == 0


@pytest.mark.asyncio
async def test_anr_pull_several(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    t = _mk_transport({
        "ls /data/anr": ShellResult(
            ok=True, exit_code=0,
            stdout="anr_1.txt anr_2.txt\n", stderr="", duration_ms=1,
        ),
    })
    r = await anr_pull(t, device="abc")
    assert r.ok
    assert r.data is not None
    assert r.data.count == 2


@pytest.mark.asyncio
async def test_devinfo_happy() -> None:
    props_output = (
        "[ro.product.model]: [Pixel 7]\n"
        "[ro.product.brand]: [google]\n"
        "[ro.product.manufacturer]: [Google]\n"
        "[ro.build.version.sdk]: [33]\n"
        "[ro.build.version.release]: [13]\n"
        "[ro.build.fingerprint]: [google/panther/...]\n"
        "[ro.product.cpu.abi]: [arm64-v8a]\n"
        "[ro.hardware]: [panther]\n"
        "[ro.serialno]: [ABC123]\n"
    )
    battery_out = "Current Battery Service state:\n  level: 82\n  scale: 100\n"
    uptime = "12345.67 123.4\n"
    df = (
        "Filesystem     1K-blocks   Used Available Use% Mounted on\n"
        "/dev/block/x   1000000   500000   500000   50% /data\n"
    )
    t = _mk_transport({
        "getprop": ShellResult(ok=True, exit_code=0, stdout=props_output, stderr="", duration_ms=1),
        "dumpsys battery": ShellResult(ok=True, exit_code=0, stdout=battery_out, stderr="", duration_ms=1),
        "cat /proc/uptime": ShellResult(ok=True, exit_code=0, stdout=uptime, stderr="", duration_ms=1),
        "df /data": ShellResult(ok=True, exit_code=0, stdout=df, stderr="", duration_ms=1),
    })
    r = await devinfo(t)
    assert r.ok
    assert r.data is not None
    assert r.data.model == "Pixel 7"
    assert r.data.sdk == "33"
    assert r.data.battery_level == 82
    assert r.data.uptime_sec == 12345
    assert "/data" in r.data.storage
