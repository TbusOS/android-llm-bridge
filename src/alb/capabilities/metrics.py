"""metrics capability — lightweight 1 Hz sampler for live dashboards.

Built on top of the same /proc, /sys, and dumpsys sources that
`info` uses, but tuned for high-frequency polling: one MetricSample
batches everything into a single shell roundtrip per tick. CPU and
network/disk percentages come from differencing two consecutive
samples — the first sample after start has zeros for those fields.

Used by:
  - `alb metrics sample`    one-shot CLI
  - `alb metrics watch`     text-mode tail
  - WS /metrics/stream       live feed for the Web UI Charts panel
  - `alb_metrics_snapshot`   MCP tool for agent triage
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
from dataclasses import dataclass, field
from time import monotonic, time
from typing import Any, AsyncIterator

from alb.infra.result import Result, fail, ok
from alb.transport.base import Transport


# ─── Sample shape ────────────────────────────────────────────────────


@dataclass(frozen=True)
class MetricSample:
    ts_ms: int                  # unix epoch ms
    cpu_pct_total: float        # 0-100; 0 on first sample
    cpu_freq_khz: list[int]     # per-core current scaling freq
    cpu_temp_c: float           # max temp across all 'cpu' thermal zones
    mem_used_kb: int            # MemTotal - MemAvailable
    mem_total_kb: int
    mem_avail_kb: int
    swap_used_kb: int
    gpu_freq_hz: int            # 0 if not detected
    gpu_util_pct: int           # -1 if unknown
    net_rx_bytes_per_s: int     # diff'd from /proc/net/dev
    net_tx_bytes_per_s: int
    disk_read_kb_per_s: int     # diff'd from /sys/block/<dev>/stat
    disk_write_kb_per_s: int
    battery_temp_c: float       # 0.0 if no battery

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


# ─── Sampler — keeps state between calls for diff metrics ────────────


_SAMPLE_CMD = (
    # Each fragment ends with a delimiter so we can split the combined
    # output back into sections in Python without ambiguity.
    "echo __ALB_STAT__; cat /proc/stat 2>/dev/null;"
    "echo __ALB_MEM__; cat /proc/meminfo 2>/dev/null;"
    "echo __ALB_NET__; cat /proc/net/dev 2>/dev/null;"
    "echo __ALB_FREQ__; for f in /sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_cur_freq;"
    " do cat $f 2>/dev/null; done;"
    "echo __ALB_THERM__; for z in /sys/class/thermal/thermal_zone*;"
    ' do echo "$z:"; cat $z/type 2>/dev/null; cat $z/temp 2>/dev/null; done;'
    "echo __ALB_GPU__; for d in /sys/class/devfreq/*;"
    ' do echo "$d:"; cat $d/name 2>/dev/null; cat $d/cur_freq 2>/dev/null; done;'
    "echo __ALB_GPUUTIL__; cat /sys/kernel/debug/mali*/gpu_utilization 2>/dev/null;"
    "echo __ALB_DISK__; cat /sys/block/*/stat 2>/dev/null;"
    "echo __ALB_BAT__; dumpsys battery 2>/dev/null | grep -E '^[[:space:]]*(present|temperature):'"
)


class MetricSampler:
    """Stateful sampler. Keeps the previous /proc/stat etc. so deltas
    can be computed for cpu / net / disk."""

    def __init__(self, transport: Transport) -> None:
        self.transport = transport
        self._prev_cpu_jiffies: tuple[int, int] | None = None  # (idle, total)
        self._prev_net_totals: tuple[int, int] | None = None    # (rx, tx)
        self._prev_disk_totals: tuple[int, int] | None = None   # (read_sec, write_sec)
        self._prev_mono_s: float | None = None

    async def sample(self) -> Result[MetricSample]:
        r = await self.transport.shell(_SAMPLE_CMD, timeout=10)
        if not r.ok or not r.stdout:
            return fail(
                code="METRICS_SAMPLE_FAILED",
                message=r.stderr.strip() or "sample shell returned nothing",
                category="capability",
                details={"stderr": r.stderr},
                timing_ms=r.duration_ms,
            )
        sections = _split_sections(r.stdout)
        now_mono = monotonic()
        elapsed_s = (
            now_mono - self._prev_mono_s if self._prev_mono_s is not None else 0.0
        )

        cpu_pct = 0.0
        idle, total = _parse_cpu_jiffies(sections.get("STAT", ""))
        if self._prev_cpu_jiffies is not None and idle and total:
            prev_idle, prev_total = self._prev_cpu_jiffies
            d_idle = idle - prev_idle
            d_total = total - prev_total
            if d_total > 0:
                cpu_pct = round(100.0 * (1.0 - d_idle / d_total), 1)
        if idle and total:
            self._prev_cpu_jiffies = (idle, total)

        mem = _parse_meminfo_subset(sections.get("MEM", ""))
        freqs = _parse_freq_dump(sections.get("FREQ", ""))
        cpu_temp = _max_cpu_temp(sections.get("THERM", ""))
        gpu_freq, _gpu_name = _parse_gpu_devfreq(sections.get("GPU", ""))
        gpu_util = _parse_int_first(sections.get("GPUUTIL", ""), default=-1)

        net_rx, net_tx = _sum_net_dev(sections.get("NET", ""))
        net_rx_per_s = 0
        net_tx_per_s = 0
        if (
            self._prev_net_totals is not None
            and elapsed_s > 0
            and net_rx >= self._prev_net_totals[0]
        ):
            net_rx_per_s = int((net_rx - self._prev_net_totals[0]) / elapsed_s)
            net_tx_per_s = int((net_tx - self._prev_net_totals[1]) / elapsed_s)
        if net_rx or net_tx:
            self._prev_net_totals = (net_rx, net_tx)

        disk_read_sec, disk_write_sec = _sum_disk_stat(sections.get("DISK", ""))
        disk_read_kb_per_s = 0
        disk_write_kb_per_s = 0
        if (
            self._prev_disk_totals is not None
            and elapsed_s > 0
            and disk_read_sec >= self._prev_disk_totals[0]
        ):
            # 512-byte sectors → KB
            disk_read_kb_per_s = int(
                (disk_read_sec - self._prev_disk_totals[0]) / 2 / elapsed_s
            )
            disk_write_kb_per_s = int(
                (disk_write_sec - self._prev_disk_totals[1]) / 2 / elapsed_s
            )
        if disk_read_sec or disk_write_sec:
            self._prev_disk_totals = (disk_read_sec, disk_write_sec)

        battery_temp = _parse_battery_temp(sections.get("BAT", ""))

        self._prev_mono_s = now_mono

        return ok(
            data=MetricSample(
                ts_ms=int(time() * 1000),
                cpu_pct_total=cpu_pct,
                cpu_freq_khz=freqs,
                cpu_temp_c=cpu_temp,
                mem_used_kb=max(0, mem["total"] - mem["avail"]),
                mem_total_kb=mem["total"],
                mem_avail_kb=mem["avail"],
                swap_used_kb=max(0, mem["swap_total"] - mem["swap_free"]),
                gpu_freq_hz=gpu_freq,
                gpu_util_pct=gpu_util,
                net_rx_bytes_per_s=net_rx_per_s,
                net_tx_bytes_per_s=net_tx_per_s,
                disk_read_kb_per_s=disk_read_kb_per_s,
                disk_write_kb_per_s=disk_write_kb_per_s,
                battery_temp_c=battery_temp,
            ),
            timing_ms=r.duration_ms,
        )


# ─── Streamer — broadcasts a shared sampling loop to N subscribers ──


class MetricsStreamer:
    """One sampling task per (transport,device) pair, fanned out to all
    WS subscribers + a fixed-size ring buffer for replay-on-connect."""

    def __init__(
        self,
        transport: Transport,
        *,
        interval_s: float = 1.0,
        ring_size: int = 300,
    ) -> None:
        self.sampler = MetricSampler(transport)
        self._interval_s = interval_s
        self.ring: collections.deque[MetricSample] = collections.deque(
            maxlen=ring_size
        )
        self._subs: set[asyncio.Queue[MetricSample]] = set()
        self._task: asyncio.Task[None] | None = None
        self._paused = False
        self._stopping = False

    @property
    def interval_s(self) -> float:
        return self._interval_s

    @interval_s.setter
    def interval_s(self, value: float) -> None:
        # Clamp to a reasonable [0.1s, 60s] band — protects the device
        # from a runaway client and stops zombie clients from hogging.
        self._interval_s = max(0.1, min(60.0, float(value)))

    @property
    def paused(self) -> bool:
        return self._paused

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def history(self, n: int = 60) -> list[MetricSample]:
        if n <= 0:
            return []
        items = list(self.ring)
        return items[-n:]

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopping = False
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stopping = True
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[MetricSample]]:
        q: asyncio.Queue[MetricSample] = asyncio.Queue(maxsize=20)
        self._subs.add(q)
        try:
            yield q
        finally:
            self._subs.discard(q)

    async def _run(self) -> None:
        while not self._stopping:
            if not self._paused:
                try:
                    r = await self.sampler.sample()
                except Exception:
                    # Network blip / transport hiccup — keep the loop
                    # alive, the next tick will retry.
                    await asyncio.sleep(self._interval_s)
                    continue
                if r.ok and r.data is not None:
                    self.ring.append(r.data)
                    for q in list(self._subs):
                        # Drop the oldest if a slow client has fallen
                        # behind, so the loop never blocks on send.
                        if q.full():
                            with contextlib.suppress(asyncio.QueueEmpty):
                                q.get_nowait()
                        with contextlib.suppress(asyncio.QueueFull):
                            q.put_nowait(r.data)
            await asyncio.sleep(self._interval_s)


# ─── Shared registry — one streamer per device ──────────────────────


_STREAMERS: dict[str, MetricsStreamer] = {}


def get_streamer(transport: Transport, *, device_key: str = "default") -> MetricsStreamer:
    """Return the streamer for `device_key`, creating it on first call.

    Multiple WS clients subscribed to the same device share one shell
    sampling loop instead of each starting their own.
    """
    s = _STREAMERS.get(device_key)
    if s is None:
        s = MetricsStreamer(transport)
        _STREAMERS[device_key] = s
    return s


async def shutdown_all_streamers() -> None:
    """Stop every active streamer (server shutdown hook)."""
    streamers = list(_STREAMERS.values())
    _STREAMERS.clear()
    for s in streamers:
        await s.stop()


# ─── Parsers ─────────────────────────────────────────────────────────


def _split_sections(stdout: str) -> dict[str, str]:
    """Split the combined sample output by the __ALB_<NAME>__ markers."""
    out: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in stdout.splitlines():
        if line.startswith("__ALB_") and line.endswith("__"):
            if current is not None:
                out[current] = "\n".join(buf)
            current = line[6:-2]
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        out[current] = "\n".join(buf)
    return out


def _parse_cpu_jiffies(stat_section: str) -> tuple[int, int]:
    """First 'cpu ' line of /proc/stat → (idle_jiffies, total_jiffies)."""
    for line in stat_section.splitlines():
        if line.startswith("cpu "):
            parts = line.split()
            try:
                fields = [int(x) for x in parts[1:]]
            except ValueError:
                return 0, 0
            if len(fields) < 4:
                return 0, 0
            idle = fields[3] + (fields[4] if len(fields) > 4 else 0)  # idle + iowait
            total = sum(fields)
            return idle, total
    return 0, 0


def _parse_meminfo_subset(mem_section: str) -> dict[str, int]:
    out = {"total": 0, "avail": 0, "free": 0, "swap_total": 0, "swap_free": 0}
    keys = {
        "MemTotal": "total",
        "MemAvailable": "avail",
        "MemFree": "free",
        "SwapTotal": "swap_total",
        "SwapFree": "swap_free",
    }
    for line in mem_section.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        if k in keys:
            parts = v.strip().split()
            if parts and parts[0].isdigit():
                out[keys[k]] = int(parts[0])
    return out


def _parse_freq_dump(freq_section: str) -> list[int]:
    out: list[int] = []
    for line in freq_section.splitlines():
        s = line.strip()
        if s.isdigit():
            out.append(int(s))
    return out


def _max_cpu_temp(therm_section: str) -> float:
    """Walk thermal zones and return the highest non-battery temperature.

    Zone type names vary wildly across SoCs: 'cpu0-thermal' on some,
    'bigcore-thermal' / 'soc-thermal' / 'little-core-thermal' /
    'cluster0-thermal' on others. Picking by keyword is fragile, so we
    take the max across every zone except battery (which is reported
    separately as battery_temp_c) and ambient sensors.
    """
    best = 0.0
    current_path: str | None = None
    buf: list[str] = []
    for line in therm_section.splitlines():
        if line.endswith(":") and line.startswith("/sys/class/thermal/"):
            best = _consume_thermal(buf, best)
            current_path = line.rstrip(":")
            buf = []
        else:
            buf.append(line)
    if current_path is not None:
        best = _consume_thermal(buf, best)
    return round(best, 1)


# Excluded so they don't dominate over real silicon temps.
_NON_SILICON_THERMAL = ("battery", "ambient", "skin", "case", "charger")


def _consume_thermal(buf: list[str], best: float) -> float:
    if len(buf) < 2:
        return best
    ztype = buf[0].strip().lower()
    if any(k in ztype for k in _NON_SILICON_THERMAL):
        return best
    try:
        # kernel reports milli-degrees C
        temp_c = int(buf[1].strip()) / 1000.0
    except ValueError:
        return best
    return max(best, temp_c)


def _parse_gpu_devfreq(section: str) -> tuple[int, str]:
    """Return (cur_freq_hz, name) for the first GPU devfreq entry."""
    current: str | None = None
    buf: list[str] = []
    keywords = ("gpu", "mali", "adreno", "powervr")
    for line in section.splitlines():
        if line.endswith(":") and line.startswith("/sys/class/devfreq/"):
            r = _devfreq_block(current, buf, keywords)
            if r is not None:
                return r
            current = line.rstrip(":")
            buf = []
        else:
            buf.append(line)
    r = _devfreq_block(current, buf, keywords)
    if r is not None:
        return r
    return 0, ""


def _devfreq_block(
    path: str | None, buf: list[str], keywords: tuple[str, ...]
) -> tuple[int, str] | None:
    if path is None:
        return None
    name = buf[0].strip() if buf else ""
    haystack = (name + " " + path).lower()
    if not any(k in haystack for k in keywords):
        return None
    for line in buf[1:]:
        s = line.strip()
        if s.isdigit():
            return int(s), name
    return None


def _parse_int_first(stdout: str, *, default: int = 0) -> int:
    for line in stdout.splitlines():
        s = line.strip().rstrip("%")
        if s.isdigit():
            return int(s)
    return default


def _sum_net_dev(stdout: str) -> tuple[int, int]:
    """/proc/net/dev: sum non-loopback rx/tx bytes."""
    rx_total = 0
    tx_total = 0
    for line in stdout.splitlines():
        if ":" not in line:
            continue
        name, _, rest = line.partition(":")
        name = name.strip()
        if name in {"lo", "Inter-", "face"}:
            continue
        parts = rest.split()
        if len(parts) >= 9:
            try:
                rx_total += int(parts[0])
                tx_total += int(parts[8])
            except ValueError:
                continue
    return rx_total, tx_total


def _sum_disk_stat(stdout: str) -> tuple[int, int]:
    """/sys/block/*/stat fields: read_sectors at index 2, write at 6.

    We can't tell which line came from which device since the shell
    glob just cats them; sum across all lines (mostly fine since real
    storage usually dominates over zram/loop noise).
    """
    read_sec = 0
    write_sec = 0
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) >= 11:
            try:
                read_sec += int(parts[2])
                write_sec += int(parts[6])
            except ValueError:
                continue
    return read_sec, write_sec


def _parse_battery_temp(section: str) -> float:
    present = False
    raw = 0
    for line in section.splitlines():
        s = line.strip().lower()
        if s.startswith("present:"):
            present = s.split(":", 1)[1].strip() == "true"
        elif s.startswith("temperature:"):
            try:
                raw = int(s.split(":", 1)[1].strip())
            except ValueError:
                raw = 0
    if not present:
        return 0.0
    return round(raw / 10.0, 1)
