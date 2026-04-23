"""Tests for the metrics capability (sampler + streamer + parsers)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from alb.capabilities.metrics import (
    MetricSample,
    MetricSampler,
    MetricsStreamer,
    _consume_thermal,
    _devfreq_block,
    _max_cpu_temp,
    _parse_battery_temp,
    _parse_cpu_jiffies,
    _parse_freq_dump,
    _parse_gpu_devfreq,
    _parse_int_first,
    _parse_meminfo_subset,
    _split_sections,
    _sum_disk_stat,
    _sum_net_dev,
    get_streamer,
    shutdown_all_streamers,
)
from alb.transport.base import ShellResult


# ─── Section splitter ─────────────────────────────────────────────


def test_split_sections_basic() -> None:
    s = "__ALB_STAT__\ncpu  100 200\n__ALB_MEM__\nMemTotal:  8 kB\n"
    out = _split_sections(s)
    assert out["STAT"].strip() == "cpu  100 200"
    assert "MemTotal" in out["MEM"]


def test_split_sections_empty() -> None:
    assert _split_sections("") == {}


# ─── /proc/stat parser ────────────────────────────────────────────


def test_parse_cpu_jiffies_basic() -> None:
    s = "cpu  100 50 30 600 10 0 5\nintr  ...\n"
    idle, total = _parse_cpu_jiffies(s)
    # idle = 600 (idle) + 10 (iowait) = 610
    # total = 100+50+30+600+10+0+5 = 795
    assert idle == 610
    assert total == 795


def test_parse_cpu_jiffies_no_cpu_line() -> None:
    assert _parse_cpu_jiffies("intr  10 20\n") == (0, 0)


# ─── /proc/meminfo subset parser ──────────────────────────────────


def test_parse_meminfo_subset_basic() -> None:
    s = (
        "MemTotal:        8000000 kB\n"
        "MemFree:          600000 kB\n"
        "MemAvailable:    3000000 kB\n"
        "SwapTotal:       4096000 kB\n"
        "SwapFree:        4000000 kB\n"
        "Slab:              50000 kB\n"
    )
    d = _parse_meminfo_subset(s)
    assert d["total"] == 8000000
    assert d["avail"] == 3000000
    assert d["swap_total"] == 4096000
    assert d["swap_free"] == 4000000


# ─── CPU freq parser ──────────────────────────────────────────────


def test_parse_freq_dump_basic() -> None:
    s = "1800000\n2200000\n408000\n"
    assert _parse_freq_dump(s) == [1800000, 2200000, 408000]


def test_parse_freq_dump_skips_garbage() -> None:
    s = "1800000\nN/A\nperformance\n2200000\n"
    assert _parse_freq_dump(s) == [1800000, 2200000]


# ─── Thermal zone parser ──────────────────────────────────────────


def test_max_cpu_temp_picks_highest_silicon_zone() -> None:
    s = (
        "/sys/class/thermal/thermal_zone0:\n"
        "cpu-big\n62100\n"
        "/sys/class/thermal/thermal_zone1:\n"
        "battery\n38000\n"  # excluded
        "/sys/class/thermal/thermal_zone2:\n"
        "cpu-little\n55000\n"
    )
    assert _max_cpu_temp(s) == 62.1


def test_max_cpu_temp_handles_vendor_naming() -> None:
    # Real device used 'soc-thermal' / 'bigcore-thermal' etc. — none
    # contain 'cpu' literally, but they're still silicon temps.
    s = (
        "/sys/class/thermal/thermal_zone0:\nsoc-thermal\n69300\n"
        "/sys/class/thermal/thermal_zone1:\nbigcore-thermal\n70200\n"
        "/sys/class/thermal/thermal_zone2:\nlittle-core-thermal\n71200\n"
        "/sys/class/thermal/thermal_zone3:\ngpu-thermal\n68400\n"
    )
    assert _max_cpu_temp(s) == 71.2


def test_max_cpu_temp_excludes_battery_and_skin() -> None:
    s = (
        "/sys/class/thermal/thermal_zone0:\nbattery\n38200\n"
        "/sys/class/thermal/thermal_zone1:\nskin-therm\n42000\n"
        "/sys/class/thermal/thermal_zone2:\ncharger\n45000\n"
    )
    assert _max_cpu_temp(s) == 0.0


def test_max_cpu_temp_no_zones() -> None:
    assert _max_cpu_temp("") == 0.0


def test_consume_thermal_handles_short_buffer() -> None:
    assert _consume_thermal([], 0.0) == 0.0
    assert _consume_thermal(["cpu-big"], 0.0) == 0.0


# ─── GPU devfreq parser ───────────────────────────────────────────


def test_parse_gpu_devfreq_finds_mali() -> None:
    s = (
        "/sys/class/devfreq/dmc:\n"
        "dmc\n600000000\n"
        "/sys/class/devfreq/27800000.gpu:\n"
        "mali-g52\n800000000\n"
    )
    freq, name = _parse_gpu_devfreq(s)
    assert freq == 800000000
    assert name == "mali-g52"


def test_parse_gpu_devfreq_no_gpu() -> None:
    s = "/sys/class/devfreq/dmc:\ndmc\n600000000\n"
    assert _parse_gpu_devfreq(s) == (0, "")


def test_devfreq_block_returns_none_for_non_match() -> None:
    assert _devfreq_block("/sys/class/devfreq/dmc", ["dmc", "100"], ("gpu",)) is None


# ─── Network and disk diff sources ────────────────────────────────


def test_sum_net_dev_skips_loopback() -> None:
    s = (
        "Inter-|   Receive                                                |  Transmit\n"
        " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n"
        "    lo: 100 1 0 0 0 0 0 0 200 1 0 0 0 0 0 0\n"
        "  eth0: 5000 10 0 0 0 0 0 0 3000 5 0 0 0 0 0 0\n"
        " wlan0: 2000 4 0 0 0 0 0 0 1000 2 0 0 0 0 0 0\n"
    )
    rx, tx = _sum_net_dev(s)
    # eth0 + wlan0 only
    assert rx == 7000
    assert tx == 4000


def test_sum_net_dev_empty() -> None:
    assert _sum_net_dev("") == (0, 0)


def test_sum_disk_stat_basic() -> None:
    # 11 fields: read_io read_merges read_sectors read_ticks
    # write_io write_merges write_sectors write_ticks in_flight io_ticks time_in_queue
    line = "100 0 200 50 80 0 160 30 0 5 5"
    s = line + "\n" + line + "\n"
    read_sec, write_sec = _sum_disk_stat(s)
    assert read_sec == 400
    assert write_sec == 320


# ─── Battery temp ────────────────────────────────────────────────


def test_parse_battery_temp_present() -> None:
    s = "  present: true\n  temperature: 382\n"
    assert _parse_battery_temp(s) == 38.2


def test_parse_battery_temp_not_present() -> None:
    s = "  present: false\n  temperature: 0\n"
    assert _parse_battery_temp(s) == 0.0


# ─── _parse_int_first ────────────────────────────────────────────


def test_parse_int_first() -> None:
    assert _parse_int_first("37%\n") == 37
    assert _parse_int_first("garbage\n  42\n") == 42
    assert _parse_int_first("", default=-1) == -1


# ─── MetricSampler integration ────────────────────────────────────


def _combined_stdout(
    *,
    cpu_line: str = "cpu 100 50 30 600 10 0 5",
    mem_total: int = 8000000,
    mem_avail: int = 3000000,
    freqs: list[int] | None = None,
    therm: str = "/sys/class/thermal/thermal_zone0:\ncpu-big\n55000",
    gpu: str = "/sys/class/devfreq/27800000.gpu:\nmali-g52\n800000000",
    gpu_util: str = "29",
    net_eth_rx: int = 5000,
    net_eth_tx: int = 3000,
    disk_read_sec: int = 200,
    disk_write_sec: int = 160,
    bat_present: bool = True,
    bat_temp: int = 382,
) -> str:
    if freqs is None:
        freqs = [1800000, 2200000]
    freq_block = "\n".join(str(f) for f in freqs)
    bat_block = (
        f"  present: {'true' if bat_present else 'false'}\n"
        f"  temperature: {bat_temp}"
    )
    return (
        f"__ALB_STAT__\n{cpu_line}\nintr 1\n"
        f"__ALB_MEM__\nMemTotal: {mem_total} kB\nMemAvailable: {mem_avail} kB\n"
        f"SwapTotal: 0 kB\nSwapFree: 0 kB\n"
        f"__ALB_NET__\n"
        f"Inter-| junk\n"
        f" face | junk\n"
        f"   lo: 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0\n"
        f"  eth0: {net_eth_rx} 1 0 0 0 0 0 0 {net_eth_tx} 1 0 0 0 0 0 0\n"
        f"__ALB_FREQ__\n{freq_block}\n"
        f"__ALB_THERM__\n{therm}\n"
        f"__ALB_GPU__\n{gpu}\n"
        f"__ALB_GPUUTIL__\n{gpu_util}\n"
        f"__ALB_DISK__\n100 0 {disk_read_sec} 50 80 0 {disk_write_sec} 30 0 5 5\n"
        f"__ALB_BAT__\n{bat_block}\n"
    )


def _mk_transport(stdouts: list[str]) -> AsyncMock:
    """Returns successive shell calls' stdout in order."""
    t = AsyncMock()
    t.name = "adb"
    counter = {"i": 0}

    async def shell(cmd: str, timeout: int = 30) -> ShellResult:
        i = counter["i"]
        counter["i"] += 1
        idx = min(i, len(stdouts) - 1)
        return ShellResult(ok=True, exit_code=0, stdout=stdouts[idx], duration_ms=10)

    t.shell = shell
    return t


@pytest.mark.asyncio
async def test_sampler_first_call_zero_deltas() -> None:
    t = _mk_transport([_combined_stdout()])
    sampler = MetricSampler(t)
    r = await sampler.sample()
    assert r.ok, r.error
    s = r.data
    assert s.cpu_pct_total == 0.0  # no previous sample
    assert s.net_rx_bytes_per_s == 0
    assert s.net_tx_bytes_per_s == 0
    assert s.disk_read_kb_per_s == 0
    assert s.mem_total_kb == 8000000
    assert s.mem_avail_kb == 3000000
    assert s.mem_used_kb == 5000000
    assert s.cpu_temp_c == 55.0
    assert s.gpu_freq_hz == 800000000
    assert s.gpu_util_pct == 29
    assert s.battery_temp_c == 38.2


@pytest.mark.asyncio
async def test_sampler_second_call_computes_deltas(monkeypatch: pytest.MonkeyPatch) -> None:
    # First sample: baseline. Second sample: cpu busy + 1KB/s on eth0.
    a = _combined_stdout(cpu_line="cpu 100 50 30 600 10 0 5", net_eth_rx=5000, net_eth_tx=3000)
    b = _combined_stdout(cpu_line="cpu 200 150 130 700 20 0 5", net_eth_rx=15000, net_eth_tx=8000)
    t = _mk_transport([a, b])
    sampler = MetricSampler(t)

    # Pin monotonic so elapsed_s == 1.0 on the second call.
    times = iter([1000.0, 1001.0])
    monkeypatch.setattr("alb.capabilities.metrics.monotonic", lambda: next(times))

    r1 = await sampler.sample()
    r2 = await sampler.sample()
    assert r1.ok and r2.ok
    s = r2.data
    assert s.cpu_pct_total > 0.0  # CPU did work
    assert s.net_rx_bytes_per_s == 10000  # (15000-5000)/1s
    assert s.net_tx_bytes_per_s == 5000


@pytest.mark.asyncio
async def test_sampler_shell_failure() -> None:
    t = AsyncMock()
    t.name = "adb"
    t.shell = AsyncMock(return_value=ShellResult(
        ok=False, stderr="device offline", error_code="ADB_COMMAND_FAILED",
    ))
    r = await MetricSampler(t).sample()
    assert not r.ok
    assert r.error.code == "METRICS_SAMPLE_FAILED"


# ─── MetricsStreamer ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_streamer_runs_and_collects_history() -> None:
    t = _mk_transport([_combined_stdout()])
    s = MetricsStreamer(t, interval_s=0.05, ring_size=10)
    await s.start()
    try:
        # Let it sample a few times
        await asyncio.sleep(0.25)
        hist = s.history(5)
        assert len(hist) >= 2
        assert all(isinstance(x, MetricSample) for x in hist)
    finally:
        await s.stop()


@pytest.mark.asyncio
async def test_streamer_subscribe_receives_samples() -> None:
    t = _mk_transport([_combined_stdout()])
    s = MetricsStreamer(t, interval_s=0.05)
    await s.start()
    try:
        async with s.subscribe() as q:
            sample = await asyncio.wait_for(q.get(), timeout=1.0)
            assert isinstance(sample, MetricSample)
    finally:
        await s.stop()


@pytest.mark.asyncio
async def test_streamer_pause_stops_ring_growth() -> None:
    t = _mk_transport([_combined_stdout()])
    s = MetricsStreamer(t, interval_s=0.05)
    await s.start()
    try:
        await asyncio.sleep(0.2)
        before = len(s.ring)
        s.pause()
        await asyncio.sleep(0.2)
        after = len(s.ring)
        # Allow at most 1 sample slip from a tick already in flight
        assert after - before <= 1
    finally:
        await s.stop()


def test_streamer_interval_clamp() -> None:
    s = MetricsStreamer(AsyncMock(), interval_s=1.0)
    s.interval_s = 0.001  # too small
    assert s.interval_s == 0.1
    s.interval_s = 1000.0  # too big
    assert s.interval_s == 60.0


@pytest.mark.asyncio
async def test_get_streamer_shares_instance_per_device() -> None:
    t = _mk_transport([_combined_stdout()])
    a = get_streamer(t, device_key="abc")
    b = get_streamer(t, device_key="abc")
    c = get_streamer(t, device_key="xyz")
    assert a is b
    assert a is not c
    await shutdown_all_streamers()


@pytest.mark.asyncio
async def test_shutdown_all_streamers_clears_registry() -> None:
    t = _mk_transport([_combined_stdout()])
    get_streamer(t, device_key="cleanup-test")
    await shutdown_all_streamers()
    # Re-creating should give a fresh instance
    fresh = get_streamer(t, device_key="cleanup-test")
    assert fresh is not None
    await shutdown_all_streamers()


# ─── MetricSample serialization ──────────────────────────────────


def test_metric_sample_to_dict() -> None:
    s = MetricSample(
        ts_ms=1700000000000, cpu_pct_total=42.0, cpu_freq_khz=[1800000],
        cpu_temp_c=55.0, mem_used_kb=4_000_000, mem_total_kb=8_000_000,
        mem_avail_kb=4_000_000, swap_used_kb=0, gpu_freq_hz=800_000_000,
        gpu_util_pct=29, net_rx_bytes_per_s=1024, net_tx_bytes_per_s=512,
        disk_read_kb_per_s=50, disk_write_kb_per_s=20, battery_temp_c=38.2,
    )
    d = s.to_dict()
    assert d["cpu_pct_total"] == 42.0
    assert d["cpu_freq_khz"] == [1800000]
    assert d["battery_temp_c"] == 38.2
