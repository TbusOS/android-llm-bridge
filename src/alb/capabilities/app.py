"""app capability — APK install / uninstall / start / stop / list / info.

See docs/capabilities/app.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from alb.infra.result import Result, fail, ok
from alb.transport.base import Transport


# ─── Models ────────────────────────────────────────────────────────
@dataclass(frozen=True)
class AppInfo:
    package: str
    version_name: str = ""
    version_code: str = ""
    first_install_time: str = ""
    last_update_time: str = ""
    requested_permissions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "package": self.package,
            "version_name": self.version_name,
            "version_code": self.version_code,
            "first_install_time": self.first_install_time,
            "last_update_time": self.last_update_time,
            "requested_permissions": list(self.requested_permissions),
        }


@dataclass(frozen=True)
class AppListResult:
    packages: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {"packages": self.packages, "count": len(self.packages)}


_PACKAGE_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z][a-zA-Z0-9_]*)+$")


# ─── install / uninstall ───────────────────────────────────────────
async def install(
    transport: Transport,
    apk: Path,
    *,
    replace: bool = True,
    grant_runtime: bool = False,
    downgrade: bool = False,
) -> Result[dict[str, Any]]:
    """Install an APK. For ssh transport, uses push + pm install."""
    if not apk.exists():
        return fail(
            code="FILE_NOT_FOUND",
            message=f"APK not found: {apk}",
            suggestion="Check the path",
            category="io",
        )

    if transport.name == "adb":
        flags = []
        if replace:
            flags.append("-r")
        if grant_runtime:
            flags.append("-g")
        if downgrade:
            flags.append("-d")
        # adb install is a special command, not `adb shell`
        result = await transport.push(apk, f"/data/local/tmp/{apk.name}")
        if not result.ok:
            return fail(
                code=result.error_code or "ADB_COMMAND_FAILED",
                message=result.stderr.strip() or "push failed",
                suggestion="Check device storage",
                category="transport",
            )
        shell_cmd = f"pm install {' '.join(flags)} /data/local/tmp/{apk.name}"
    elif transport.name == "ssh":
        remote = f"/data/local/tmp/{apk.name}"
        push_r = await transport.push(apk, remote)
        if not push_r.ok:
            return fail(
                code=push_r.error_code or "SSH_COMMAND_FAILED",
                message=push_r.stderr or "push failed",
                suggestion="Check device reachability",
                category="transport",
            )
        flags_list = []
        if replace:
            flags_list.append("-r")
        if grant_runtime:
            flags_list.append("-g")
        if downgrade:
            flags_list.append("-d")
        shell_cmd = f"pm install {' '.join(flags_list)} {remote}"
    else:
        return fail(
            code="TRANSPORT_NOT_SUPPORTED",
            message="app install requires adb or ssh transport",
            suggestion="Run: alb setup adb (or ssh)",
            category="transport",
        )

    r = await transport.shell(shell_cmd, timeout=180)
    await transport.shell(f"rm -f /data/local/tmp/{apk.name}", timeout=10)

    if not r.ok:
        return fail(
            code=_classify_install_error(r.stdout + r.stderr),
            message=(r.stdout or r.stderr).strip() or "pm install failed",
            suggestion="Inspect stdout/stderr for INSTALL_FAILED_* code",
            category="capability",
            details={"stdout": r.stdout, "stderr": r.stderr},
            timing_ms=r.duration_ms,
        )

    if "Success" not in r.stdout:
        return fail(
            code=_classify_install_error(r.stdout),
            message=r.stdout.strip() or "install did not report Success",
            suggestion="See details for INSTALL_FAILED_* reason",
            category="capability",
            details={"stdout": r.stdout},
        )

    return ok(data={"installed": str(apk), "output": r.stdout.strip()},
              timing_ms=r.duration_ms)


async def uninstall(
    transport: Transport,
    package: str,
    *,
    keep_data: bool = False,
    allow_dangerous: bool = False,
) -> Result[dict[str, Any]]:
    if not _PACKAGE_NAME_RE.match(package):
        return fail(
            code="PACKAGE_NAME_INVALID",
            message=f"Invalid package name: {package}",
            suggestion="Use a name like com.example.app",
            category="input",
        )

    perm = await transport.check_permissions(
        "app.uninstall", {"package": package, "keep_data": keep_data}
    )
    if perm.behavior == "deny":
        return fail(
            code="PERMISSION_DENIED",
            message=perm.reason or "uninstall blocked",
            suggestion=perm.suggestion or "",
            category="permission",
        )
    if perm.behavior == "ask" and not allow_dangerous:
        return fail(
            code="PERMISSION_DENIED",
            message=perm.reason or "uninstall needs confirmation",
            suggestion=perm.suggestion or "Re-run with --allow-dangerous",
            category="permission",
            details={"behavior": "ask"},
        )

    cmd = f"pm uninstall {'-k ' if keep_data else ''}{package}"
    r = await transport.shell(cmd, timeout=60)
    if not r.ok or "Success" not in r.stdout:
        if "not installed" in (r.stdout + r.stderr).lower():
            return fail(
                code="APP_NOT_INSTALLED",
                message=f"{package} is not installed",
                suggestion="Run: alb app list",
                category="capability",
            )
        return fail(
            code=r.error_code or "ADB_COMMAND_FAILED",
            message=(r.stdout or r.stderr).strip(),
            suggestion="Some system apps are not uninstallable",
            category="capability",
            details={"stdout": r.stdout, "stderr": r.stderr},
        )
    return ok(data={"uninstalled": package}, timing_ms=r.duration_ms)


# ─── start / stop ──────────────────────────────────────────────────
async def start(transport: Transport, component: str) -> Result[dict[str, Any]]:
    if "/" in component:
        cmd = f"am start -n {component}"
    else:
        if not _PACKAGE_NAME_RE.match(component):
            return fail(
                code="PACKAGE_NAME_INVALID",
                message=f"Invalid package name: {component}",
                suggestion="Use package name (com.x.y) or package/.Activity",
                category="input",
            )
        cmd = f"monkey -p {component} -c android.intent.category.LAUNCHER 1"
    r = await transport.shell(cmd, timeout=30)
    if not r.ok:
        return fail(
            code=r.error_code or "ADB_COMMAND_FAILED",
            message=r.stderr.strip() or "am/monkey failed",
            suggestion="Verify package is installed and activity exists",
            category="capability",
            details={"stderr": r.stderr},
        )
    return ok(data={"started": component}, timing_ms=r.duration_ms)


async def stop(transport: Transport, package: str) -> Result[dict[str, Any]]:
    if not _PACKAGE_NAME_RE.match(package):
        return fail(
            code="PACKAGE_NAME_INVALID",
            message=f"Invalid package name: {package}",
            suggestion="Use a name like com.example.app",
            category="input",
        )
    r = await transport.shell(f"am force-stop {package}", timeout=10)
    if not r.ok:
        return fail(
            code=r.error_code or "ADB_COMMAND_FAILED",
            message=r.stderr.strip() or "force-stop failed",
            suggestion="",
            category="capability",
        )
    return ok(data={"stopped": package}, timing_ms=r.duration_ms)


# ─── list / info ───────────────────────────────────────────────────
async def list_apps(
    transport: Transport,
    *,
    filter: str | None = None,  # noqa: A002
    include_system: bool = False,
) -> Result[AppListResult]:
    flags = [] if include_system else ["-3"]
    r = await transport.shell(f"pm list packages {' '.join(flags)}".strip(), timeout=30)
    if not r.ok:
        return fail(
            code=r.error_code or "ADB_COMMAND_FAILED",
            message="pm list packages failed",
            suggestion="Device may be offline",
            category="transport",
        )
    pkgs = [
        line[len("package:"):].strip()
        for line in r.stdout.splitlines()
        if line.startswith("package:")
    ]
    if filter:
        needle = filter.lower()
        pkgs = [p for p in pkgs if needle in p.lower()]
    return ok(data=AppListResult(packages=sorted(pkgs)), timing_ms=r.duration_ms)


async def info(transport: Transport, package: str) -> Result[AppInfo]:
    if not _PACKAGE_NAME_RE.match(package):
        return fail(
            code="PACKAGE_NAME_INVALID",
            message=f"Invalid package name: {package}",
            suggestion="Use a name like com.example.app",
            category="input",
        )
    r = await transport.shell(f"dumpsys package {package}", timeout=30)
    if not r.ok:
        return fail(
            code=r.error_code or "ADB_COMMAND_FAILED",
            message="dumpsys package failed",
            suggestion="Verify package is installed",
            category="transport",
        )

    out = r.stdout
    if "Unable to find package" in out or f"Package [{package}]" not in out:
        return fail(
            code="APP_NOT_INSTALLED",
            message=f"{package} not installed",
            suggestion="Run: alb app list",
            category="capability",
        )

    return ok(
        data=AppInfo(
            package=package,
            version_name=_grep_first(out, r"versionName=([^\s]+)") or "",
            version_code=_grep_first(out, r"versionCode=(\d+)") or "",
            first_install_time=_grep_first(out, r"firstInstallTime=([^\s]+)") or "",
            last_update_time=_grep_first(out, r"lastUpdateTime=([^\s]+)") or "",
            requested_permissions=_grep_permissions(out),
        ),
        timing_ms=r.duration_ms,
    )


async def clear_data(
    transport: Transport,
    package: str,
    *,
    allow_dangerous: bool = False,
) -> Result[dict[str, Any]]:
    perm = await transport.check_permissions("app.clear_data", {"package": package})
    if perm.behavior == "deny":
        return fail(
            code="PERMISSION_DENIED",
            message=perm.reason or "clear_data blocked",
            suggestion=perm.suggestion or "",
            category="permission",
        )
    if perm.behavior == "ask" and not allow_dangerous:
        return fail(
            code="PERMISSION_DENIED",
            message="clear_data is destructive",
            suggestion="Re-run with --allow-dangerous",
            category="permission",
            details={"behavior": "ask"},
        )

    r = await transport.shell(f"pm clear {package}", timeout=30)
    if not r.ok or "Success" not in r.stdout:
        return fail(
            code=r.error_code or "ADB_COMMAND_FAILED",
            message=(r.stdout or r.stderr).strip() or "pm clear failed",
            suggestion="",
            category="capability",
        )
    return ok(data={"cleared": package}, timing_ms=r.duration_ms)


# ─── Helpers ───────────────────────────────────────────────────────
def _grep_first(text: str, pattern: str) -> str | None:
    m = re.search(pattern, text)
    return m.group(1) if m else None


def _grep_permissions(text: str) -> list[str]:
    out: list[str] = []
    in_section = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("requested permissions:"):
            in_section = True
            continue
        if in_section:
            if not line or not line.startswith("android.permission"):
                if line and not line[0].isspace() and ":" in line:
                    break
                continue
            out.append(line.split(":")[0].strip())
    return out


def _classify_install_error(text: str) -> str:
    m = re.search(r"INSTALL_FAILED_[A-Z_]+", text)
    return m.group(0) if m else "APP_INSTALL_FAILED"
