"""Tests for diagnose capability."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from alb.capabilities.diagnose import (
    _count_cpu_cores,
    _parse_bugreportz_output,
    _parse_getprop,
    _parse_meminfo,
    _parse_wm_density,
    _parse_wm_size,
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
    # Extras present even when extra collectors fail (DEBT-022 PR-A
    # fallback contract): keys exist with default values.
    assert r.data.extras["soc"] == ""
    assert r.data.extras["cpu_cores"] == 0
    assert r.data.extras["cpu_max_khz"] == 0
    assert r.data.extras["ram_total_kb"] == 0
    assert r.data.extras["temp_c"] == -1.0
    assert r.data.extras["display"] == {}


# ─── Extra collector parsers (DEBT-022 PR-A) ───────────────────────
def test_count_cpu_cores() -> None:
    cpuinfo = (
        "processor\t: 0\nBogoMIPS\t: 38\n\n"
        "processor\t: 1\nBogoMIPS\t: 38\n\n"
        "processor\t: 2\nBogoMIPS\t: 38\n\n"
    )
    assert _count_cpu_cores(cpuinfo) == 3
    assert _count_cpu_cores("") == 0


def test_parse_meminfo() -> None:
    mem = (
        "MemTotal:        7929164 kB\n"
        "MemFree:         3000000 kB\n"
        "MemAvailable:    5500000 kB\n"
        "Buffers:           50000 kB\n"
    )
    total, avail = _parse_meminfo(mem)
    assert total == 7929164
    assert avail == 5500000


def test_parse_meminfo_empty() -> None:
    assert _parse_meminfo("") == (0, 0)


def test_parse_wm_size() -> None:
    out = "Physical size: 1080x2400\nOverride size: 720x1600\n"
    assert _parse_wm_size(out) == "1080x2400"
    assert _parse_wm_size("") == ""


def test_parse_wm_density() -> None:
    out = "Physical density: 420\nOverride density: 320\n"
    assert _parse_wm_density(out) == "420"
    assert _parse_wm_density("") == ""


@pytest.mark.asyncio
async def test_devinfo_extras_full() -> None:
    """Happy path with all extra collectors returning data."""
    props_output = (
        "[ro.product.model]: [Pixel 7]\n"
        "[ro.boot.soc.product]: [Tensor G2]\n"
        "[ro.product.cpu.abi]: [arm64-v8a]\n"
    )
    cpuinfo = (
        "processor\t: 0\n\nprocessor\t: 1\n\n"
        "processor\t: 2\n\nprocessor\t: 3\n\n"
    )
    meminfo = "MemTotal:    7929164 kB\nMemAvailable:  5500000 kB\n"
    wm_size = "Physical size: 1080x2400\n"
    wm_density = "Physical density: 420\n"
    thermal = "47350\n"  # millicelsius → 47.35 C
    cpufreq = "2802000\n"  # max 2.802 GHz

    t = _mk_transport({
        "getprop": ShellResult(ok=True, exit_code=0, stdout=props_output, stderr="", duration_ms=1),
        "dumpsys battery": ShellResult(ok=False, exit_code=1, stderr="", duration_ms=1),
        "cat /proc/uptime": ShellResult(ok=False, exit_code=1, stderr="", duration_ms=1),
        "df /data": ShellResult(ok=False, exit_code=1, stderr="", duration_ms=1),
        "cat /proc/cpuinfo": ShellResult(ok=True, exit_code=0, stdout=cpuinfo, stderr="", duration_ms=1),
        "cat /sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq": ShellResult(
            ok=True, exit_code=0, stdout=cpufreq, stderr="", duration_ms=1),
        "cat /proc/meminfo": ShellResult(ok=True, exit_code=0, stdout=meminfo, stderr="", duration_ms=1),
        "wm size": ShellResult(ok=True, exit_code=0, stdout=wm_size, stderr="", duration_ms=1),
        "wm density": ShellResult(ok=True, exit_code=0, stdout=wm_density, stderr="", duration_ms=1),
        "cat /sys/class/thermal/thermal_zone0/temp": ShellResult(
            ok=True, exit_code=0, stdout=thermal, stderr="", duration_ms=1),
    })
    r = await devinfo(t)
    assert r.ok
    assert r.data is not None
    assert r.data.extras["soc"] == "Tensor G2"
    assert r.data.extras["cpu_cores"] == 4
    assert r.data.extras["cpu_max_khz"] == 2802000
    assert r.data.extras["ram_total_kb"] == 7929164
    assert r.data.extras["ram_avail_kb"] == 5500000
    assert r.data.extras["display"] == {"size": "1080x2400", "density": "420"}
    assert r.data.extras["temp_c"] == pytest.approx(47.35)


@pytest.mark.asyncio
async def test_devinfo_soc_fallback_chain() -> None:
    """SoC field falls through ro.boot.soc.product → ro.hardware.chipname → ro.board.platform."""
    props_output = (
        "[ro.product.model]: [Test]\n"
        "[ro.hardware.chipname]: [SM8650]\n"
    )
    t = _mk_transport({
        "getprop": ShellResult(ok=True, exit_code=0, stdout=props_output, stderr="", duration_ms=1),
    })
    r = await devinfo(t)
    assert r.ok
    assert r.data.extras["soc"] == "SM8650"
