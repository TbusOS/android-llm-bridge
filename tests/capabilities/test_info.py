"""Tests for the info capability (system / cpu / memory / storage / network / battery)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from alb.capabilities.info import (
    _build_core,
    _build_zone,
    _count_processors,
    _detect_gpu_vendor,
    _extract_default_route,
    _extract_refresh_rate,
    _extract_rotation,
    _parse_by_name_listing,
    _parse_cpu_freq_dump,
    _parse_cpuinfo_head,
    _parse_df_k,
    _parse_dumpsys_battery,
    _parse_getprop,
    _parse_ip_addr,
    _parse_meminfo,
    _parse_mounts_for_fstype,
    _parse_pm_list,
    _parse_proc_partitions,
    _parse_rss,
    _parse_soc_props,
    _parse_thermal_zones,
    _parse_toybox_top,
    _parse_wm_density,
    _parse_wm_size,
    _pick_gpu_devfreq,
    _sniff_ufs_spec,
    all_info,
    battery,
    cpu,
    display,
    gpu,
    memory,
    network,
    packages,
    panel_names,
    processes,
    security,
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


def test_parse_by_name_listing_skips_self_link() -> None:
    # Some boards ship `/dev/block/by-name/mmcblk0 -> /dev/block/mmcblk0`.
    # That's meaningless — don't tag the whole disk with a by_name label.
    s = (
        "lrwxrwxrwx 1 root root 18 2000-01-15 mmcblk0 -> /dev/block/mmcblk0\n"
        "lrwxrwxrwx 1 root root 21 2000-01-15 boot_a -> /dev/block/mmcblk0p11\n"
    )
    d = _parse_by_name_listing(s)
    assert "mmcblk0" not in d
    assert d["mmcblk0p11"] == "boot_a"


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


def test_parse_proc_partitions_filters_ram_loop_zram() -> None:
    s = (
        "major minor  #blocks  name\n"
        "   1        0      8192 ram0\n"
        "   1        7      8192 ram7\n"
        "   7        0     32768 loop0\n"
        " 253        0   4050000 zram0\n"
        "   8        0  62500000 mmcblk0\n"
        "   8       11    107520 mmcblk0p11\n"
    )
    # Default: noise filtered
    out = _parse_proc_partitions(s, {})
    names = [p.name for p in out]
    assert names == ["mmcblk0", "mmcblk0p11"]

    # Opt in for full list
    full = _parse_proc_partitions(s, {}, include_virtual=True)
    full_names = [p.name for p in full]
    assert "ram0" in full_names
    assert "loop0" in full_names
    assert "zram0" in full_names


def test_parse_soc_props_ordering() -> None:
    s = "SOC_X100\nGenericVendor\ngeneric-board\n"
    assert _parse_soc_props(s) == ("SOC_X100", "GenericVendor")


def test_parse_soc_props_fallback_to_board_platform() -> None:
    # ro.soc.model empty → fall back to ro.board.platform
    s = "\nGenericVendor\nboard_xyz\n"
    assert _parse_soc_props(s) == ("board_xyz", "GenericVendor")


def test_parse_soc_props_all_empty() -> None:
    assert _parse_soc_props("\n\n\n") == ("", "")


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
        "ro.soc.model": ShellResult(
            ok=True, stdout="SOC_X100\nAcmeCorp\nsoc_x100\n",
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
    assert r.data.soc_model == "SOC_X100"
    assert r.data.soc_manufacturer == "AcmeCorp"
    assert len(r.data.cores) == 1
    assert r.data.cores[0].freq_khz_current == 1800000
    assert len(r.data.thermal_zones) == 1
    assert r.data.thermal_zones[0].temp_c == 52.1


@pytest.mark.asyncio
async def test_cpu_aarch64_no_hardware_line_but_getprop_has_soc() -> None:
    # Simulates real Android aarch64 where /proc/cpuinfo has no Hardware/
    # model name — only CPU implementer/part numerics. model is "" but
    # soc_model comes back filled from getprop.
    t = _mk_transport({
        "/proc/cpuinfo": ShellResult(
            ok=True,
            stdout=(
                "processor\t: 0\nBogoMIPS\t: 2000.00\n"
                "Features\t: fp asimd aes\nCPU implementer\t: 0x41\n"
            ),
        ),
        "ro.soc.model": ShellResult(
            ok=True, stdout="AcmeSOC-9000\nAcmeCorp\nacmeboard\n",
        ),
    })
    r = await cpu(t)
    assert r.ok
    assert r.data.model == ""
    assert r.data.soc_model == "AcmeSOC-9000"
    assert r.data.soc_manufacturer == "AcmeCorp"


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
async def test_battery_no_physical_battery() -> None:
    # Dev board: dumpsys responds but every field is zero / present=false.
    t = _mk_transport({
        "dumpsys battery": ShellResult(
            ok=True,
            stdout="present: false\nlevel: 0\nvoltage: 0\nstatus: 1\n",
        ),
    })
    r = await battery(t)
    assert not r.ok
    assert r.error is not None
    assert r.error.code == "NO_BATTERY"


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


@pytest.mark.asyncio
async def test_network_dns_fallback_to_resolv_conf() -> None:
    # Android 10+ no longer exposes DNS via getprop — should fall back
    # to /etc/resolv.conf.
    t = _mk_transport({
        "ip -o addr": ShellResult(ok=True, stdout=""),
        "ip -o link": ShellResult(ok=True, stdout=""),
        "ip route": ShellResult(ok=True, stdout=""),
        "net.dns1": ShellResult(ok=True, stdout="\n\n"),  # empty
        "/etc/resolv.conf": ShellResult(
            ok=True,
            stdout="# auto-generated\nnameserver 10.0.0.1\nnameserver 10.0.0.2\n",
        ),
    })
    r = await network(t)
    assert r.ok
    assert "10.0.0.1" in r.data.dns
    assert "10.0.0.2" in r.data.dns


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
    # The 6 core panels wired in part 1 must all be present; additional
    # part-2 panels (gpu, security, display, packages, processes) may be
    # ok or fail depending on whether their probes hit stub coverage.
    assert {"system", "cpu", "memory", "storage", "network", "battery"} <= set(
        results.keys()
    )
    for name in ("system", "cpu", "memory", "storage", "network"):
        assert results[name].ok, f"{name}: {results[name].error}"


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
    assert "gpu" in names
    assert "security" in names
    assert "display" in names
    assert "packages" in names
    assert "processes" in names
    # Ordering: whatever it is, has to be deterministic across calls
    assert panel_names() == names


# ─── GPU parser tests ─────────────────────────────────────────────


def test_pick_gpu_devfreq_by_name() -> None:
    dump = (
        "/sys/class/devfreq/dmc:\n"
        "dmc\n600000000\n800000000\n300000000\nsimple_ondemand\n"
        "/sys/class/devfreq/fde60000.gpu:\n"
        "mali-g610\n800000000\n1000000000\n200000000\nsimple_ondemand\n"
    )
    entry = _pick_gpu_devfreq(dump)
    assert entry["name"] == "mali-g610"
    assert entry["cur"] == 800000000


def test_pick_gpu_devfreq_by_path() -> None:
    # Fallback: match on path containing 'gpu' when name is generic
    dump = (
        "/sys/class/devfreq/fde60000.gpu:\n"
        "\n500000000\n800000000\n100000000\nondemand\n"
    )
    entry = _pick_gpu_devfreq(dump)
    assert entry["cur"] == 500000000 or entry.get("path", "").endswith(".gpu")


def test_pick_gpu_devfreq_no_gpu() -> None:
    dump = (
        "/sys/class/devfreq/dmc:\n"
        "dmc\n600000000\n800000000\n300000000\nsimple_ondemand\n"
    )
    assert _pick_gpu_devfreq(dump) == {}


def test_detect_gpu_vendor() -> None:
    assert _detect_gpu_vendor("mali-g610") == "arm"
    assert _detect_gpu_vendor("Adreno 620") == "qualcomm"
    assert _detect_gpu_vendor("PowerVR Rogue") == "imagination"
    assert _detect_gpu_vendor("Unknown") == ""


# ─── Display parser tests ────────────────────────────────────────


def test_parse_wm_size() -> None:
    out = "Physical size: 1080x2400\nOverride size: 1080x2340\n"
    assert _parse_wm_size(out) == (1080, 2400)


def test_parse_wm_size_empty() -> None:
    assert _parse_wm_size("") == (0, 0)


def test_parse_wm_density() -> None:
    assert _parse_wm_density("Physical density: 420\nOverride density: 480\n") == (420, 480)


def test_parse_wm_density_no_override() -> None:
    assert _parse_wm_density("Physical density: 320\n") == (320, 0)


def test_extract_refresh_rate() -> None:
    stdout = "Display 0: ...\n  mRefreshRate=60.000004\n"
    assert _extract_refresh_rate(stdout) == 60.0


def test_extract_refresh_rate_fps_variant() -> None:
    assert _extract_refresh_rate("  fps=120.0  ") == 120.0


def test_extract_rotation() -> None:
    assert _extract_rotation("  mRotation=1\n") == 1


# ─── Packages parser tests ───────────────────────────────────────


def test_parse_pm_list_basic() -> None:
    s = "package:com.android.launcher3\npackage:com.google.gms\n"
    assert _parse_pm_list(s) == ["com.android.launcher3", "com.google.gms"]


def test_parse_pm_list_ignores_junk() -> None:
    s = "some header\npackage:com.a\nblank line\npackage:com.b\n"
    assert _parse_pm_list(s) == ["com.a", "com.b"]


# ─── Processes parser tests ──────────────────────────────────────


_TOYBOX_TOP_SAMPLE = """top - 10:14:02, 1 users, load 0.50 0.30 0.20
Tasks:   500 total,   1 running
Mem: 8192M total, 4096M used, 4096M free
  PID USER     PR  NI  VIRT  RES SHR S %CPU %MEM TIME+ CMD
 1234 system   20   0 2000M 250M 100M S 15.0  3.0 01:23 system_server
 5678 u0_a42   20   0 1500M 200M  80M S  8.5  2.5 00:45 com.android.launcher3
 9012 root     20   0  800M 150M  50M S  4.0  1.8 00:10 surfaceflinger
"""


def test_parse_toybox_top_basic() -> None:
    entries = _parse_toybox_top(_TOYBOX_TOP_SAMPLE)
    assert len(entries) == 3
    assert entries[0].pid == 1234
    assert entries[0].user == "system"
    assert entries[0].cpu_pct == 15.0
    assert entries[0].mem_pct == 3.0
    assert entries[0].name == "system_server"


def test_parse_toybox_top_no_header() -> None:
    assert _parse_toybox_top("just some junk\n") == []


def test_parse_rss_suffixes() -> None:
    assert _parse_rss("250M") == 250 * 1024
    assert _parse_rss("2G") == 2 * 1024 * 1024
    assert _parse_rss("512K") == 512
    assert _parse_rss("12345") == 12345
    assert _parse_rss("") == 0
    assert _parse_rss("garbage") == 0


# ─── Integration tests for new panels ────────────────────────────


@pytest.mark.asyncio
async def test_gpu_happy_path() -> None:
    dump = (
        "/sys/class/devfreq/fde60000.gpu:\n"
        "mali-g610\n800000000\n1000000000\n200000000\nsimple_ondemand\n"
    )
    t = _mk_transport({
        "devfreq": ShellResult(ok=True, stdout=dump),
        "gpu_utilization": ShellResult(ok=True, stdout="37\n"),
        "SurfaceFlinger": ShellResult(ok=True, stdout="GLES: ARM, Mali-G610, OpenGL ES 3.2\n"),
    })
    r = await gpu(t)
    assert r.ok
    assert r.data.name == "mali-g610"
    assert r.data.vendor == "arm"
    assert r.data.freq_hz_current == 800000000
    assert r.data.freq_hz_max == 1000000000
    assert r.data.util_pct == 37
    assert "Mali-G610" in r.data.renderer


@pytest.mark.asyncio
async def test_gpu_no_devfreq_gpu_entry() -> None:
    t = _mk_transport({
        "devfreq": ShellResult(
            ok=True,
            stdout="/sys/class/devfreq/dmc:\ndmc\n600000000\n800000000\n300000000\nsimple_ondemand\n",
        ),
    })
    r = await gpu(t)
    assert r.ok
    assert r.data.name == ""
    assert r.data.freq_hz_current == 0


@pytest.mark.asyncio
async def test_security_happy_path() -> None:
    props = (
        "[ro.boot.verifiedbootstate]: [green]\n"
        "[ro.boot.avb_version]: [1.2]\n"
        "[ro.boot.veritymode]: [enforcing]\n"
        "[ro.crypto.state]: [encrypted]\n"
        "[ro.crypto.type]: [file]\n"
        "[sys.oem_unlock_allowed]: [0]\n"
        "[ro.oem_unlock_supported]: [1]\n"
        "[ro.adb.secure]: [1]\n"
    )
    t = _mk_transport({
        "getprop": ShellResult(ok=True, stdout=props),
        "getenforce": ShellResult(ok=True, stdout="Enforcing\n"),
        "policyvers": ShellResult(ok=True, stdout="33\n"),
    })
    r = await security(t)
    assert r.ok
    assert r.data.verified_boot_state == "green"
    assert r.data.avb_version == "1.2"
    assert r.data.verity_mode == "enforcing"
    assert r.data.crypto_state == "encrypted"
    assert r.data.crypto_type == "file"
    assert r.data.selinux_mode == "enforcing"
    assert r.data.selinux_policy_version == "33"
    assert r.data.oem_unlock_allowed is False
    assert r.data.oem_unlock_supported is True
    assert r.data.adb_secure is True


@pytest.mark.asyncio
async def test_security_getprop_empty() -> None:
    t = _mk_transport({})
    r = await security(t)
    assert not r.ok
    assert r.error.code == "ADB_COMMAND_FAILED"


@pytest.mark.asyncio
async def test_display_happy_path() -> None:
    t = _mk_transport({
        "wm size": ShellResult(ok=True, stdout="Physical size: 1080x2400\n"),
        "wm density": ShellResult(ok=True, stdout="Physical density: 420\n"),
        "dumpsys display": ShellResult(
            ok=True,
            stdout="Display 0:\n  mRefreshRate=60.000004\n  mRotation=0\n",
        ),
        "screen_brightness": ShellResult(ok=True, stdout="128\n"),
    })
    r = await display(t)
    assert r.ok
    assert r.data.width == 1080
    assert r.data.height == 2400
    assert r.data.density == 420
    assert r.data.refresh_rate_hz == 60.0
    assert r.data.brightness == 128
    assert r.data.rotation == 0


@pytest.mark.asyncio
async def test_display_brightness_null() -> None:
    t = _mk_transport({
        "wm size": ShellResult(ok=True, stdout="Physical size: 720x1280\n"),
        "wm density": ShellResult(ok=True, stdout="Physical density: 320\n"),
        "dumpsys display": ShellResult(ok=True, stdout=""),
        "screen_brightness": ShellResult(ok=True, stdout="null\n"),
    })
    r = await display(t)
    assert r.ok
    assert r.data.brightness == -1


@pytest.mark.asyncio
async def test_packages_happy_path() -> None:
    sys_out = "package:com.android.settings\npackage:com.android.launcher3\n"
    user_out = "package:com.example.app\n"
    dis_out = ""
    t = _mk_transport({
        "pm list packages -s": ShellResult(ok=True, stdout=sys_out),
        "pm list packages -3": ShellResult(ok=True, stdout=user_out),
        "pm list packages -d": ShellResult(ok=True, stdout=dis_out),
    })
    r = await packages(t)
    assert r.ok
    assert r.data.system_count == 2
    assert r.data.user_count == 1
    assert r.data.total == 3
    assert "com.android.settings" in r.data.system_samples


@pytest.mark.asyncio
async def test_packages_pm_unavailable() -> None:
    t = _mk_transport({})
    r = await packages(t)
    assert not r.ok
    assert r.error.code == "PM_UNAVAILABLE"


@pytest.mark.asyncio
async def test_processes_happy_path() -> None:
    t = _mk_transport({
        "top -n 1 -b -m 30 -q": ShellResult(ok=True, stdout=_TOYBOX_TOP_SAMPLE),
        "ps -A": ShellResult(ok=True, stdout="500\n"),
    })
    r = await processes(t, limit=15)
    assert r.ok
    assert r.data.count == 499  # 500 - header
    assert len(r.data.top_cpu) == 3
    assert r.data.top_cpu[0].name == "system_server"
    assert r.data.top_cpu[0].cpu_pct == 15.0
    # top_mem should be sorted by RSS
    assert r.data.top_mem[0].pid == 1234  # RES=250M highest


@pytest.mark.asyncio
async def test_processes_top_unavailable_still_counts() -> None:
    t = _mk_transport({
        "ps -A": ShellResult(ok=True, stdout="42\n"),
    })
    r = await processes(t)
    assert r.ok
    assert r.data.count == 41
    assert r.data.top_cpu == []
