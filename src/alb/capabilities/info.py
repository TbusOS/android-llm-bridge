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


# ─── Panel: gpu ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class GPUInfo:
    name: str            # device name from sysfs (e.g. "fde60000.gpu")
    vendor: str          # "arm" / "qualcomm" / "imagination" / ""
    renderer: str        # OpenGL renderer string if we can get it
    freq_hz_current: int
    freq_hz_max: int
    freq_hz_min: int
    governor: str
    util_pct: int        # -1 if unknown

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


async def gpu(t: Transport, *, device: str | None = None) -> Result[GPUInfo]:
    """GPU devfreq snapshot.

    Most Android SoCs expose the GPU via `/sys/class/devfreq/<id>` with a
    `name` like 'mali-...' or a device path like '[addr].gpu'. We look
    for the first devfreq entry whose name mentions gpu / mali / adreno.
    """
    start = perf_counter()

    dump = await _shell_or_empty(
        t,
        r"for d in /sys/class/devfreq/*; do "
        r'echo "$d:" && '
        r"cat $d/name 2>/dev/null && "
        r"cat $d/cur_freq 2>/dev/null && "
        r"cat $d/max_freq 2>/dev/null && "
        r"cat $d/min_freq 2>/dev/null && "
        r"cat $d/governor 2>/dev/null; done",
        timeout=10,
    )
    entry = _pick_gpu_devfreq(dump)

    # Load / utilization — Mali exposes via debugfs (root-only usually).
    util_out = await _shell_or_empty(
        t,
        "cat /sys/kernel/debug/mali*/gpu_utilization 2>/dev/null; "
        "cat /sys/devices/platform/*.gpu/utilisation 2>/dev/null",
        timeout=5,
    )
    util = _parse_single_int(util_out, default=-1)

    # Renderer — dumpsys SurfaceFlinger prints 'GLES:' line.
    sf_out = await _shell_or_empty(
        t, "dumpsys SurfaceFlinger 2>/dev/null | grep -iE '^GLES:' | head -1",
        timeout=10,
    )
    renderer = sf_out.strip()
    if renderer.lower().startswith("gles:"):
        renderer = renderer.split(":", 1)[1].strip()

    vendor = _detect_gpu_vendor(entry.get("name", "") + " " + renderer)

    info = GPUInfo(
        name=entry.get("name", ""),
        vendor=vendor,
        renderer=renderer,
        freq_hz_current=entry.get("cur", 0),
        freq_hz_max=entry.get("max", 0),
        freq_hz_min=entry.get("min", 0),
        governor=entry.get("gov", ""),
        util_pct=util,
    )
    return ok(data=info, timing_ms=_elapsed_ms(start))


_DEVFREQ_KEYWORDS = ("gpu", "mali", "adreno", "powervr")


def _pick_gpu_devfreq(stdout: str) -> dict[str, Any]:
    """Walk `for d in /sys/class/devfreq/*; echo $d: ...` output and
    return the first block that mentions gpu/mali/adreno/powervr."""
    current_path: str | None = None
    buf: list[str] = []
    best: dict[str, Any] = {}
    for line in stdout.splitlines():
        if line.endswith(":") and line.startswith("/sys/class/devfreq/"):
            if current_path is not None:
                entry = _build_devfreq_entry(current_path, buf)
                if not best and _devfreq_is_gpu(entry):
                    best = entry
            current_path = line.rstrip(":")
            buf = []
        else:
            buf.append(line)
    if current_path is not None:
        entry = _build_devfreq_entry(current_path, buf)
        if not best and _devfreq_is_gpu(entry):
            best = entry
    return best


def _devfreq_is_gpu(entry: dict[str, Any]) -> bool:
    haystack = (entry.get("name", "") + " " + entry.get("path", "")).lower()
    return any(k in haystack for k in _DEVFREQ_KEYWORDS)


def _build_devfreq_entry(path: str, values: list[str]) -> dict[str, Any]:
    vals = [v.strip() for v in values if v.strip()]
    name = vals[0] if vals else ""
    cur = _safe_int(vals[1]) if len(vals) >= 2 else 0
    max_ = _safe_int(vals[2]) if len(vals) >= 3 else 0
    min_ = _safe_int(vals[3]) if len(vals) >= 4 else 0
    gov = vals[4] if len(vals) >= 5 else ""
    # If order differs (some kernels omit name), best-effort extract numerics
    if cur == 0 and len(vals) >= 1 and vals[0].isdigit():
        name = ""
        cur = _safe_int(vals[0])
        max_ = _safe_int(vals[1]) if len(vals) >= 2 else 0
        min_ = _safe_int(vals[2]) if len(vals) >= 3 else 0
        gov = vals[3] if len(vals) >= 4 else ""
    return {"path": path, "name": name, "cur": cur, "max": max_, "min": min_, "gov": gov}


def _safe_int(s: str) -> int:
    try:
        return int(s.strip())
    except (ValueError, AttributeError):
        return 0


def _parse_single_int(stdout: str, *, default: int = 0) -> int:
    for line in stdout.splitlines():
        s = line.strip().rstrip("%")
        if s.isdigit():
            return int(s)
    return default


def _detect_gpu_vendor(text: str) -> str:
    t = text.lower()
    if "mali" in t:
        return "arm"
    if "adreno" in t:
        return "qualcomm"
    if "powervr" in t:
        return "imagination"
    if "videocore" in t:
        return "broadcom"
    return ""


# ─── Panel: security ─────────────────────────────────────────────────


@dataclass(frozen=True)
class SecurityInfo:
    verified_boot_state: str      # green / yellow / orange / red / ""
    avb_version: str
    verity_mode: str              # enforcing / logging / disabled / ""
    crypto_state: str             # encrypted / unencrypted / unsupported / ""
    crypto_type: str              # file / block / ""
    file_encryption: str          # aes-256-xts / adiantum / ""
    selinux_mode: str             # enforcing / permissive
    selinux_policy_version: str
    oem_unlock_allowed: bool
    oem_unlock_supported: bool
    adb_secure: bool

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


async def security(t: Transport, *, device: str | None = None) -> Result[SecurityInfo]:
    start = perf_counter()
    props_out = await _shell_or_empty(t, "getprop", timeout=10)
    if not props_out:
        return fail(
            code="ADB_COMMAND_FAILED",
            message="getprop returned nothing",
            category="transport",
            timing_ms=_elapsed_ms(start),
        )
    props = _parse_getprop(props_out)

    selinux_out = await _shell_or_empty(t, "getenforce 2>/dev/null", timeout=3)
    pv_out = await _shell_or_empty(
        t, "cat /sys/fs/selinux/policyvers 2>/dev/null", timeout=3
    )
    policy_version = pv_out.strip() if pv_out.strip().isdigit() else ""

    info = SecurityInfo(
        verified_boot_state=props.get("ro.boot.verifiedbootstate", ""),
        avb_version=props.get("ro.boot.avb_version", ""),
        verity_mode=props.get("ro.boot.veritymode", ""),
        crypto_state=props.get("ro.crypto.state", ""),
        crypto_type=props.get("ro.crypto.type", ""),
        file_encryption=(
            props.get("ro.crypto.volume.metadata.encryption", "")
            or props.get("fbe.contents", "")
            or props.get("ro.crypto.file_encryption", "")
        ),
        selinux_mode=selinux_out.strip().lower(),
        selinux_policy_version=policy_version,
        oem_unlock_allowed=_prop_bool(props.get("sys.oem_unlock_allowed", "")),
        oem_unlock_supported=_prop_bool(
            props.get("ro.oem_unlock_supported", ""),
            truthy_values=("1", "true"),
        ),
        adb_secure=_prop_bool(
            props.get("ro.adb.secure", ""),
            truthy_values=("1", "true"),
        ),
    )
    return ok(data=info, timing_ms=_elapsed_ms(start))


def _prop_bool(value: str, *, truthy_values: tuple[str, ...] = ("true", "1")) -> bool:
    return value.strip().lower() in truthy_values


# ─── Panel: display ──────────────────────────────────────────────────


@dataclass(frozen=True)
class DisplayInfo:
    width: int
    height: int
    density: int                  # dpi (Physical density)
    density_override: int         # user-overridden density if any
    refresh_rate_hz: float
    brightness: int               # 0-255; -1 if not readable
    rotation: int

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


async def display(t: Transport, *, device: str | None = None) -> Result[DisplayInfo]:
    start = perf_counter()

    size_out = await _shell_or_empty(t, "wm size 2>/dev/null", timeout=5)
    density_out = await _shell_or_empty(t, "wm density 2>/dev/null", timeout=5)
    dumpsys_out = await _shell_or_empty(
        t,
        "dumpsys display 2>/dev/null | head -80",
        timeout=10,
    )
    brightness_out = await _shell_or_empty(
        t, "settings get system screen_brightness 2>/dev/null", timeout=5
    )

    width, height = _parse_wm_size(size_out)
    density, density_override = _parse_wm_density(density_out)
    refresh = _extract_refresh_rate(dumpsys_out)
    rotation = _extract_rotation(dumpsys_out)
    brightness = _safe_int(brightness_out)
    if brightness == 0 and "null" in brightness_out.lower():
        brightness = -1

    return ok(
        data=DisplayInfo(
            width=width,
            height=height,
            density=density,
            density_override=density_override,
            refresh_rate_hz=refresh,
            brightness=brightness,
            rotation=rotation,
        ),
        timing_ms=_elapsed_ms(start),
    )


def _parse_wm_size(stdout: str) -> tuple[int, int]:
    """'Physical size: 1080x2400' (and optionally 'Override size: ...')."""
    for line in stdout.splitlines():
        m = re.search(r"(\d+)\s*x\s*(\d+)", line)
        if m:
            return int(m.group(1)), int(m.group(2))
    return 0, 0


def _parse_wm_density(stdout: str) -> tuple[int, int]:
    """'Physical density: 420' + optional 'Override density: 480'."""
    phys = 0
    override = 0
    for line in stdout.splitlines():
        m = re.search(r"(?i)physical density:\s*(\d+)", line)
        if m:
            phys = int(m.group(1))
        m = re.search(r"(?i)override density:\s*(\d+)", line)
        if m:
            override = int(m.group(1))
    return phys, override


def _extract_refresh_rate(stdout: str) -> float:
    for line in stdout.splitlines():
        # 'mRefreshRate=60.000004' / 'fps=60.0'
        m = re.search(
            r"(?:mRefreshRate|fps)\s*=\s*([\d.]+)",
            line,
        )
        if m:
            try:
                return round(float(m.group(1)), 2)
            except ValueError:
                pass
    return 0.0


def _extract_rotation(stdout: str) -> int:
    for line in stdout.splitlines():
        m = re.search(r"(?:mRotation|orientation)\s*=\s*(\d+)", line)
        if m:
            return int(m.group(1))
    return 0


# ─── Panel: packages ─────────────────────────────────────────────────


@dataclass(frozen=True)
class PackagesInfo:
    total: int
    system_count: int
    user_count: int
    disabled_count: int
    system_samples: list[str]
    user_samples: list[str]

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


async def packages(
    t: Transport,
    *,
    device: str | None = None,
    sample_limit: int = 10,
) -> Result[PackagesInfo]:
    start = perf_counter()
    sys_out = await _shell_or_empty(t, "pm list packages -s 2>/dev/null", timeout=15)
    user_out = await _shell_or_empty(t, "pm list packages -3 2>/dev/null", timeout=15)
    dis_out = await _shell_or_empty(
        t, "pm list packages -d 2>/dev/null", timeout=10
    )

    if not sys_out and not user_out:
        return fail(
            code="PM_UNAVAILABLE",
            message="pm list packages returned nothing",
            suggestion="Device may not be fully booted or pm service unreachable",
            category="capability",
            timing_ms=_elapsed_ms(start),
        )

    sys_pkgs = _parse_pm_list(sys_out)
    user_pkgs = _parse_pm_list(user_out)
    dis_pkgs = _parse_pm_list(dis_out)

    return ok(
        data=PackagesInfo(
            total=len(sys_pkgs) + len(user_pkgs),
            system_count=len(sys_pkgs),
            user_count=len(user_pkgs),
            disabled_count=len(dis_pkgs),
            system_samples=sys_pkgs[:sample_limit],
            user_samples=user_pkgs[:sample_limit],
        ),
        timing_ms=_elapsed_ms(start),
    )


def _parse_pm_list(stdout: str) -> list[str]:
    """`pm list packages` emits 'package:<name>' one per line."""
    out: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("package:"):
            out.append(line[len("package:"):])
    return out


# ─── Panel: processes ────────────────────────────────────────────────


@dataclass(frozen=True)
class ProcessEntry:
    pid: int
    user: str
    cpu_pct: float
    mem_pct: float
    rss_kb: int
    name: str

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class ProcessesInfo:
    count: int
    top_cpu: list[ProcessEntry]
    top_mem: list[ProcessEntry]

    def to_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "top_cpu": [p.to_dict() for p in self.top_cpu],
            "top_mem": [p.to_dict() for p in self.top_mem],
        }


async def processes(
    t: Transport,
    *,
    device: str | None = None,
    limit: int = 15,
) -> Result[ProcessesInfo]:
    """Top processes by CPU and by memory.

    Forces a known column layout via `-o` so parsing is deterministic
    regardless of toybox/procps quirks (some versions merge `S` with
    `%CPU` as `S[%CPU]`, others omit headers entirely with `-q`).
    """
    start = perf_counter()

    cmd = (
        f"top -n 1 -b -m {limit * 2} "
        "-o PID,USER,%CPU,%MEM,RES,CMDLINE 2>/dev/null"
    )
    top_out = await _shell_or_empty(t, cmd, timeout=15)
    entries = _parse_toybox_top(top_out)

    # Older toybox doesn't honor -o; retry without it and fall back to
    # column discovery on whatever header it emits.
    if not entries:
        top2 = await _shell_or_empty(
            t, f"top -n 1 -b -m {limit * 2} 2>/dev/null", timeout=15
        )
        entries = _parse_toybox_top(top2)

    # Count all processes via ps (cheap)
    ps_out = await _shell_or_empty(t, "ps -A 2>/dev/null | wc -l", timeout=5)
    total = _safe_int(ps_out)
    if total > 0:
        total -= 1  # subtract header line

    if not entries:
        # Still return a usable response with the count only.
        return ok(
            data=ProcessesInfo(count=total, top_cpu=[], top_mem=[]),
            timing_ms=_elapsed_ms(start),
        )

    top_cpu = sorted(entries, key=lambda e: -e.cpu_pct)[:limit]
    top_mem = sorted(entries, key=lambda e: -e.rss_kb)[:limit]
    return ok(
        data=ProcessesInfo(count=total, top_cpu=top_cpu, top_mem=top_mem),
        timing_ms=_elapsed_ms(start),
    )


def _parse_toybox_top(stdout: str) -> list[ProcessEntry]:
    """Parse the tabular body of `top -n 1 -b`.

    Toybox sometimes merges adjacent columns into one header token like
    `S[%CPU]` or `RES[CMDLINE]` while still printing two whitespace-
    separated fields per row. Expand those before lookup so column
    indexes line up with the actual row tokens.
    """
    lines = stdout.splitlines()
    header_idx = _find_top_header(lines)
    if header_idx < 0:
        return []

    cols = _expand_merged_top_header(lines[header_idx].split())
    try:
        pid_i = _col_index(cols, ("PID",))
        user_i = _col_index(cols, ("USER", "UID"))
        cpu_i = _col_index(cols, ("%CPU", "CPU%"))
        mem_i = _col_index(cols, ("%MEM", "MEM%"))
        rss_i = _col_index(cols, ("RES", "RSS"))
        name_i = _col_index(cols, ("CMDLINE", "ARGS", "NAME", "CMD", "COMMAND"))
    except KeyError:
        return []

    out: list[ProcessEntry] = []
    needed = max(pid_i, user_i, cpu_i, mem_i, rss_i, name_i) + 1
    for row in lines[header_idx + 1:]:
        if not row.strip():
            continue
        # Split exactly once for each non-CMD column so the trailing
        # CMD/ARGS column captures the remainder (it can contain spaces).
        fields = row.split(None, len(cols) - 1)
        if len(fields) < needed:
            continue
        try:
            pid = int(fields[pid_i])
        except ValueError:
            continue
        out.append(ProcessEntry(
            pid=pid,
            user=fields[user_i],
            cpu_pct=_safe_float(fields[cpu_i]),
            mem_pct=_safe_float(fields[mem_i]),
            rss_kb=_parse_rss(fields[rss_i]),
            name=fields[name_i].strip(),
        ))
    return out


def _expand_merged_top_header(raw_cols: list[str]) -> list[str]:
    """`['S[%CPU]', 'RES[CMDLINE]']` → `['S', '%CPU', 'RES', 'CMDLINE']`.

    Toybox prints two row tokens for these merged headers, so we must
    treat the header as two columns to keep row indexes aligned.
    """
    out: list[str] = []
    for c in raw_cols:
        m = re.match(r"^([^\[]+)\[([^\]]+)\]$", c)
        if m:
            out.append(m.group(1))
            out.append(m.group(2))
        else:
            out.append(c)
    return out


def _find_top_header(lines: list[str]) -> int:
    for i, line in enumerate(lines):
        if "PID" in line and ("USER" in line or "UID" in line) and (
            "CPU" in line or "%CPU" in line.upper()
        ):
            return i
    return -1


def _col_index(cols: list[str], candidates: tuple[str, ...]) -> int:
    """Locate a column by name, with substring tolerance.

    Some toybox versions merge fields, e.g. the state + %CPU column shows
    up as `S[%CPU]`. Exact match wins; substring is the fallback.
    """
    for cand in candidates:
        if cand in cols:
            return cols.index(cand)
    for cand in candidates:
        for i, col in enumerate(cols):
            if cand in col:
                return i
    raise KeyError(candidates)


def _safe_float(s: str) -> float:
    try:
        return float(s.strip().rstrip("%"))
    except (ValueError, AttributeError):
        return 0.0


def _parse_rss(s: str) -> int:
    """RSS in toybox is already KB; procps may use 'K'/'M'/'G' suffix."""
    s = s.strip()
    if not s:
        return 0
    suffix = s[-1].upper()
    multipliers = {"K": 1, "M": 1024, "G": 1024 * 1024}
    if suffix in multipliers:
        try:
            return int(float(s[:-1]) * multipliers[suffix])
        except ValueError:
            return 0
    try:
        return int(s)
    except ValueError:
        return 0


# ─── Aggregate: run all panels in parallel ────────────────────────────────


_PANELS = {
    "system": system,
    "cpu": cpu,
    "gpu": gpu,
    "memory": memory,
    "storage": storage,
    "network": network,
    "battery": battery,
    "security": security,
    "display": display,
    "packages": packages,
    "processes": processes,
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
