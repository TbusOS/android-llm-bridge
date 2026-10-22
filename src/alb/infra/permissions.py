"""Permission hook shared by every transport.

Policy (2026-10-23, see docs/permissions.md): commands that run ON THE
BOARD are never restricted — reboot to bootloader, fastboot flash, setprop,
setenforce, rm, dd all go through. A board is a debugging target; blocking
these only made debugging impossible.

What must stay protected is the Windows host the board is plugged into.
That host never runs free-form commands from alb: its agent executes only
the ops listed in its own op table (clients/windows-agent/alb_agent.py,
`_build_ops`), so there is no path for a board command to delete a host
file. This module therefore has no rules today; `check_permissions` stays
as the hook a future host-side rule would plug into.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Behavior = Literal["allow", "ask", "deny"]


@dataclass(frozen=True)
class PermissionResult:
    behavior: Behavior
    reason: str | None = None
    matched_rule: str | None = None
    suggestion: str | None = None


async def default_check(
    transport_name: str,
    action: str,
    input_data: dict,
) -> PermissionResult:
    """Default permission check: board-side actions are always allowed."""
    return PermissionResult(behavior="allow")
