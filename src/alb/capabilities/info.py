"""info capability — structured device software + hardware snapshot.

Each public function returns a single panel's worth of data (e.g. CPU,
memory, storage). `all_info()` runs the full set in parallel. Data sources
are on-device: getprop / /proc / /sys / dumpsys / df / ip.

Everything is diagnostic — no state changes, no tapping.

See docs/capabilities/info.md.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from alb.infra.result import Result, fail, ok
from alb.transport.base import Transport

# ─── Shared helpers ────────────────────────────────────────────────


def _elapsed_ms(start: float) -> int:
    return int((perf_counter() - start) * 1000)


def _parse_getprop(stdout: str) -> dict[str, str]:
    """`[key]: [value]` lines → dict."""
    out: dict[str, str] = {}
    for line in stdout.splitlines():
        if not line.startswith("["):
            continue
        try:
            key_part, value_part = line.split("]:", 1)
            key = key_part.strip().lstrip("[").rstrip("]")
            value = value_part.strip().lstrip("[").rstrip("]")
            out[key] = value
        except ValueError:
            continue
    return out


async def _shell_or_empty(t: Transport, cmd: str, timeout: int = 10) -> str:
    """Run shell, return stdout on ok, empty string on failure.

    Panels should degrade gracefully when a single source is missing
    rather than failing the whole query.
    """
    r = await t.shell(cmd, timeout=timeout)
    return r.stdout if r.ok else ""


# ─── Panel: system (Android + kernel + bootloader + SELinux) ────────


@dataclass(frozen=True)
class SystemInfo:
    android_release: str
    api_level: str
    build_type: str
    build_fingerprint: str
    security_patch: str
    kernel_version: str
    arch: str
    selinux: str
    bootloader: str
    baseband: str
    serial: str
    model: str
    brand: str
    manufacturer: str
    product: str
    hardware: str
    soc_model: str
    slot: str
    adb_root: bool
    extras: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        return d


async def system(t: Transport, *, device: str | None = None) -> Result[SystemInfo]:
    """Overview + Kernel + Bootloader + SELinux — one call, many sources."""
    start = perf_counter()
    props_out = await _shell_or_empty(t, "getprop", timeout=15)
    if not props_out:
        return fail(
            code="ADB_COMMAND_FAILED",
            message="getprop returned nothing",
            suggestion="Device may be offline / unauthorized",
            category="transport",
            timing_ms=_elapsed_ms(start),
        )
    props = _parse_getprop(props_out)

    uname_out = await _shell_or_empty(t, "uname -a", timeout=5)
    arch_out = await _shell_or_empty(t, "uname -m", timeout=5)
    selinux_out = await _shell_or_empty(t, "getenforce", timeout=5)
    whoami = await _shell_or_empty(t, "id -u 2>/dev/null", timeout=5)

    info = SystemInfo(
        android_release=props.get("ro.build.version.release", ""),
        api_level=props.get("ro.build.version.sdk", ""),
        build_type=props.get("ro.build.type", ""),
        build_fingerprint=props.get("ro.build.fingerprint", ""),
        security_patch=props.get("ro.build.version.security_patch", ""),
        kernel_version=uname_out.strip(),
        arch=arch_out.strip(),
        selinux=selinux_out.strip().lower(),
        bootloader=props.get("ro.bootloader", ""),
        baseband=props.get("gsm.version.baseband", ""),
        serial=props.get("ro.serialno", ""),
        model=props.get("ro.product.model", ""),
        brand=props.get("ro.product.brand", ""),
        manufacturer=props.get("ro.product.manufacturer", ""),
        product=props.get("ro.product.name", ""),
        hardware=props.get("ro.hardware", ""),
        soc_model=props.get("ro.soc.model", "") or props.get("ro.board.platform", ""),
        slot=props.get("ro.boot.slot_suffix", ""),
        adb_root=whoami.strip() == "0",
        extras={
            "abi_list": props.get("ro.product.cpu.abilist", ""),
            "build_date": props.get("ro.build.date", ""),
            "build_id": props.get("ro.build.id", ""),
            "vndk_version": props.get("ro.vndk.version", ""),
            "treble_enabled": props.get("ro.treble.enabled", ""),
        },
    )
    return ok(data=info, timing_ms=_elapsed_ms(start))


# ─── Panel: cpu ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class CPUCore:
    index: int
    freq_khz_current: int
    freq_khz_max: int
    freq_khz_min: int
    governor: str

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class ThermalZone:
    name: str
    type: str
    temp_c: float

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class CPUInfo:
    processor_count: int
    model: str  # from /proc/cpuinfo; often empty on Android aarch64
    soc_model: str  # SoC name from getprop (e.g. from ro.soc.model)
    soc_manufacturer: str
    features: list[str]
    cores: list[CPUCore]
    thermal_zones: list[ThermalZone]

    def to_dict(self) -> dict[str, Any]:
        return {
            "processor_count": self.processor_count,
            "model": self.model,
            "soc_model": self.soc_model,
            "soc_manufacturer": self.soc_manufacturer,
            "features": self.features,
            "cores": [c.to_dict() for c in self.cores],
            "thermal_zones": [z.to_dict() for z in self.thermal_zones],
        }


async def cpu(t: Transport, *, device: str | None = None) -> Result[CPUInfo]:
    start = perf_counter()
    cpuinfo = await _shell_or_empty(t, "cat /proc/cpuinfo", timeout=5)
    n = _count_processors(cpuinfo)
    model, features = _parse_cpuinfo_head(cpuinfo)

    # ARM64 /proc/cpuinfo often omits a human-readable model; fall back to
    # getprop for the SoC identity.
    soc_out = await _shell_or_empty(
        t,
        "getprop ro.soc.model; getprop ro.soc.manufacturer; "
        "getprop ro.board.platform",
        timeout=5,
    )
    soc_model, soc_mfr = _parse_soc_props(soc_out)

    # Pull per-core freq / governor. Globbing sysfs with a single shell line
    # keeps us to one roundtrip.
    freq_out = await _shell_or_empty(
        t,
        r"for i in /sys/devices/system/cpu/cpu[0-9]*/cpufreq; do "
        r'echo "$i:" && cat $i/scaling_cur_freq 2>/dev/null && '
        r"cat $i/cpuinfo_max_freq 2>/dev/null && "
        r"cat $i/cpuinfo_min_freq 2>/dev/null && "
        r"cat $i/scaling_governor 2>/dev/null; done",
        timeout=10,
    )
    cores = _parse_cpu_freq_dump(freq_out, n)

    thermal_out = await _shell_or_empty(
        t,
        r"for z in /sys/class/thermal/thermal_zone*; do "
        r'echo "$z:" && cat $z/type 2>/dev/null && '
        r"cat $z/temp 2>/dev/null; done",
        timeout=10,
    )
    zones = _parse_thermal_zones(thermal_out)

    return ok(
        data=CPUInfo(
            processor_count=n,
            model=model,
            soc_model=soc_model,
            soc_manufacturer=soc_mfr,
            features=features,
            cores=cores,
            thermal_zones=zones,
        ),
        timing_ms=_elapsed_ms(start),
    )


def _parse_soc_props(stdout: str) -> tuple[str, str]:
    """Three `getprop X` outputs on separate lines → (model, manufacturer).

    Order: ro.soc.model, ro.soc.manufacturer, ro.board.platform (fallback).
    """
    lines = [ln.strip() for ln in stdout.splitlines()]
    model = lines[0] if len(lines) >= 1 and lines[0] else ""
    mfr = lines[1] if len(lines) >= 2 else ""
    if not model and len(lines) >= 3:
        model = lines[2]
    return model, mfr


def _count_processors(stdout: str) -> int:
    return sum(1 for line in stdout.splitlines() if line.startswith("processor"))


def _parse_cpuinfo_head(stdout: str) -> tuple[str, list[str]]:
    model = ""
    features: list[str] = []
    for line in stdout.splitlines():
        if ":" not in line:
            continue
        key, value = (s.strip() for s in line.split(":", 1))
        kl = key.lower()
        if not model and kl in {"model name", "hardware", "cpu model"}:
            model = value
        elif kl in {"features", "flags"} and not features:
            features = value.split()
    return model, features


def _parse_cpu_freq_dump(stdout: str, n_cores: int) -> list[CPUCore]:
    """Parse the 'for i in ...; do echo $i:; cat ...; done' block into CPUCores."""
    cores: list[CPUCore] = []
    current_path: str | None = None
    buf: list[str] = []
    for line in stdout.splitlines():
        if line.endswith(":") and line.startswith("/sys/"):
            if current_path is not None:
                cores.append(_build_core(current_path, buf))
            current_path = line.rstrip(":")
            buf = []
        else:
            buf.append(line)
    if current_path is not None:
        cores.append(_build_core(current_path, buf))
    # Fallback: if sysfs was locked down, emit placeholders matching core count
    if not cores and n_cores > 0:
        cores = [CPUCore(i, 0, 0, 0, "") for i in range(n_cores)]
    return cores


def _build_core(path: str, values: list[str]) -> CPUCore:
    # Path: /sys/devices/system/cpu/cpuN/cpufreq
    m = re.search(r"/cpu(\d+)/", path)
    index = int(m.group(1)) if m else 0
    numeric = [v.strip() for v in values if v.strip()]
    # Expected order: cur, max, min, governor — governor is non-numeric.
    cur = max_ = min_ = 0
    governor = ""
    nums: list[int] = []
    for v in numeric:
        try:
            nums.append(int(v))
        except ValueError:
            if not governor and not v.isdigit():
                governor = v
    if len(nums) >= 1:
        cur = nums[0]
    if len(nums) >= 2:
        max_ = nums[1]
    if len(nums) >= 3:
        min_ = nums[2]
    return CPUCore(index, cur, max_, min_, governor)


def _parse_thermal_zones(stdout: str) -> list[ThermalZone]:
    zones: list[ThermalZone] = []
    current_path: str | None = None
    buf: list[str] = []
    for line in stdout.splitlines():
        if line.endswith(":") and line.startswith("/sys/class/thermal/"):
            if current_path is not None:
                zones.append(_build_zone(current_path, buf))
            current_path = line.rstrip(":")
            buf = []
        else:
            buf.append(line)
    if current_path is not None:
        zones.append(_build_zone(current_path, buf))
    return zones


def _build_zone(path: str, values: list[str]) -> ThermalZone:
    m = re.search(r"thermal_zone(\d+)", path)
    name = f"thermal_zone{m.group(1)}" if m else path.rsplit("/", 1)[-1]
    ztype = values[0].strip() if values else ""
    temp_c = 0.0
    if len(values) >= 2:
        try:
            # Kernel gives milli-degrees C.
            temp_c = int(values[1].strip()) / 1000.0
        except ValueError:
            pass
    return ThermalZone(name=name, type=ztype, temp_c=round(temp_c, 1))


# ─── Panel: memory ───────────────────────────────────────────────────


@dataclass(frozen=True)
class MemoryInfo:
    total_kb: int
    free_kb: int
    available_kb: int
    buffers_kb: int
    cached_kb: int
    swap_total_kb: int
    swap_free_kb: int
    zram_total_kb: int
    extras: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        return d


async def memory(t: Transport, *, device: str | None = None) -> Result[MemoryInfo]:
    start = perf_counter()
    mi = await _shell_or_empty(t, "cat /proc/meminfo", timeout=5)
    if not mi:
        return fail(
            code="MEMINFO_UNREADABLE",
            message="Could not read /proc/meminfo",
            suggestion="Device offline or permissions issue",
            category="capability",
            timing_ms=_elapsed_ms(start),
        )

    parsed = _parse_meminfo(mi)

    zram_out = await _shell_or_empty(t, "cat /sys/block/zram0/disksize 2>/dev/null", timeout=3)
    zram_kb = 0
    if zram_out.strip().isdigit():
        zram_kb = int(zram_out.strip()) // 1024

    info = MemoryInfo(
        total_kb=parsed.get("MemTotal", 0),
        free_kb=parsed.get("MemFree", 0),
        available_kb=parsed.get("MemAvailable", 0),
        buffers_kb=parsed.get("Buffers", 0),
        cached_kb=parsed.get("Cached", 0),
        swap_total_kb=parsed.get("SwapTotal", 0),
        swap_free_kb=parsed.get("SwapFree", 0),
        zram_total_kb=zram_kb,
        extras={
            k: v for k, v in parsed.items()
            if k in {"Dirty", "AnonPages", "Slab", "KReclaimable", "Shmem", "Mlocked"}
        },
    )
    return ok(data=info, timing_ms=_elapsed_ms(start))


def _parse_meminfo(stdout: str) -> dict[str, int]:
    """'Key: 123 kB' → {Key: 123}."""
    out: dict[str, int] = {}
    for line in stdout.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        parts = value.strip().split()
        if parts and parts[0].isdigit():
            out[key.strip()] = int(parts[0])
    return out


# ─── Panel: storage ───────────────────────────────────────────────────


@dataclass(frozen=True)
class Filesystem:
    source: str
    mount: str
    fstype: str
    size_kb: int
    used_kb: int
    avail_kb: int
    use_pct: int

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class Partition:
    name: str
    size_kb: int
    by_name: str  # symlink target under /dev/block/by-name/, if any

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class StorageInfo:
    filesystems: list[Filesystem]
    partitions: list[Partition]
    ufs_spec: str  # "UFS 3.1" / "eMMC 5.1" / "" if unknown

    def to_dict(self) -> dict[str, Any]:
        return {
            "filesystems": [f.to_dict() for f in self.filesystems],
            "partitions": [p.to_dict() for p in self.partitions],
            "ufs_spec": self.ufs_spec,
        }


# Real storage devices — exclude ramdisks / loopback / zram. Keep dm-N
# (device-mapper, used by A/B sparse filesystems on modern Android).
_PARTITION_NOISE = re.compile(r"^(ram|loop|zram)\d+$")
_PARTITION_REAL = re.compile(r"^(mmcblk|sd[a-z]|nvme|vd[a-z])")


async def storage(
    t: Transport,
    *,
    device: str | None = None,
    include_virtual: bool = False,
) -> Result[StorageInfo]:
    """Storage snapshot.

    Args:
        include_virtual: when False (default), ramdisks / loop / zram
                         devices are dropped from the partition list so
                         real storage stands out.
    """
    start = perf_counter()

    df_out = await _shell_or_empty(t, "df -k 2>/dev/null", timeout=10)
    mount_out = await _shell_or_empty(t, "cat /proc/mounts", timeout=5)
    fstype_map = _parse_mounts_for_fstype(mount_out)
    filesystems = _parse_df_k(df_out, fstype_map)

    parts_out = await _shell_or_empty(t, "cat /proc/partitions", timeout=5)
    by_name_out = await _shell_or_empty(
        t,
        "ls -l /dev/block/by-name/ 2>/dev/null || "
        "ls -l /dev/block/platform/*/by-name/ 2>/dev/null",
        timeout=5,
    )
    by_name_map = _parse_by_name_listing(by_name_out)
    partitions = _parse_proc_partitions(
        parts_out, by_name_map, include_virtual=include_virtual
    )

    # Heuristic UFS/eMMC detection via dmesg — may be empty without root.
    dmesg_out = await _shell_or_empty(
        t, "dmesg 2>/dev/null | grep -iE 'ufs|mmc|emmc' | head -5", timeout=10
    )
    ufs_spec = _sniff_ufs_spec(dmesg_out)

    return ok(
        data=StorageInfo(
            filesystems=filesystems,
            partitions=partitions,
            ufs_spec=ufs_spec,
        ),
        timing_ms=_elapsed_ms(start),
    )


def _parse_df_k(stdout: str, fstype_map: dict[str, str]) -> list[Filesystem]:
    out: list[Filesystem] = []
    lines = stdout.splitlines()
    if not lines:
        return out
    # Skip header row
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 6:
            continue
        # Source  1K-blocks  Used  Available  Use%  MountedOn
        source = parts[0]
        try:
            size = int(parts[1])
            used = int(parts[2])
            avail = int(parts[3])
        except ValueError:
            continue
        use_pct_str = parts[4].rstrip("%")
        try:
            use_pct = int(use_pct_str)
        except ValueError:
            use_pct = 0
        mount = parts[5]
        out.append(Filesystem(
            source=source,
            mount=mount,
            fstype=fstype_map.get(mount, ""),
            size_kb=size,
            used_kb=used,
            avail_kb=avail,
            use_pct=use_pct,
        ))
    return out


def _parse_mounts_for_fstype(stdout: str) -> dict[str, str]:
    """/proc/mounts: 'source mount fstype opts 0 0' → {mount: fstype}."""
    out: dict[str, str] = {}
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3:
            out[parts[1]] = parts[2]
    return out


def _parse_proc_partitions(
    stdout: str,
    by_name_map: dict[str, str],
    *,
    include_virtual: bool = False,
) -> list[Partition]:
    """major minor #blocks name → list[Partition] (sizes in KB since blocks=1K).

    Virtual devices (ram0-N, loopN, zramN) are filtered out by default so
    the real storage layout is visible at a glance.
    """
    out: list[Partition] = []
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) != 4 or parts[0] == "major":
            continue
        try:
            blocks = int(parts[2])
        except ValueError:
            continue
        name = parts[3]
        if not include_virtual and _PARTITION_NOISE.match(name):
            continue
        out.append(Partition(
            name=name,
            size_kb=blocks,
            by_name=by_name_map.get(name, ""),
        ))
    return out


def _parse_by_name_listing(stdout: str) -> dict[str, str]:
    """ls -l output → {block_device_name: by_name_label}.

    Each line looks like:
      lrwxrwxrwx 1 root root 16 2024-01-01 01:00 boot_a -> /dev/block/sda5

    Self-links (e.g. `mmcblk0 -> /dev/block/mmcblk0`) are skipped — some
    boards ship them and they'd otherwise tag the whole disk with a
    meaningless by_name.
    """
    out: dict[str, str] = {}
    for line in stdout.splitlines():
        if "->" not in line:
            continue
        left, _, right = line.partition("->")
        label = left.split()[-1] if left.split() else ""
        dev = right.strip().rsplit("/", 1)[-1]
        if not label or not dev or label == dev:
            continue
        out[dev] = label
    return out


def _sniff_ufs_spec(dmesg_out: str) -> str:
    text = dmesg_out.lower()
    if "ufs3.1" in text or "ufs 3.1" in text:
        return "UFS 3.1"
    if "ufs3.0" in text or "ufs 3.0" in text:
        return "UFS 3.0"
    if "ufs2.1" in text or "ufs 2.1" in text:
        return "UFS 2.1"
    if "ufs" in text:
        return "UFS"
    if "emmc" in text or "mmc0" in text:
        return "eMMC"
    return ""


# ─── Panel: network ───────────────────────────────────────────────────


@dataclass(frozen=True)
class NetworkIface:
    name: str
    state: str  # up/down/unknown
    mac: str
    ipv4: list[str]
    ipv6: list[str]
    mtu: int

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class NetworkInfo:
    interfaces: list[NetworkIface]
    default_route: str
    dns: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "interfaces": [i.to_dict() for i in self.interfaces],
            "default_route": self.default_route,
            "dns": self.dns,
        }


async def network(t: Transport, *, device: str | None = None) -> Result[NetworkInfo]:
    start = perf_counter()
    ip_out = await _shell_or_empty(t, "ip -o addr 2>/dev/null", timeout=5)
    link_out = await _shell_or_empty(t, "ip -o link 2>/dev/null", timeout=5)
    route_out = await _shell_or_empty(t, "ip route 2>/dev/null | head -5", timeout=5)

    interfaces = _parse_ip_addr(ip_out, link_out)
    default_route = _extract_default_route(route_out)
    # DNS on Android 10+ no longer goes through getprop net.dns*; it's set
    # per-network via ConnectivityService. Try getprop first for older
    # devices, then fall back to /etc/resolv.conf.
    dns_out = await _shell_or_empty(t, "getprop net.dns1; getprop net.dns2", timeout=3)
    dns = [line.strip() for line in dns_out.splitlines() if line.strip()]
    if not dns:
        resolv = await _shell_or_empty(
            t, "cat /etc/resolv.conf 2>/dev/null", timeout=3
        )
        for line in resolv.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] == "nameserver":
                dns.append(parts[1])

    return ok(
        data=NetworkInfo(
            interfaces=interfaces,
            default_route=default_route,
            dns=dns,
        ),
        timing_ms=_elapsed_ms(start),
    )


def _parse_ip_addr(addr_out: str, link_out: str) -> list[NetworkIface]:
    ifaces: dict[str, NetworkIface] = {}
    state_map: dict[str, str] = {}
    mac_map: dict[str, str] = {}
    mtu_map: dict[str, int] = {}

    # Link dump: '2: wlan0: <BROADCAST,...> mtu 1500 ... link/ether aa:bb:cc'
    for line in link_out.splitlines():
        m = re.match(r"^\d+:\s+([^:@]+)[:@]", line)
        if not m:
            continue
        name = m.group(1).strip()
        mtu_m = re.search(r"mtu\s+(\d+)", line)
        if mtu_m:
            mtu_map[name] = int(mtu_m.group(1))
        mac_m = re.search(r"link/(?:ether|sit|none)\s+([\da-fA-F:]+)", line)
        if mac_m:
            mac_map[name] = mac_m.group(1).lower()
        flags_m = re.search(r"<([^>]+)>", line)
        if flags_m:
            flags = flags_m.group(1).split(",")
            state_map[name] = "up" if "UP" in flags else "down"

    # Addr dump: '2: wlan0    inet 192.168.1.42/24 ...'
    addrs4: dict[str, list[str]] = {}
    addrs6: dict[str, list[str]] = {}
    for line in addr_out.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        name = parts[1].rstrip(":").rstrip("@")
        family = parts[2]
        addr = parts[3]
        if family == "inet":
            addrs4.setdefault(name, []).append(addr)
        elif family == "inet6":
            addrs6.setdefault(name, []).append(addr)

    names = set(state_map) | set(addrs4) | set(addrs6)
    for name in sorted(names):
        ifaces[name] = NetworkIface(
            name=name,
            state=state_map.get(name, "unknown"),
            mac=mac_map.get(name, ""),
            ipv4=addrs4.get(name, []),
            ipv6=addrs6.get(name, []),
            mtu=mtu_map.get(name, 0),
        )
    return list(ifaces.values())


def _extract_default_route(stdout: str) -> str:
    for line in stdout.splitlines():
        if line.startswith("default"):
            return line.strip()
    return ""


# ─── Panel: battery ──────────────────────────────────────────────────


@dataclass(frozen=True)
class BatteryInfo:
    level_pct: int
    status: str
    health: str
    voltage_mv: int
    current_ua: int
    temperature_c: float
    technology: str
    plugged: str
    cycle_count: int
    present: bool

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


async def battery(t: Transport, *, device: str | None = None) -> Result[BatteryInfo]:
    start = perf_counter()
    out = await _shell_or_empty(t, "dumpsys battery 2>/dev/null", timeout=10)
    if not out:
        return fail(
            code="BATTERY_UNAVAILABLE",
            message="dumpsys battery returned nothing",
            suggestion="Device may not expose a battery (emulator / dev board)",
            category="capability",
            timing_ms=_elapsed_ms(start),
        )
    info = _parse_dumpsys_battery(out)
    # Dev boards report present=false and leave every field at zero.
    # Surface this clearly rather than letting the UI render an empty
    # battery card — it's a real physical state, not a probe failure.
    if not info.present and info.level_pct == 0 and info.voltage_mv == 0:
        return fail(
            code="NO_BATTERY",
            message="Device has no battery (dumpsys reports present=false)",
            suggestion="Common on dev boards / emulators. Safe to hide the battery panel.",
            category="device",
            details={"raw": out[:500]},
            timing_ms=_elapsed_ms(start),
        )
    return ok(data=info, timing_ms=_elapsed_ms(start))


_STATUS_MAP = {
    "1": "unknown", "2": "charging", "3": "discharging",
    "4": "not_charging", "5": "full",
}
_HEALTH_MAP = {
    "1": "unknown", "2": "good", "3": "overheat", "4": "dead",
    "5": "over_voltage", "6": "unspecified_failure", "7": "cold",
}
_PLUGGED_MAP = {"0": "unplugged", "1": "AC", "2": "USB", "4": "wireless"}


def _parse_dumpsys_battery(stdout: str) -> BatteryInfo:
    kv: dict[str, str] = {}
    for line in stdout.splitlines():
        line = line.strip()
        if ":" in line:
            key, _, value = line.partition(":")
            kv[key.strip().lower()] = value.strip()

    def _int(key: str, default: int = 0) -> int:
        try:
            return int(kv.get(key, str(default)))
        except ValueError:
            return default

    def _bool(key: str) -> bool:
        return kv.get(key, "").strip().lower() == "true"

    temp_raw = _int("temperature")
    return BatteryInfo(
        level_pct=_int("level"),
        status=_STATUS_MAP.get(kv.get("status", ""), kv.get("status", "")),
        health=_HEALTH_MAP.get(kv.get("health", ""), kv.get("health", "")),
        voltage_mv=_int("voltage"),
        current_ua=_int("current now", _int("current_now")),
        # Android reports tenths of °C (e.g. 283 → 28.3°C)
        temperature_c=round(temp_raw / 10.0, 1) if temp_raw else 0.0,
        technology=kv.get("technology", ""),
        plugged=_PLUGGED_MAP.get(kv.get("plugged", ""), kv.get("plugged", "")),
        cycle_count=_int("cycle count"),
        present=_bool("present"),
    )


# ─── Aggregate: run all 6 in parallel ────────────────────────────────


_PANELS = {
    "system": system,
    "cpu": cpu,
    "memory": memory,
    "storage": storage,
    "network": network,
    "battery": battery,
}


async def all_info(
    t: Transport,
    *,
    device: str | None = None,
    panels: list[str] | None = None,
) -> dict[str, Result[Any]]:
    """Run the requested panels in parallel, return {name: Result}.

    Defaults to all available panels. Pass `panels=["cpu","memory"]` to
    subset. Unknown names are skipped silently so new callers can request
    future panels without breaking on older builds.
    """
    names = panels or list(_PANELS)
    funcs = [(n, _PANELS[n]) for n in names if n in _PANELS]
    results = await asyncio.gather(
        *(func(t, device=device) for _, func in funcs),
        return_exceptions=False,
    )
    return dict(zip([n for n, _ in funcs], results, strict=True))


def panel_names() -> list[str]:
    """Enumerate the available panel keys (for CLI / MCP help)."""
    return list(_PANELS)
