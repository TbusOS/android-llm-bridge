"""diagnose capability — bugreport / ANR / tombstone / devinfo.

See docs/capabilities/diagnose.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alb.infra.result import Result, fail, ok
from alb.infra.workspace import iso_timestamp, resolve_capture_path, workspace_path
from alb.transport.base import Transport


# ─── Models ────────────────────────────────────────────────────────
@dataclass(frozen=True)
class BugreportResult:
    zip_path: str
    txt_path: str | None
    duration_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "zip_path": self.zip_path,
            "txt_path": self.txt_path,
            "duration_ms": self.duration_ms,
        }


@dataclass(frozen=True)
class PullBundleResult:
    kind: str
    count: int
    files: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "count": self.count, "files": self.files}


@dataclass(frozen=True)
class DeviceInfo:
    model: str
    brand: str
    manufacturer: str
    sdk: str
    release: str
    build_fingerprint: str
    abi: str
    hardware: str
    serialno: str
    uptime_sec: int
    battery_level: int
    storage: dict[str, str]
    extras: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "brand": self.brand,
            "manufacturer": self.manufacturer,
            "sdk": self.sdk,
            "release": self.release,
            "build_fingerprint": self.build_fingerprint,
            "abi": self.abi,
            "hardware": self.hardware,
            "serialno": self.serialno,
            "uptime_sec": self.uptime_sec,
            "battery_level": self.battery_level,
            "storage": self.storage,
            "extras": self.extras,
        }


# ─── bugreport ─────────────────────────────────────────────────────
async def bugreport(
    transport: Transport,
    *,
    device: str | None = None,
    output: Path | str | None = None,
) -> Result[BugreportResult]:
    """Trigger `bugreportz` and pull the zip into workspace (or `output`).

    LLM: takes 60-180s. Returns only the zip path; use alb_log_search on
    the extracted main.txt for analysis.

    Args:
        output: Optional override for the zip path (same rules as
            `capture_uart` / `collect_logcat` / `resolve_capture_path`):
            - None → workspace/.../bugreports/<ts>-bugreport.zip
            - Existing dir or trailing "/" → that dir + auto file name
            - Anything else → exact file path
    """
    if transport.name != "adb":
        return fail(
            code="TRANSPORT_NOT_SUPPORTED",
            message="bugreport requires adb transport",
            suggestion="Run: alb setup adb",
            category="transport",
        )

    # `bugreportz -p` prints progress; without arg it writes to a canonical
    # path on-device and echoes OK:/path/to.zip
    r = await transport.shell("bugreportz", timeout=300)
    if not r.ok:
        return fail(
            code=r.error_code or "BUGREPORT_FAILED",
            message=r.stderr.strip() or "bugreportz returned error",
            suggestion="Older devices may need `adb bugreport` instead",
            category="capability",
            details={"stderr": r.stderr},
            timing_ms=r.duration_ms,
        )

    remote_zip = _parse_bugreportz_output(r.stdout)
    if not remote_zip:
        return fail(
            code="BUGREPORT_FAILED",
            message=f"Could not parse bugreportz output: {r.stdout[:200]!r}",
            suggestion="Run adb bugreport manually to verify",
            category="capability",
        )

    local_zip = resolve_capture_path(
        output,
        f"{iso_timestamp()}-bugreport.zip",
        default_category="bugreports",
        device=device,
    )

    pull_r = await transport.pull(remote_zip, local_zip)
    if not pull_r.ok:
        return fail(
            code=pull_r.error_code or "ADB_COMMAND_FAILED",
            message="Failed to pull bugreport zip",
            suggestion="Check device storage; try again",
            category="transport",
            details={"stderr": pull_r.stderr},
        )

    return ok(
        data=BugreportResult(
            zip_path=str(local_zip),
            txt_path=None,
            duration_ms=r.duration_ms + pull_r.duration_ms,
        ),
        artifacts=[local_zip],
        timing_ms=r.duration_ms + pull_r.duration_ms,
    )


# ─── ANR / tombstone ───────────────────────────────────────────────
async def anr_pull(
    transport: Transport,
    *,
    clear_after: bool = False,
    device: str | None = None,
) -> Result[PullBundleResult]:
    """Pull /data/anr/*.txt into workspace/.../anr/<ts>/."""
    return await _pull_bundle(
        transport,
        remote_glob="/data/anr",
        kind="anr",
        clear_after=clear_after,
        device=device,
    )


async def tombstone_pull(
    transport: Transport,
    *,
    limit: int = 10,
    device: str | None = None,
) -> Result[PullBundleResult]:
    """Pull /data/tombstones/* into workspace/.../tombstones/<ts>/."""
    return await _pull_bundle(
        transport,
        remote_glob="/data/tombstones",
        kind="tombstones",
        limit=limit,
        device=device,
    )


async def _pull_bundle(
    transport: Transport,
    *,
    remote_glob: str,
    kind: str,
    device: str | None,
    clear_after: bool = False,
    limit: int | None = None,
) -> Result[PullBundleResult]:
    ls = await transport.shell(f"ls {remote_glob} 2>/dev/null", timeout=10)
    if not ls.ok:
        return fail(
            code=ls.error_code or "ADB_COMMAND_FAILED",
            message=ls.stderr or f"listing {remote_glob} failed",
            suggestion="Device may be offline",
            category="transport",
        )

    names = [n for n in (ls.stdout or "").split() if n]
    if not names:
        empty_code = "NO_ANR_FOUND" if kind == "anr" else "NO_TOMBSTONE_FOUND"
        return ok(data=PullBundleResult(kind=kind, count=0, files=[]),
                  artifacts=[])

    if limit:
        names = names[-limit:]

    dst_dir = workspace_path(kind, iso_timestamp(), device=device)
    dst_dir.mkdir(parents=True, exist_ok=True)

    saved: list[str] = []
    for n in names:
        rem = f"{remote_glob}/{n}"
        # round11 SEC-3: `n` comes from the device's `ls` output — basename
        # it so a hostile / malformed name (e.g. "../../etc/x") can't escape
        # dst_dir when building the local write path. Skip empties / dotdirs.
        safe = Path(n).name
        if not safe or safe in {".", ".."}:
            continue
        local = dst_dir / safe
        r = await transport.pull(rem, local)
        if r.ok:
            saved.append(str(local))

    if clear_after and kind == "anr":
        perm = await transport.check_permissions(
            "diagnose.anr_clear", {"path": remote_glob}
        )
        if perm.behavior == "allow":
            await transport.shell(f"rm -f {remote_glob}/*.txt", timeout=10)

    return ok(
        data=PullBundleResult(kind=kind, count=len(saved), files=saved),
        artifacts=[Path(s) for s in saved],
    )


# ─── devinfo ───────────────────────────────────────────────────────
async def devinfo(transport: Transport) -> Result[DeviceInfo]:
    """Collect a structured device snapshot (fast, no artifact)."""
    props_r = await transport.shell("getprop", timeout=15)
    if not props_r.ok:
        return fail(
            code=props_r.error_code or "ADB_COMMAND_FAILED",
            message="getprop failed",
            suggestion="Device may be offline or unauthorized",
            category="transport",
        )
    props = _parse_getprop(props_r.stdout)

    bat_r = await transport.shell("dumpsys battery 2>/dev/null", timeout=10)
    battery_level = _extract_battery_level(bat_r.stdout if bat_r.ok else "")

    up_r = await transport.shell("cat /proc/uptime", timeout=5)
    uptime_sec = 0
    if up_r.ok:
        try:
            uptime_sec = int(float(up_r.stdout.split()[0]))
        except (ValueError, IndexError):
            pass

    df_r = await transport.shell("df /data /sdcard 2>/dev/null", timeout=10)
    storage = _parse_df(df_r.stdout if df_r.ok else "")

    cpu_r = await transport.shell("cat /proc/cpuinfo 2>/dev/null", timeout=5)
    cpuinfo = cpu_r.stdout if cpu_r.ok else ""
    cpu_cores = _count_cpu_cores(cpuinfo)
    per_core = _parse_cpuinfo_cores(cpuinfo)
    layout = await _read_cpu_layout(transport)
    core_khz = layout["max_khz"]
    # The fastest core, not cpu0: on a big.LITTLE board cpu0 is usually in
    # the slow cluster, and "8 cores · <cpu0's GHz>" under-reports a board
    # whose big cores run faster (seen 2026-11-06).
    cpu_max_khz = max(core_khz.values(), default=0)

    mem_r = await transport.shell("cat /proc/meminfo 2>/dev/null", timeout=5)
    ram_total_kb, ram_avail_kb = _parse_meminfo(mem_r.stdout if mem_r.ok else "")

    display = await _collect_display(transport)

    temp_c = await _read_thermal_c(transport)

    soc = (
        props.get("ro.boot.soc.product")
        or props.get("ro.hardware.chipname")
        or props.get("ro.board.platform")
        or ""
    )

    extras: dict[str, Any] = {
        "soc": soc,
        # What the system says the chip is (Android 12+ fills these from the
        # vendor); `soc` above stays the old fallback chain, which ends at the
        # board platform name rather than the chip.
        "soc_model": props.get("ro.soc.model", ""),
        "soc_manufacturer": props.get("ro.soc.manufacturer", ""),
        "board_platform": props.get("ro.board.platform", ""),
        # The kernel's number for the chip (Qualcomm boards). Shown raw: the
        # kernel's own name table may not list it, and a number beats a guess.
        "soc_id": layout["soc_id"],
        "cpu_cores": cpu_cores,
        "cpu_max_khz": cpu_max_khz,
        "cpu_arch": _cpu_arch(per_core),
        "cpu_clusters": _cpu_clusters(per_core, core_khz),
        # Which binaries the system runs. abi_list64 empty = 32-bit apps only,
        # whatever the CPU could do. Read from props on purpose, not from
        # `uname -m`: a 64-bit kernel answers `armv8l` to a shell running in
        # 32-bit mode (arch/arm64/include/asm/compat.h COMPAT_UTS_MACHINE), so
        # uname cannot tell the kernel's width.
        "abi_list": props.get("ro.product.cpu.abilist", ""),
        "abi_list64": props.get("ro.product.cpu.abilist64", ""),
        "ram_total_kb": ram_total_kb,
        "ram_avail_kb": ram_avail_kb,
        "display": display,
        "temp_c": temp_c,
    }

    info = DeviceInfo(
        model=props.get("ro.product.model", ""),
        brand=props.get("ro.product.brand", ""),
        manufacturer=props.get("ro.product.manufacturer", ""),
        sdk=props.get("ro.build.version.sdk", ""),
        release=props.get("ro.build.version.release", ""),
        build_fingerprint=props.get("ro.build.fingerprint", ""),
        abi=props.get("ro.product.cpu.abi", ""),
        hardware=props.get("ro.hardware", ""),
        serialno=props.get("ro.serialno", ""),
        uptime_sec=uptime_sec,
        battery_level=battery_level,
        storage=storage,
        extras=extras,
    )
    return ok(data=info)


# ─── device_system (DEBT-022 PR-B · inspect 详情页 full system dump) ─

@dataclass(frozen=True)
class DeviceSystem:
    """Composite system snapshot for the inspect detail page.

    All fields are best-effort — any individual collector failure
    leaves its slot at "" / [] / {} so partial data still renders.
    Fast (~1-2 s on a healthy device); not a substitute for
    bugreport for forensic depth.
    """

    props: dict[str, str]
    partitions: list[dict[str, str]]
    mounts: list[dict[str, str]]
    block_devices: list[dict[str, str]]
    meminfo: dict[str, int]
    storage: dict[str, dict[str, str]]
    network: list[dict[str, str]]
    battery: dict[str, str]
    thermal: list[dict[str, str]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "props": self.props,
            "partitions": self.partitions,
            "mounts": self.mounts,
            "block_devices": self.block_devices,
            "meminfo": self.meminfo,
            "storage": self.storage,
            "network": self.network,
            "battery": self.battery,
            "thermal": self.thermal,
        }


async def device_system(transport: Transport) -> Result[DeviceSystem]:
    """Composite full-system snapshot. ADR-028 (a) — 'system' endpoint.

    Failure modes:
      - getprop fails → return ok=False (every other field depends on
        being able to talk to the shell at all; if getprop is dead, so
        is everything)
      - any other collector fails → that field is empty, others
        continue
    """
    props_r = await transport.shell("getprop", timeout=15)
    if not props_r.ok:
        return fail(
            code=props_r.error_code or "ADB_COMMAND_FAILED",
            message="getprop failed",
            suggestion="Device may be offline or unauthorized",
            category="transport",
        )
    props = _parse_getprop(props_r.stdout)

    partitions = await _collect_partitions(transport)
    mounts = await _collect_mounts(transport)
    block_devices = await _collect_block_devices(transport)
    meminfo_full = await _collect_meminfo_full(transport)
    storage = await _collect_storage(transport)
    network = await _collect_network(transport)
    battery_full = await _collect_battery_full(transport)
    thermal = await _collect_thermal(transport)

    snapshot = DeviceSystem(
        props=props,
        partitions=partitions,
        mounts=mounts,
        block_devices=block_devices,
        meminfo=meminfo_full,
        storage=storage,
        network=network,
        battery=battery_full,
        thermal=thermal,
    )
    return ok(data=snapshot)


async def _collect_partitions(transport: Transport) -> list[dict[str, str]]:
    """`ls -la /dev/block/by-name/` → [{name, target, dev?}, ...]"""
    r = await transport.shell("ls -la /dev/block/by-name/ 2>/dev/null", timeout=10)
    if not r.ok:
        return []
    return _parse_partitions_listing(r.stdout)


def _parse_partitions_listing(stdout: str) -> list[dict[str, str]]:
    """ls -la output looks like:
       lrwxr-xr-x 1 root root 21 ... boot -> /dev/block/mmcblk0p20
    """
    out: list[dict[str, str]] = []
    for line in stdout.splitlines():
        if " -> " not in line:
            continue
        head, _, target = line.partition(" -> ")
        # name is the last whitespace-separated token in head
        parts = head.rstrip().split()
        if not parts:
            continue
        name = parts[-1]
        out.append({"name": name, "target": target.strip()})
    return out


async def _collect_mounts(transport: Transport) -> list[dict[str, str]]:
    r = await transport.shell("cat /proc/mounts 2>/dev/null", timeout=10)
    if not r.ok:
        return []
    out: list[dict[str, str]] = []
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 4:
            out.append(
                {
                    "device": parts[0],
                    "mount_point": parts[1],
                    "fstype": parts[2],
                    "opts": parts[3],
                }
            )
    return out


async def _collect_block_devices(transport: Transport) -> list[dict[str, str]]:
    """`cat /proc/partitions` → [{major, minor, blocks_kib, name}, ...]"""
    r = await transport.shell("cat /proc/partitions 2>/dev/null", timeout=10)
    if not r.ok:
        return []
    out: list[dict[str, str]] = []
    for line in r.stdout.splitlines()[1:]:  # skip header
        parts = line.split()
        if len(parts) >= 4:
            out.append(
                {
                    "major": parts[0],
                    "minor": parts[1],
                    "size_kib": parts[2],
                    "name": parts[3],
                }
            )
    return out


async def _collect_meminfo_full(transport: Transport) -> dict[str, int]:
    """Full /proc/meminfo as {key: kB int}."""
    r = await transport.shell("cat /proc/meminfo 2>/dev/null", timeout=5)
    if not r.ok:
        return {}
    out: dict[str, int] = {}
    for line in r.stdout.splitlines():
        if ":" not in line:
            continue
        key, _, rest = line.partition(":")
        rest = rest.strip().split()
        if rest:
            try:
                out[key.strip()] = int(rest[0])
            except ValueError:
                pass
    return out


async def _collect_storage(transport: Transport) -> dict[str, dict[str, str]]:
    """df on common mount points → {mount: {used, avail, use_pct}}."""
    r = await transport.shell(
        "df /data /sdcard /storage/emulated /cache 2>/dev/null", timeout=10
    )
    if not r.ok:
        return {}
    out: dict[str, dict[str, str]] = {}
    for line in r.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 6:
            mount = parts[-1]
            out[mount] = {
                "used_kib": parts[-4],
                "avail_kib": parts[-3],
                "use_pct": parts[-2],
                "device": parts[0],
            }
    return out


async def _collect_network(transport: Transport) -> list[dict[str, str]]:
    """`ip addr` → [{iface, state, mac, ipv4?, ipv6?}, ...]"""
    r = await transport.shell("ip -o addr 2>/dev/null", timeout=10)
    if not r.ok:
        # Fallback to ifconfig
        r = await transport.shell("ifconfig -a 2>/dev/null", timeout=10)
        if not r.ok:
            return []
        return _parse_ifconfig(r.stdout)
    return _parse_ip_oneliner(r.stdout)


def _parse_ip_oneliner(stdout: str) -> list[dict[str, str]]:
    """`ip -o addr` lines look like:
       2: wlan0    inet 192.168.1.10/24 brd ... scope global
    """
    by_iface: dict[str, dict[str, str]] = {}
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        iface = parts[1]
        family = parts[2]
        addr = parts[3]
        entry = by_iface.setdefault(iface, {"iface": iface})
        if family == "inet":
            entry["ipv4"] = addr
        elif family == "inet6":
            entry["ipv6"] = addr
    return list(by_iface.values())


def _parse_ifconfig(stdout: str) -> list[dict[str, str]]:
    """Light ifconfig parser — Android busybox / toybox flavour. Only
    pulls iface name + ipv4 + mac for the inspect summary."""
    out: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw in stdout.splitlines():
        line = raw.rstrip()
        if not line:
            current = None
            continue
        if not line.startswith((" ", "\t")):
            iface = line.split(":", 1)[0].split()[0]
            current = {"iface": iface}
            out.append(current)
            continue
        if current is None:
            continue
        stripped = line.strip()
        if "inet " in stripped:
            for tok in stripped.split():
                if tok.count(".") == 3 and "/" not in tok:
                    current["ipv4"] = tok
                    break
        if "ether " in stripped:
            tok = stripped.split("ether ", 1)[1].split()[0]
            current["mac"] = tok
    return out


async def _collect_battery_full(transport: Transport) -> dict[str, str]:
    """Full `dumpsys battery` as a flat KV dict."""
    r = await transport.shell("dumpsys battery 2>/dev/null", timeout=10)
    if not r.ok:
        return {}
    out: dict[str, str] = {}
    for line in r.stdout.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            out[key.strip()] = value.strip()
    return out


async def _collect_thermal(transport: Transport) -> list[dict[str, str]]:
    """List thermal zones with type + temp."""
    r = await transport.shell(
        "for d in /sys/class/thermal/thermal_zone*; do "
        "echo \"$(basename $d)|$(cat $d/type 2>/dev/null)|$(cat $d/temp 2>/dev/null)\"; "
        "done 2>/dev/null",
        timeout=10,
    )
    if not r.ok:
        return []
    out: list[dict[str, str]] = []
    for line in r.stdout.splitlines():
        parts = line.split("|")
        if len(parts) >= 3 and parts[0].startswith("thermal_zone"):
            try:
                temp_c = f"{int(parts[2]) / 1000:.1f}"
            except ValueError:
                temp_c = ""
            out.append({"zone": parts[0], "type": parts[1], "temp_c": temp_c})
    return out


# ─── extra collectors (DEBT-022 PR-A · device card 丰富化) ─────────
# One round trip for every core's top frequency and the kernel's SoC number.
# Each value on its own `key=value` line, so a serial console that echoes the
# command (the echo starts with `echo`) cannot be mistaken for an answer.
_CPU_LAYOUT_CMD = (
    'echo "soc_id=$(cat /sys/devices/soc0/soc_id 2>/dev/null)"; '
    "for c in /sys/devices/system/cpu/cpu[0-9]*; do "
    'echo "${c##*/}=$(cat $c/cpufreq/cpuinfo_max_freq 2>/dev/null)"; done'
)
_CPU_LAYOUT_LINE_RE = re.compile(r"^(soc_id|cpu(\d+))=(\S*)$")


async def _read_cpu_layout(transport: Transport) -> dict[str, Any]:
    """{"soc_id": str, "max_khz": {cpu index: kHz}}. Best effort: a core
    without cpufreq is left out, a board without soc0 gives soc_id ""."""
    r = await transport.shell(_CPU_LAYOUT_CMD, timeout=10)
    return _parse_cpu_layout(r.stdout if r.ok else "")


def _parse_cpu_layout(stdout: str) -> dict[str, Any]:
    soc_id = ""
    max_khz: dict[int, int] = {}
    for line in stdout.splitlines():
        m = _CPU_LAYOUT_LINE_RE.match(line.strip())
        if not m:
            continue
        if m.group(1) == "soc_id":
            soc_id = m.group(3)
        elif m.group(3).isdigit() and int(m.group(3)) > 0:
            max_khz[int(m.group(2))] = int(m.group(3))
    return {"soc_id": soc_id, "max_khz": max_khz}


# Core names by (CPU implementer, CPU part) from /proc/cpuinfo, as the kernel
# names them in arch/arm64/include/asm/cputype.h (ARM_CPU_PART_* and
# QCOM_CPU_PART_*). cpuinfo gives only the two numbers; a pair missing here is
# shown raw ("0x51/0x9ff") rather than guessed.
_CPU_CORE_NAMES: dict[tuple[str, str], str] = {
    ("0x41", "0xd03"): "Cortex-A53",
    ("0x41", "0xd04"): "Cortex-A35",
    ("0x41", "0xd05"): "Cortex-A55",
    ("0x41", "0xd07"): "Cortex-A57",
    ("0x41", "0xd08"): "Cortex-A72",
    ("0x41", "0xd09"): "Cortex-A73",
    ("0x41", "0xd0a"): "Cortex-A75",
    ("0x41", "0xd0b"): "Cortex-A76",
    ("0x41", "0xd0c"): "Neoverse-N1",
    ("0x41", "0xd0d"): "Cortex-A77",
    ("0x41", "0xd40"): "Neoverse-V1",
    ("0x41", "0xd41"): "Cortex-A78",
    ("0x41", "0xd44"): "Cortex-X1",
    ("0x41", "0xd46"): "Cortex-A510",
    ("0x41", "0xd47"): "Cortex-A710",
    ("0x41", "0xd48"): "Cortex-X2",
    ("0x41", "0xd49"): "Neoverse-N2",
    ("0x41", "0xd4b"): "Cortex-A78C",
    ("0x41", "0xd4c"): "Cortex-X1C",
    ("0x41", "0xd4d"): "Cortex-A715",
    ("0x41", "0xd4e"): "Cortex-X3",
    ("0x41", "0xd80"): "Cortex-A520",
    ("0x41", "0xd81"): "Cortex-A720",
    ("0x41", "0xd82"): "Cortex-X4",
    ("0x51", "0x200"): "Kryo",
    # 0x800 is also QCOM_CPU_PART_FALKOR_V1, a server core no Android board has.
    ("0x51", "0x800"): "Kryo 2xx Gold",
    ("0x51", "0x801"): "Kryo 2xx Silver",
    ("0x51", "0x802"): "Kryo 3xx Gold",
    ("0x51", "0x803"): "Kryo 3xx Silver",
    ("0x51", "0x804"): "Kryo 4xx Gold",
    ("0x51", "0x805"): "Kryo 4xx Silver",
    ("0x51", "0xc00"): "Falkor",
}


def _parse_cpuinfo_cores(stdout: str) -> dict[int, dict[str, str]]:
    """{processor index: {"implementer", "part", "architecture"}} from
    /proc/cpuinfo, lower-cased. Fields a kernel does not print are absent."""
    keys = {
        "cpu implementer": "implementer",
        "cpu part": "part",
        "cpu architecture": "architecture",
    }
    cores: dict[int, dict[str, str]] = {}
    current: int | None = None
    for line in stdout.splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key, value = key.strip().lower(), value.strip().lower()
        if key == "processor":
            current = int(value) if value.isdigit() else None
            if current is not None:
                cores.setdefault(current, {})
        elif current is not None and key in keys and value:
            cores[current][keys[key]] = value
    return cores


def _cpu_arch(cores: dict[int, dict[str, str]]) -> str:
    """"ARMv8" from `CPU architecture: 8`; "" when cpuinfo has no such field."""
    for core in cores.values():
        arch = core.get("architecture", "")
        if arch:
            return f"ARMv{arch}" if arch.isdigit() else arch
    return ""


def _cpu_clusters(
    cores: dict[int, dict[str, str]], max_khz: dict[int, int]
) -> list[dict[str, Any]]:
    """Cores grouped by kind and top frequency, in core order:
    [{"count": 4, "cpus": [0, 1, 2, 3], "core": "Kryo 2xx Silver",
      "part": "0x51/0x801", "max_khz": 1800000}, ...].
    Empty when there is nothing to tell the cores apart by."""
    groups: list[dict[str, Any]] = []
    where: dict[tuple[str, int], dict[str, Any]] = {}
    for cpu in sorted(set(cores) | set(max_khz)):
        info = cores.get(cpu, {})
        imp, part = info.get("implementer", ""), info.get("part", "")
        raw = f"{imp}/{part}" if imp and part else ""
        name = _CPU_CORE_NAMES.get((imp, part), "")
        key = (raw, max_khz.get(cpu, 0))
        group = where.get(key)
        if group is None:
            group = {"count": 0, "cpus": [], "core": name, "part": raw, "max_khz": key[1]}
            where[key] = group
            groups.append(group)
        group["count"] += 1
        group["cpus"].append(cpu)
    if not any(g["part"] or g["max_khz"] for g in groups):
        return []
    return groups


async def _collect_display(transport: Transport) -> dict[str, str]:
    """`wm size` + `wm density`. Empty dict if any fail."""
    out: dict[str, str] = {}
    size_r = await transport.shell("wm size 2>/dev/null", timeout=5)
    if size_r.ok:
        size = _parse_wm_size(size_r.stdout)
        if size:
            out["size"] = size
    dens_r = await transport.shell("wm density 2>/dev/null", timeout=5)
    if dens_r.ok:
        dens = _parse_wm_density(dens_r.stdout)
        if dens:
            out["density"] = dens
    return out


async def _read_thermal_c(transport: Transport) -> float:
    """Read thermal_zone0/temp (millicelsius). -1.0 if unavailable."""
    r = await transport.shell(
        "cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null",
        timeout=5,
    )
    if not r.ok or not r.stdout.strip():
        return -1.0
    try:
        return int(r.stdout.strip()) / 1000.0
    except ValueError:
        return -1.0


# ─── Parsers ───────────────────────────────────────────────────────
def _parse_bugreportz_output(stdout: str) -> str:
    """Parse the final `OK:/sdcard/bugreports/xxx.zip` line."""
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("OK:"):
            return line[3:].strip()
        if line.startswith("FAIL:"):
            return ""
    return ""


def _parse_getprop(stdout: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in stdout.splitlines():
        # format: [key]: [value]
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


def _extract_battery_level(stdout: str) -> int:
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("level:"):
            try:
                return int(stripped.split(":", 1)[1].strip())
            except ValueError:
                return -1
    return -1


def _parse_df(stdout: str) -> dict[str, str]:
    res: dict[str, str] = {}
    for line in stdout.splitlines()[1:]:  # skip header
        parts = line.split()
        if len(parts) >= 6:
            mount = parts[-1]
            # Example: Filesystem 1K-blocks  Used Available Use% Mounted
            res[mount] = f"used={parts[-4]} avail={parts[-3]} use%={parts[-2]}"
    return res


def _count_cpu_cores(stdout: str) -> int:
    """Count `processor	: N` lines in /proc/cpuinfo. 0 if not parseable."""
    count = 0
    for line in stdout.splitlines():
        if line.startswith("processor"):
            count += 1
    return count


def _parse_meminfo(stdout: str) -> tuple[int, int]:
    """Return (MemTotal_kB, MemAvailable_kB). 0,0 on parse failure."""
    total = 0
    avail = 0
    for line in stdout.splitlines():
        if line.startswith("MemTotal:"):
            total = _meminfo_value_kb(line)
        elif line.startswith("MemAvailable:"):
            avail = _meminfo_value_kb(line)
    return total, avail


def _meminfo_value_kb(line: str) -> int:
    # `MemTotal:        7929164 kB`
    parts = line.split()
    if len(parts) >= 2:
        try:
            return int(parts[1])
        except ValueError:
            return 0
    return 0


def _parse_wm_size(stdout: str) -> str:
    # `Physical size: 1080x2400` or `Override size: ...`
    for line in stdout.splitlines():
        s = line.strip()
        if s.startswith("Physical size:"):
            return s.split(":", 1)[1].strip()
    return ""


def _parse_wm_density(stdout: str) -> str:
    # `Physical density: 420`
    for line in stdout.splitlines():
        s = line.strip()
        if s.startswith("Physical density:"):
            return s.split(":", 1)[1].strip()
    return ""


# ─── pstore (previous boot's crash record) ─────────────────────────
#
# After a kernel panic / watchdog reset the last console lines and the panic
# itself survive in RAM and are exposed as files under /sys/fs/pstore
# (ramoops: console-ramoops-0, dmesg-ramoops-N, pmsg-ramoops-0, ...). Kernels
# that predate pstore expose the same thing as /proc/last_kmsg. Reading it is
# the first step of every "why did it reboot" question.
#
# Nothing here deletes: some setups clear pstore on read (`rm` after `cat`)
# so the next crash has room — alb must leave that decision to the operator,
# otherwise one `alb diag pstore` could erase the only copy of a crash.

PSTORE_DIR = "/sys/fs/pstore"
LAST_KMSG = "/proc/last_kmsg"

# First line that names the crash. The three kernel strings are literal
# (printk never changes their case); "watchdog" appears both as the kernel's
# `watchdog:` and as vendor "Watchdog bark/bite" lines, so that one is
# case-insensitive.
# Two tiers. A previous boot's console log routinely carries benign
# `watchdog:` init lines long before anything goes wrong, so a watchdog line
# is reported only when none of the unambiguous markers is present.
_PANIC_RE = re.compile(r"Kernel panic|Internal error: Oops|Unable to handle kernel")
_WATCHDOG_RE = re.compile(r"watchdog", re.IGNORECASE)

# pmsg is not kernel text: it is Android's logger writing the previous
# boot's logcat as binary log entries (header, priority, tag, message). The
# kernel's crash lines never go there, so scanning it for them only finds
# noise — on a real board, keystore's "Watchdog thread idle" log line,
# reported as the crash with the entry header's bytes in front of it.
_LOGCAT_RECORD_PREFIX = "pmsg-"
LOGCAT_RECORD_HINT = "binary logcat of the previous boot — read it on the device: logcat -L"


@dataclass(frozen=True)
class PstoreFile:
    name: str
    size: int
    local_path: str  # "" when the pull failed
    first_panic_line: str | None
    error: str = ""
    # "kernel": console / dmesg text, scanned for crash markers.
    # "logcat": a pmsg record, binary, not scanned (LOGCAT_RECORD_HINT).
    kind: str = "kernel"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "size": self.size,
            "local_path": self.local_path,
            "first_panic_line": self.first_panic_line,
            "error": self.error,
            "kind": self.kind,
        }


@dataclass(frozen=True)
class PstoreResult:
    """Where the previous boot's record came from and what it says.

    source: "pstore" | "last_kmsg" | "none". The two *_present flags say
    which locations existed, so "none" is a statement about the device, not
    about alb failing to look.
    """

    source: str
    pstore_present: bool
    last_kmsg_present: bool
    files: list[PstoreFile]
    saved_dir: str | None
    note: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "pstore_present": self.pstore_present,
            "last_kmsg_present": self.last_kmsg_present,
            "files": [f.to_dict() for f in self.files],
            "saved_dir": self.saved_dir,
            "note": self.note,
        }


async def pstore_pull(
    transport: Transport,
    *,
    device: str | None = None,
) -> Result[PstoreResult]:
    """Pull /sys/fs/pstore/* (else /proc/last_kmsg) into workspace/.../pstore/<ts>/.

    Needs root to list pstore on most builds; a denied listing comes back as
    ROOT_REQUIRED rather than "empty", because those two look the same to a
    reader and mean opposite things.
    """
    ls = await transport.shell(f"ls -l {PSTORE_DIR}", timeout=10)
    ls_text = (ls.stdout + ls.stderr).lower()

    if not ls.ok and "permission denied" in ls_text:
        return fail(
            code="ROOT_REQUIRED",
            message=f"listing {PSTORE_DIR} denied: {(ls.stderr or ls.stdout).strip()}",
            suggestion="needs root — run `alb adb root`, then retry",
            category="permission",
            timing_ms=ls.duration_ms,
        )
    if not ls.ok and "no such file" not in ls_text and "not a directory" not in ls_text:
        return fail(
            code=ls.error_code or "ADB_COMMAND_FAILED",
            message=(ls.stderr or ls.stdout).strip() or f"listing {PSTORE_DIR} failed",
            suggestion="Device may be offline",
            category="transport",
            timing_ms=ls.duration_ms,
        )

    pstore_present = ls.ok
    entries = _parse_ls_l(ls.stdout) if ls.ok else []

    if entries:
        dst_dir = workspace_path("pstore", iso_timestamp(), device=device)
        dst_dir.mkdir(parents=True, exist_ok=True)
        files = [
            await _fetch_record(transport, f"{PSTORE_DIR}/{name}", dst_dir / name, size)
            for name, size in entries
        ]
        if not any(f.local_path for f in files):
            return _nothing_pulled(files, f"{len(files)} record(s) listed in {PSTORE_DIR}")
        return ok(
            data=PstoreResult(
                source="pstore",
                pstore_present=True,
                last_kmsg_present=False,
                files=files,
                saved_dir=str(dst_dir),
                note=f"{len(files)} pstore file(s) in {PSTORE_DIR}",
            ),
            artifacts=[Path(f.local_path) for f in files if f.local_path],
        )

    # Older kernels (and some that reset pstore on boot) expose the previous
    # kernel log as a single proc file instead.
    pstore_state = "empty" if pstore_present else "absent"
    probe = await transport.shell(f"ls {LAST_KMSG}", timeout=10)
    if not probe.ok:
        return ok(
            data=PstoreResult(
                source="none",
                pstore_present=pstore_present,
                last_kmsg_present=False,
                files=[],
                saved_dir=None,
                note=(
                    f"{PSTORE_DIR} {pstore_state}; {LAST_KMSG} absent — the previous "
                    "boot left no crash record (clean reboot, or ramoops not configured)"
                ),
            ),
            artifacts=[],
        )

    dst_dir = workspace_path("pstore", iso_timestamp(), device=device)
    dst_dir.mkdir(parents=True, exist_ok=True)
    rec = await _fetch_record(transport, LAST_KMSG, dst_dir / "last_kmsg", -1)
    if not rec.local_path:
        return _nothing_pulled([rec], f"{PSTORE_DIR} {pstore_state}; {LAST_KMSG} present")
    return ok(
        data=PstoreResult(
            source="last_kmsg",
            pstore_present=pstore_present,
            last_kmsg_present=True,
            files=[rec],
            saved_dir=str(dst_dir),
            note=f"{PSTORE_DIR} {pstore_state}; {LAST_KMSG} present",
        ),
        artifacts=[Path(rec.local_path)] if rec.local_path else [],
    )


def _nothing_pulled(files: list[PstoreFile], where: str) -> Result[PstoreResult]:
    """Records exist on the device but not one reached the host.

    Reported as a failure, not as an ok with zero artifacts: a caller that
    reads only ``ok`` would otherwise take "could not read the crash record"
    for "there was no crash".
    """
    errors = "; ".join(f"{f.name}: {f.error}" for f in files)
    if any("permission denied" in f.error.lower() for f in files):
        return fail(
            code="ROOT_REQUIRED",
            message=f"{where}, but reading them was denied: {errors}",
            suggestion="needs root — run `alb adb root`, then retry",
            category="permission",
        )
    return fail(
        code="PSTORE_PULL_FAILED",
        message=f"{where}, but none could be pulled: {errors}",
        suggestion="Check `alb devices`; read each file's error above",
        category="transport",
    )


async def _fetch_record(transport: Transport, remote: str, local: Path, size: int) -> PstoreFile:
    """Pull one record and scan it for the first crash marker.

    size < 0 means "unknown until pulled" (proc files report 0 in `ls -l`).
    """
    r = await transport.pull(remote, local)
    if not r.ok:
        return PstoreFile(
            name=local.name,
            size=max(size, 0),
            local_path="",
            first_panic_line=None,
            error=(r.stderr or r.stdout).strip() or "pull failed",
        )
    return _scan_record(local, size)


def _scan_record(local: Path, size: int) -> PstoreFile:
    """Size + first crash marker of a pulled record.

    Sync on purpose: ramoops records are at most a few hundred KB, and the
    scan is the whole point of the pull — nothing else is waiting on the loop.
    """
    if not local.exists():
        return PstoreFile(
            name=local.name,
            size=max(size, 0),
            local_path="",
            first_panic_line=None,
            error="pull reported ok but wrote nothing",
        )
    real_size = local.stat().st_size if size < 0 else size
    if local.name.startswith(_LOGCAT_RECORD_PREFIX):
        return PstoreFile(
            name=local.name,
            size=real_size,
            local_path=str(local),
            first_panic_line=None,
            kind="logcat",
        )
    # ramoops buffers carry ECC / stale bytes; decode leniently, never raise.
    text = local.read_bytes().decode("utf-8", errors="replace")
    return PstoreFile(
        name=local.name,
        size=real_size,
        local_path=str(local),
        first_panic_line=_first_panic_line(text),
    )


def _first_panic_line(text: str) -> str | None:
    lines = text.splitlines()
    for pattern in (_PANIC_RE, _WATCHDOG_RE):
        for line in lines:
            if pattern.search(line):
                return _printable(line)[:200]
    return None


def _printable(line: str) -> str:
    """The line as a terminal can show it: control bytes and undecodable
    ones (U+FFFD from the lenient decode) dropped. A ramoops record can carry
    stale bytes on the same line as the marker; printed raw they garble the
    summary, or move the cursor."""
    return "".join(c for c in line if c.isprintable() and c != "�").strip()


def _parse_ls_l(stdout: str) -> list[tuple[str, int]]:
    """`ls -l` (toybox / busybox) → [(basename, size)], sorted by name.

    -r--r--r-- 1 root root 131072 2026-01-01 00:00 console-ramoops-0
    Names come from the device, so they are basenamed (round11 SEC-3) before
    they ever become a local path.
    """
    out: list[tuple[str, int]] = []
    for raw in stdout.splitlines():
        parts = raw.split()
        if len(parts) < 6 or raw.startswith("total"):
            continue
        safe = Path(parts[-1]).name
        if not safe or safe in {".", ".."}:
            continue
        try:
            size = int(parts[4])
        except ValueError:
            size = 0
        out.append((safe, size))
    return sorted(out)
