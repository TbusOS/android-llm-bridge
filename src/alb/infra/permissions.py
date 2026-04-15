"""Permission engine (placeholder).

Full design in docs/permissions.md. M1 will implement:
- DANGEROUS_PATTERNS blocklist
- Multi-layer policy (defaults < config < profile < CLI flags < session)
- transport.check_permissions hook
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Behavior = Literal["allow", "ask", "deny"]


@dataclass(frozen=True)
class PermissionResult:
    behavior: Behavior
    reason: str | None = None
    matched_rule: str | None = None
    suggestion: str | None = None


# ─── Default blocklist (subset for M0 skeleton; full list in M1) ─────
DANGEROUS_PATTERNS: list[tuple[str, str]] = [
    (r"^\s*rm\s+-rf?\s+/($|\s|\*)", "rm root filesystem"),
    (r"^\s*rm\s+-rf?\s+/sdcard($|\s|/?\*)", "rm entire sdcard"),
    (r"^\s*rm\s+-rf?\s+/data($|\s|/?\*)", "rm /data"),
    (r"^\s*rm\s+-rf?\s+/system", "rm /system"),
    (r">\s*/dev/block/", "write to raw block device"),
    (r"^\s*dd\s+.*of=/dev/block", "dd to block device"),
    (r"^\s*mkfs\.", "format partition"),
    (r"^\s*reboot\s+(bootloader|fastboot)", "reboot to bootloader/fastboot"),
    (r"^\s*fastboot\s+(erase|flash|format|oem)", "fastboot destructive"),
    (r"^\s*setprop\s+persist\.", "modify persistent property"),
    (r"^\s*setprop\s+ro\.", "modify read-only property"),
    (
        r"^\s*(killall|pkill)\s+(system_server|zygote|init|surfaceflinger)",
        "kill critical system process",
    ),
    (r"^\s*setenforce\s+0", "disable SELinux"),
]

_COMPILED: list[tuple[re.Pattern[str], str]] = [
    (re.compile(pat), reason) for pat, reason in DANGEROUS_PATTERNS
]


async def default_check(
    transport_name: str,
    action: str,
    input_data: dict,
) -> PermissionResult:
    """Run the default permission check against a command.

    M1 will extend this with multi-layer config lookup (defaults < config < profile ...).
    For now just applies the blocklist.
    """
    cmd = input_data.get("cmd", "")
    if not cmd:
        return PermissionResult(behavior="allow")

    for pattern, reason in _COMPILED:
        if pattern.search(cmd):
            return PermissionResult(
                behavior="deny",
                reason=f"Matches dangerous pattern: {reason}",
                matched_rule=pattern.pattern,
                suggestion="Scope to a specific path, or configure an allow rule in your profile",
            )

    return PermissionResult(behavior="allow")
