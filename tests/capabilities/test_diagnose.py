"""Tests for diagnose capability."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from pathlib import Path

from alb.capabilities.diagnose import (
    _count_cpu_cores,
    _parse_bugreportz_output,
    _parse_getprop,
    _parse_meminfo,
    _parse_wm_density,
    _parse_wm_size,
    anr_pull,
    bugreport,
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
async def test_anr_pull_basenames_hostile_names(monkeypatch, tmp_path) -> None:
    """round11 SEC-3: a device-supplied ls name with path traversal must be
    reduced to its basename so it can't escape the destination dir."""
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    pulled: list = []
    t = _mk_transport({
        "ls /data/anr": ShellResult(
            ok=True, exit_code=0,
            stdout="../../../etc/evil.txt good.txt\n", stderr="", duration_ms=1,
        ),
    })
    orig_pull = t.pull

    async def pull(remote, local):  # noqa: ANN001, ANN202
        pulled.append(local)
        return await orig_pull(remote, local)

    t.pull = pull
    r = await anr_pull(t, device="abc")
    assert r.ok
    assert pulled, "expected at least one pull"
    # No '..' segment survives → the traversal name was basenamed.
    for p in pulled:
        assert ".." not in p.parts
    assert any(p.name == "evil.txt" for p in pulled)


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
    cpufreq = "soc_id=\n" + "".join(f"cpu{i}=2802000\n" for i in range(4))  # 2.802 GHz

    t = _mk_transport({
        "getprop": ShellResult(ok=True, exit_code=0, stdout=props_output, stderr="", duration_ms=1),
        "dumpsys battery": ShellResult(ok=False, exit_code=1, stderr="", duration_ms=1),
        "cat /proc/uptime": ShellResult(ok=False, exit_code=1, stderr="", duration_ms=1),
        "df /data": ShellResult(ok=False, exit_code=1, stderr="", duration_ms=1),
        "cat /proc/cpuinfo": ShellResult(ok=True, exit_code=0, stdout=cpuinfo, stderr="", duration_ms=1),
        'echo "soc_id=': ShellResult(ok=True, exit_code=0, stdout=cpufreq, stderr="", duration_ms=1),
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


# ─── bugreport --output (parity with capture_uart / logcat / dmesg) ────


@pytest.mark.asyncio
async def test_bugreport_output_as_file_path(monkeypatch, tmp_path: Path) -> None:
    """--output <file>: zip written to that exact file (parent auto-created)."""
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path / "ws"))
    t = _mk_transport({
        "bugreportz": ShellResult(
            ok=True, exit_code=0,
            stdout="OK:/sdcard/bugreports/x.zip\n", stderr="", duration_ms=10,
        ),
    })
    target = tmp_path / "out" / "session-A.zip"
    r = await bugreport(t, device="abc", output=target)
    assert r.ok
    assert r.data is not None
    art = Path(r.artifacts[0])
    assert art == target
    assert art.exists()


@pytest.mark.asyncio
async def test_bugreport_output_as_directory(monkeypatch, tmp_path: Path) -> None:
    """--output <dir>/: trailing slash → auto-create dir + <ts>-bugreport.zip inside."""
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path / "ws"))
    t = _mk_transport({
        "bugreportz": ShellResult(
            ok=True, exit_code=0,
            stdout="OK:/sdcard/bugreports/x.zip\n", stderr="", duration_ms=10,
        ),
    })
    out_dir = tmp_path / "captures"
    r = await bugreport(t, device="abc", output=str(out_dir) + "/")
    assert r.ok
    art = Path(r.artifacts[0])
    assert art.parent == out_dir
    assert art.name.endswith("-bugreport.zip")
    assert art.exists()


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


# ─── chip and CPU layout (2026-11-06) ──────────────────────────────
# The overview said "qcom" and "8 cores · 1.80 GHz" for a board with a named
# chip and two clusters: ro.hardware instead of the chip, cpu0's frequency
# (the slow cluster) instead of the top one. The cpuinfo below is the format a
# 64-bit kernel prints to a shell in 32-bit mode (that board's adb shell);
# the layout lines have the shape _CPU_LAYOUT_CMD printed there (numbers
# changed).

_BIG_LITTLE_CPUINFO = "".join(
    f"processor\t: {i}\n"
    "model name\t: ARMv8 Processor rev 4 (v8l)\n"
    "Features\t: half thumb fastmult vfp edsp neon vfpv3 tls vfpv4 idiva idivt lpae\n"
    "CPU implementer\t: 0x51\n"
    "CPU architecture: 8\n"
    "CPU variant\t: 0xa\n"
    f"CPU part\t: {'0x801' if i < 4 else '0x800'}\n"
    f"CPU revision\t: {4 if i < 4 else 2}\n\n"
    for i in range(8)
)
_BIG_LITTLE_LAYOUT = "soc_id=123\n" + "".join(
    f"cpu{i}={1800000 if i < 4 else 2000000}\n" for i in range(8)
)
_CHIP_PROPS = (
    "[ro.product.model]: [Board X]\n"
    "[ro.hardware]: [qcom]\n"
    "[ro.board.platform]: [platform-x]\n"
    "[ro.soc.manufacturer]: [QTI]\n"
    "[ro.soc.model]: [SM0000]\n"
    "[ro.product.cpu.abi]: [armeabi-v7a]\n"
    "[ro.product.cpu.abilist]: [armeabi-v7a,armeabi]\n"
    "[ro.product.cpu.abilist64]: []\n"
)


def _ok(stdout: str) -> ShellResult:
    return ShellResult(ok=True, exit_code=0, stdout=stdout, stderr="", duration_ms=1)


@pytest.mark.asyncio
async def test_devinfo_names_the_chip_and_each_cluster() -> None:
    t = _mk_transport({
        "getprop": _ok(_CHIP_PROPS),
        "cat /proc/cpuinfo": _ok(_BIG_LITTLE_CPUINFO),
        'echo "soc_id=': _ok(_BIG_LITTLE_LAYOUT),
    })
    r = await devinfo(t)
    assert r.ok and r.data is not None
    x = r.data.extras
    assert (x["soc_manufacturer"], x["soc_model"], x["board_platform"], x["soc_id"]) == (
        "QTI", "SM0000", "platform-x", "123",
    )
    assert x["cpu_max_khz"] == 2000000, "the big cluster, not cpu0"
    assert x["cpu_arch"] == "ARMv8"
    assert x["cpu_clusters"] == [
        {"count": 4, "cpus": [0, 1, 2, 3], "core": "Kryo 2xx Silver",
         "part": "0x51/0x801", "max_khz": 1800000},
        {"count": 4, "cpus": [4, 5, 6, 7], "core": "Kryo 2xx Gold",
         "part": "0x51/0x800", "max_khz": 2000000},
    ]
    assert (x["abi_list"], x["abi_list64"]) == ("armeabi-v7a,armeabi", "")


def test_cpu_layout_ignores_an_echoed_command_and_empty_values() -> None:
    """A serial console echoes the command line back; cores without cpufreq
    print `cpuN=`."""
    from alb.capabilities.diagnose import _CPU_LAYOUT_CMD, _parse_cpu_layout

    # No soc_id line after the echo (a board without soc0 prints `soc_id=`, a
    # cut-off capture may print nothing): the echo's own "soc_id=$(cat" must
    # not be read as the answer.
    out = f"console:/ $ {_CPU_LAYOUT_CMD}\ncpu0=1000\ncpu1=\n"
    assert _parse_cpu_layout(out) == {"soc_id": "", "max_khz": {0: 1000}}


def test_an_unknown_core_is_shown_by_its_numbers() -> None:
    from alb.capabilities.diagnose import _cpu_clusters, _parse_cpuinfo_cores

    cores = _parse_cpuinfo_cores(
        "processor\t: 0\nCPU implementer\t: 0x41\nCPU part\t: 0xd03\n\n"
        "processor\t: 1\nCPU implementer\t: 0x99\nCPU part\t: 0x123\n"
    )
    groups = _cpu_clusters(cores, {0: 1000, 1: 2000})
    assert [(g["core"], g["part"]) for g in groups] == [
        ("Cortex-A53", "0x41/0xd03"), ("", "0x99/0x123"),
    ]


def test_no_clusters_when_nothing_tells_cores_apart() -> None:
    """x86 emulators print neither part numbers nor, often, cpufreq."""
    from alb.capabilities.diagnose import _cpu_clusters, _parse_cpuinfo_cores

    cores = _parse_cpuinfo_cores("processor\t: 0\nvendor_id\t: GenuineIntel\n")
    assert _cpu_clusters(cores, {}) == []
