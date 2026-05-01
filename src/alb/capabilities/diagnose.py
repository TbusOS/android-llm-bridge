"""diagnose capability — bugreport / ANR / tombstone / devinfo.

See docs/capabilities/diagnose.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alb.infra.result import Result, fail, ok
from alb.infra.workspace import iso_timestamp, workspace_path
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
) -> Result[BugreportResult]:
    """Trigger `bugreportz` and pull the zip into workspace.

    LLM: takes 60-180s. Returns only the zip path; use alb_log_search on
    the extracted main.txt for analysis.
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

    local_dir = workspace_path("bugreports", iso_timestamp(), device=device).parent
    local_zip = local_dir / f"{iso_timestamp()}-bugreport.zip"
    local_zip.parent.mkdir(parents=True, exist_ok=True)

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
        local = dst_dir / n
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
    cpu_cores = _count_cpu_cores(cpu_r.stdout if cpu_r.ok else "")
    cpu_max_khz = await _read_cpu_max_khz(transport)

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
        "cpu_cores": cpu_cores,
        "cpu_max_khz": cpu_max_khz,
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
async def _read_cpu_max_khz(transport: Transport) -> int:
    """Best-effort: read max CPU freq from cpufreq sysfs. 0 if unavailable."""
    r = await transport.shell(
        "cat /sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq 2>/dev/null",
        timeout=5,
    )
    if not r.ok or not r.stdout.strip():
        return 0
    try:
        return int(r.stdout.strip())
    except ValueError:
        return 0


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
