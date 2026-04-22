"""shell capability — execute a command via the active transport.

See docs/capabilities/shell.md for the full spec.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from alb.infra.result import Result, fail, ok
from alb.transport.base import Transport


@dataclass(frozen=True)
class ShellOutput:
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
        }


async def execute(
    transport: Transport,
    cmd: str,
    *,
    timeout: int = 30,
    allow_dangerous: bool = False,
) -> Result[ShellOutput]:
    """Run a shell command. Returns a structured Result.

    Args:
        transport: active Transport (AdbTransport / SshTransport / SerialTransport)
        cmd: the command string to run inside the device
        timeout: seconds before the command is killed
        allow_dangerous: bypass ASK-level permission checks. DENY is never bypassed.

    LLM notes:
        - stdout is included in data; for large output prefer a capability
          that routes to workspace (alb.capabilities.logging, alb_bugreport, ...).
        - The permission system may reject the command — see error.suggestion.
    """
    perm = await transport.check_permissions(
        "shell.execute",
        {"cmd": cmd, "allow_dangerous": allow_dangerous},
    )
    if perm.behavior == "deny":
        return fail(
            code="PERMISSION_DENIED",
            message=perm.reason or "Command blocked by permission policy",
            suggestion=perm.suggestion or "",
            category="permission",
            details={
                "matched_rule": perm.matched_rule,
                "attempted_command": cmd,
            },
        )

    if perm.behavior == "ask" and not allow_dangerous:
        return fail(
            code="PERMISSION_DENIED",
            message=perm.reason or "Command needs explicit confirmation",
            suggestion=perm.suggestion or "Re-run with allow_dangerous=True after confirming",
            category="permission",
            details={
                "behavior": "ask",
                "matched_rule": perm.matched_rule,
                "attempted_command": cmd,
            },
        )

    r = await transport.shell(cmd, timeout=timeout)

    if not r.ok:
        return fail(
            code=r.error_code or "SHELL_NONZERO_EXIT",
            message=r.stderr.strip() or "Command failed",
            suggestion=_suggest_for(r.error_code),
            category="transport",
            details={
                "stdout": r.stdout,
                "stderr": r.stderr,
                "exit_code": r.exit_code,
            },
            timing_ms=r.duration_ms,
        )

    return ok(
        data=ShellOutput(
            stdout=r.stdout,
            stderr=r.stderr,
            exit_code=r.exit_code,
            duration_ms=r.duration_ms,
        ),
        timing_ms=r.duration_ms,
    )


def _suggest_for(code: str | None) -> str:
    mapping = {
        "TIMEOUT_SHELL": "Increase timeout or use stream_read for long-running commands",
        "DEVICE_NOT_FOUND": "Run: alb devices",
        "DEVICE_OFFLINE": "Reconnect device / run: alb status",
        "DEVICE_UNAUTHORIZED": "Accept 'Allow USB debugging' prompt on device",
        "ADB_SERVER_UNREACHABLE": "Check Xshell tunnel: ss -tlnp | grep 5037",
        "ADB_BINARY_NOT_FOUND": "Install platform-tools or set ALB_ADB_PATH",
    }
    return mapping.get(code or "", "See docs/errors.md for details")
