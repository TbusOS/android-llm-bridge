"""Tests for the info capability (system / cpu / memory / storage / network / battery)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from alb.capabilities.info import (
    _build_core,
    _build_zone,
    _count_processors,
    _extract_default_route,
    _parse_by_name_listing,
    _parse_cpu_freq_dump,
    _parse_cpuinfo_head,
    _parse_df_k,
    _parse_dumpsys_battery,
    _parse_getprop,
    _parse_ip_addr,
    _parse_meminfo,
    _parse_mounts_for_fstype,
    _parse_proc_partitions,
    _parse_thermal_zones,
    _sniff_ufs_spec,
    all_info,
    battery,
    cpu,
    memory,
    network,
    panel_names,
    storage,
    system,
)
from alb.transport.base import ShellResult


# ─── Getprop + meminfo parsers ────────────────────────────────────


def test_parse_getprop_basic() -> None:
    s = "[ro.product.model]: [Pixel 9]\n[ro.build.version.sdk]: [34]\n"
    d = _parse_getprop(s)
    assert d["ro.product.model"] == "Pixel 9"
    assert d["ro.build.version.sdk"] == "34"


def test_parse_getprop_empty_value() -> None:
    assert _parse_getprop("[persist.x]: []\n")["persist.x"] == ""


def test_parse_meminfo_basic() -> None:
    s = "MemTotal:        7892 kB\nMemFree:          621 kB\nBuffers:          142 kB\n"
    d = _parse_meminfo(s)
    assert d == {"MemTotal": 7892, "MemFree": 621, "Buffers": 142}


def test_parse_meminfo_ignores_non_numeric() -> None:
    # Some lines in /proc/meminfo don't fit the 'NN kB' pattern (e.g. HugePages_Rsvd)
    s = "MemTotal: 100 kB\nHugePages_Rsvd:  some text\n"
    d = _parse_meminfo(s)
    assert d == {"MemTotal": 100}


# ─── CPU parsers ──────────────────────────────────────────────────


def test_count_processors_basic() -> None:
    s = "processor\t: 0\nprocessor\t: 1\nprocessor\t: 2\n"
    assert _count_processors(s) == 3


def test_parse_cpuinfo_head_model_and_features() -> None:
    s = (
        "processor\t: 0\n"
        "Hardware\t: Generic ARM Platform\n"
        "Features\t: fp asimd aes pmull sha1 sha2\n"
    )
    model, feats = _parse_cpuinfo_head(s)
    assert model == "Generic ARM Platform"
    assert "asimd" in feats and "aes" in feats


def test_parse_cpu_freq_dump_basic() -> None:
    s = (
        "/sys/devices/system/cpu/cpu0/cpufreq:\n"
        "1800000\n2200000\n408000\nschedutil\n"
        "/sys/devices/system/cpu/cpu1/cpufreq:\n"
        "2200000\n2200000\n408000\nschedutil\n"
    )
    cores = _parse_cpu_freq_dump(s, n_cores=2)
    assert len(cores) == 2
    assert cores[0].index == 0
    assert cores[0].freq_khz_current == 1800000
    assert cores[0].freq_khz_max == 2200000
    assert cores[0].freq_khz_min == 408000
    assert cores[0].governor == "schedutil"


def test_parse_cpu_freq_dump_fallback_when_locked() -> None:
    # Empty sysfs (locked down on prod devices): fallback to placeholders.
    cores = _parse_cpu_freq_dump("", n_cores=4)
    assert len(cores) == 4
    assert all(c.freq_khz_current == 0 for c in cores)
    assert all(c.governor == "" for c in cores)


def test_build_core_out_of_order_values() -> None:
    # If kernel changes the order, _build_core should still handle cleanly.
    c = _build_core("/sys/devices/system/cpu/cpu3/cpufreq", ["performance"])
    assert c.index == 3
    assert c.governor == "performance"
    assert c.freq_khz_current == 0


def test_parse_thermal_zones_basic() -> None:
    s = (
        "/sys/class/thermal/thermal_zone0:\n"
        "cpu-big\n52100\n"
        "/sys/class/thermal/thermal_zone1:\n"
        "cpu-little\n49300\n"
    )
    zones = _parse_thermal_zones(s)
    assert len(zones) == 2
    assert zones[0].name == "thermal_zone0"
    assert zones[0].type == "cpu-big"
    assert zones[0].temp_c == 52.1
    assert zones[1].temp_c == 49.3


def test_build_zone_missing_temp() -> None:
    z = _build_zone("/sys/class/thermal/thermal_zone5", ["battery"])
    assert z.type == "battery"
    assert z.temp_c == 0.0


# ─── Storage parsers ──────────────────────────────────────────────


def test_parse_mounts_fstype() -> None:
    s = "/dev/root / erofs ro,noatime 0 0\n/dev/block/dm-6 /data f2fs rw 0 0\n"
    d = _parse_mounts_for_fstype(s)
    assert d["/"] == "erofs"
    assert d["/data"] == "f2fs"


def test_parse_df_k_basic() -> None:
    s = (
        "Filesystem 1K-blocks Used Available Use% Mounted on\n"
        "/dev/block/dm-6 103000000 47000000 56000000 46% /data\n"
        "/dev/root 4194304 3600000 594304 86% /\n"
    )
    fs_map = {"/data": "f2fs", "/": "erofs"}
    out = _parse_df_k(s, fs_map)
    assert len(out) == 2
    assert out[0].mount == "/data"
    assert out[0].fstype == "f2fs"
    assert out[0].size_kb == 103000000
    assert out[0].use_pct == 46


def test_parse_by_name_listing() -> None:
    s = (
        "total 0\n"
        "lrwxrwxrwx 1 root root 16 2024-01-01 01:00 boot_a -> /dev/block/sda5\n"
        "lrwxrwxrwx 1 root root 16 2024-01-01 01:00 system_a -> /dev/block/sda17\n"
    )
    d = _parse_by_name_listing(s)
    assert d["sda5"] == "boot_a"
    assert d["sda17"] == "system_a"


def test_parse_proc_partitions_basic() -> None:
    s = (
        "major minor  #blocks  name\n"
        "   8        0  62500000 sda\n"
        "   8        5     65536 sda5\n"
    )
    by_name = {"sda5": "boot_a"}
    out = _parse_proc_partitions(s, by_name)
    assert len(out) == 2
    assert out[0].name == "sda"
    assert out[0].size_kb == 62500000
    assert out[1].by_name == "boot_a"


def test_sniff_ufs_spec_detection() -> None:
    assert _sniff_ufs_spec("[    0.123456] ufs-qcom: UFS3.1 detected") == "UFS 3.1"
    assert _sniff_ufs_spec("mmc0: new eMMC card") == "eMMC"
    assert _sniff_ufs_spec("hello world") == ""


# ─── Network parsers ──────────────────────────────────────────────


def test_parse_ip_addr_basic() -> None:
    addr_out = (
        "1: lo    inet 127.0.0.1/8 scope host lo\n"
        "2: wlan0    inet 192.168.1.42/24 scope global wlan0\n"
        "2: wlan0    inet6 fe80::1/64 scope link\n"
    )
    link_out = (
        "1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 link/loopback 00:00:00:00:00:00\n"
        "2: wlan0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 link/ether aa:bb:cc:dd:ee:ff\n"
    )
    ifs = _parse_ip_addr(addr_out, link_out)
    names = {i.name for i in ifs}
    assert {"lo", "wlan0"}.issubset(names)
    wlan = next(i for i in ifs if i.name == "wlan0")
    assert wlan.state == "up"
    assert wlan.mtu == 1500
    assert wlan.mac == "aa:bb:cc:dd:ee:ff"
    assert "192.168.1.42/24" in wlan.ipv4
    assert "fe80::1/64" in wlan.ipv6


def test_parse_ip_addr_down_interface() -> None:
    link_out = "3: eth0: <BROADCAST,MULTICAST> mtu 1500 link/ether 11:22:33:44:55:66\n"
    ifs = _parse_ip_addr("", link_out)
    eth0 = next(i for i in ifs if i.name == "eth0")
    assert eth0.state == "down"


def test_extract_default_route() -> None:
    s = "default via 192.168.1.1 dev wlan0 proto dhcp\n192.168.1.0/24 dev wlan0\n"
    assert _extract_default_route(s).startswith("default via 192.168.1.1")


# ─── Battery parser ──────────────────────────────────────────────


def test_parse_dumpsys_battery_basic() -> None:
    s = (
        "Current Battery Service state:\n"
        "  AC powered: true\n"
        "  status: 2\n"
        "  health: 2\n"
        "  present: true\n"
        "  level: 74\n"
        "  voltage: 4120\n"
        "  temperature: 382\n"
        "  technology: Li-ion\n"
        "  plugged: 1\n"
        "  current now: 1824000\n"
        "  cycle count: 412\n"
    )
    info = _parse_dumpsys_battery(s)
    assert info.level_pct == 74
    assert info.status == "charging"
    assert info.health == "good"
    assert info.voltage_mv == 4120
    assert info.temperature_c == 38.2
    assert info.plugged == "AC"
    assert info.current_ua == 1824000
    assert info.cycle_count == 412
    assert info.present is True


def test_parse_dumpsys_battery_missing_fields() -> None:
    info = _parse_dumpsys_battery("level: 50\n")
    assert info.level_pct == 50
    assert info.status == ""  # unknown status code
    assert info.present is False


# ─── Fake transport ──────────────────────────────────────────────


def _mk_transport(shell_responses: dict[str, ShellResult]) -> AsyncMock:
    t = AsyncMock()
    t.name = "adb"

    async def shell(cmd: str, timeout: int = 30) -> ShellResult:
        for prefix, result in shell_responses.items():
            if prefix in cmd:
                return result
        # Default: empty ok (so _shell_or_empty returns "")
        return ShellResult(ok=True, exit_code=0, stdout="", duration_ms=0)

    t.shell = shell
    return t


# ─── Integration-level tests per panel ───────────────────────────


@pytest.mark.asyncio
async def test_system_happy_path() -> None:
    props = (
        "[ro.build.version.release]: [14]\n"
        "[ro.build.version.sdk]: [34]\n"
        "[ro.build.type]: [userdebug]\n"
        "[ro.product.model]: [Generic Dev Board]\n"
        "[ro.bootloader]: [u-boot 2023.10]\n"
        "[ro.serialno]: [ABC123XYZ]\n"
    )
    t = _mk_transport({
        "getprop": ShellResult(ok=True, stdout=props),
        "uname -a": ShellResult(ok=True, stdout="Linux board 6.1.75 #1 aarch64"),
        "uname -m": ShellResult(ok=True, stdout="aarch64"),
        "getenforce": ShellResult(ok=True, stdout="Enforcing"),
        "id -u": ShellResult(ok=True, stdout="0"),
    })
    r = await system(t)
    assert r.ok, r.error
    assert r.data.android_release == "14"
    assert r.data.api_level == "34"
    assert r.data.serial == "ABC123XYZ"
    assert r.data.arch == "aarch64"
    assert r.data.selinux == "enforcing"
    assert r.data.adb_root is True


@pytest.mark.asyncio
async def test_system_getprop_fails() -> None:
    t = _mk_transport({
        "getprop": ShellResult(ok=False, stdout="", stderr="offline"),
    })
    r = await system(t)
    assert not r.ok
    assert r.error is not None


@pytest.mark.asyncio
async def test_memory_happy_path() -> None:
    t = _mk_transport({
        "cat /proc/meminfo": ShellResult(
            ok=True,
            stdout="MemTotal: 8000000 kB\nMemFree: 600000 kB\nMemAvailable: 3200000 kB\nSwapTotal: 2048000 kB\nSwapFree: 1636000 kB\n",
        ),
        "/sys/block/zram0/disksize": ShellResult(
            ok=True, stdout="2147483648\n"  # 2 GB in bytes
        ),
    })
    r = await memory(t)
    assert r.ok
    assert r.data.total_kb == 8000000
    assert r.data.available_kb == 3200000
    assert r.data.swap_total_kb == 2048000
    assert r.data.zram_total_kb == 2097152  # 2 GB in KB


@pytest.mark.asyncio
async def test_memory_no_meminfo() -> None:
    t = _mk_transport({})  # all commands return empty stdout
    r = await memory(t)
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "MEMINFO_UNREADABLE"


@pytest.mark.asyncio
async def test_cpu_happy_path() -> None:
    t = _mk_transport({
        "/proc/cpuinfo": ShellResult(
            ok=True,
            stdout="processor\t: 0\nprocessor\t: 1\nHardware\t: Generic\nFeatures\t: fp asimd\n",
        ),
        "cpufreq": ShellResult(
            ok=True,
            stdout="/sys/devices/system/cpu/cpu0/cpufreq:\n1800000\n2200000\n408000\nschedutil\n",
        ),
        "thermal_zone": ShellResult(
            ok=True,
            stdout="/sys/class/thermal/thermal_zone0:\ncpu-big\n52100\n",
        ),
    })
    r = await cpu(t)
    assert r.ok
    assert r.data.processor_count == 2
    assert r.data.model == "Generic"
    assert len(r.data.cores) == 1
    assert r.data.cores[0].freq_khz_current == 1800000
    assert len(r.data.thermal_zones) == 1
    assert r.data.thermal_zones[0].temp_c == 52.1


@pytest.mark.asyncio
async def test_battery_happy_path() -> None:
    t = _mk_transport({
        "dumpsys battery": ShellResult(
            ok=True,
            stdout="level: 74\nstatus: 2\ntemperature: 382\nvoltage: 4120\nplugged: 1\npresent: true\n",
        ),
    })
    r = await battery(t)
    assert r.ok
    assert r.data.level_pct == 74
    assert r.data.status == "charging"
    assert r.data.temperature_c == 38.2


@pytest.mark.asyncio
async def test_battery_no_dumpsys() -> None:
    t = _mk_transport({})
    r = await battery(t)
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "BATTERY_UNAVAILABLE"


@pytest.mark.asyncio
async def test_storage_happy_path() -> None:
    t = _mk_transport({
        "df -k": ShellResult(
            ok=True,
            stdout=(
                "Filesystem 1K-blocks Used Available Use% Mounted on\n"
                "/dev/block/dm-6 103000000 47000000 56000000 46% /data\n"
            ),
        ),
        "/proc/mounts": ShellResult(
            ok=True,
            stdout="/dev/block/dm-6 /data f2fs rw 0 0\n",
        ),
        "/proc/partitions": ShellResult(
            ok=True,
            stdout=(
                "major minor  #blocks  name\n"
                "   8        0  62500000 sda\n"
                "   8        5     65536 sda5\n"
            ),
        ),
        "by-name": ShellResult(
            ok=True,
            stdout="lrwxrwxrwx 1 root root 16 2024-01-01 boot_a -> /dev/block/sda5\n",
        ),
        "dmesg": ShellResult(
            ok=True,
            stdout="[    0.123] UFS3.1 detected\n",
        ),
    })
    r = await storage(t)
    assert r.ok
    assert len(r.data.filesystems) == 1
    assert r.data.filesystems[0].mount == "/data"
    assert r.data.filesystems[0].fstype == "f2fs"
    assert r.data.ufs_spec == "UFS 3.1"
    assert len(r.data.partitions) == 2
    sda5 = next(p for p in r.data.partitions if p.name == "sda5")
    assert sda5.by_name == "boot_a"


@pytest.mark.asyncio
async def test_network_happy_path() -> None:
    t = _mk_transport({
        "ip -o addr": ShellResult(
            ok=True,
            stdout="2: wlan0    inet 192.168.1.42/24 scope global wlan0\n",
        ),
        "ip -o link": ShellResult(
            ok=True,
            stdout="2: wlan0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 link/ether aa:bb:cc:dd:ee:ff\n",
        ),
        "ip route": ShellResult(
            ok=True,
            stdout="default via 192.168.1.1 dev wlan0 proto dhcp\n",
        ),
        "net.dns1": ShellResult(ok=True, stdout="8.8.8.8\n1.1.1.1\n"),
    })
    r = await network(t)
    assert r.ok
    wlan = next(i for i in r.data.interfaces if i.name == "wlan0")
    assert "192.168.1.42/24" in wlan.ipv4
    assert "aa:bb:cc:dd:ee:ff" == wlan.mac
    assert r.data.default_route.startswith("default via 192.168.1.1")
    assert "8.8.8.8" in r.data.dns


# ─── all_info + panel_names ──────────────────────────────────────


@pytest.mark.asyncio
async def test_all_info_runs_each_panel_once() -> None:
    # All panels get a working-enough set of outputs.
    props = "[ro.build.version.release]: [14]\n[ro.build.version.sdk]: [34]\n"
    t = _mk_transport({
        "getprop": ShellResult(ok=True, stdout=props),
        "uname": ShellResult(ok=True, stdout="aarch64"),
        "/proc/meminfo": ShellResult(ok=True, stdout="MemTotal: 100 kB\n"),
        "dumpsys battery": ShellResult(ok=True, stdout="level: 50\nstatus: 2\n"),
        "df -k": ShellResult(
            ok=True,
            stdout="Filesystem 1K-blocks Used Available Use% Mounted on\n/dev/x 100 50 50 50% /data\n",
        ),
        "/proc/partitions": ShellResult(
            ok=True, stdout="major minor  #blocks  name\n8 0 1000 sda\n"
        ),
        "ip -o addr": ShellResult(
            ok=True, stdout="2: wlan0 inet 10.0.0.1/24 scope global wlan0\n"
        ),
        "ip -o link": ShellResult(
            ok=True,
            stdout="2: wlan0: <UP> mtu 1500 link/ether aa:bb:cc:dd:ee:ff\n",
        ),
        "/proc/cpuinfo": ShellResult(ok=True, stdout="processor\t: 0\n"),
    })
    results = await all_info(t)
    assert set(results.keys()) == {
        "system", "cpu", "memory", "storage", "network", "battery",
    }
    # All succeeded with the stub data we provided
    for name, r in results.items():
        assert r.ok, f"{name}: {r.error}"


@pytest.mark.asyncio
async def test_all_info_subset() -> None:
    t = _mk_transport({
        "dumpsys battery": ShellResult(ok=True, stdout="level: 88\n"),
    })
    results = await all_info(t, panels=["battery"])
    assert list(results.keys()) == ["battery"]


@pytest.mark.asyncio
async def test_all_info_unknown_panel_skipped() -> None:
    t = _mk_transport({
        "dumpsys battery": ShellResult(ok=True, stdout="level: 33\n"),
    })
    results = await all_info(t, panels=["battery", "does_not_exist"])
    assert list(results.keys()) == ["battery"]


def test_panel_names_stable() -> None:
    names = panel_names()
    assert "system" in names
    assert "cpu" in names
    assert "battery" in names
    # Ordering: whatever it is, has to be deterministic across calls
    assert panel_names() == names
