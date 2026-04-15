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
    extras: dict[str, str]

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
        extras={},
    )
    return ok(data=info)


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
