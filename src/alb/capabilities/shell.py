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
        message, suggestion = _failure_text(r.exit_code, r.stdout, r.stderr, r.error_code)
        return fail(
            code=r.error_code or "SHELL_NONZERO_EXIT",
            message=message,
            suggestion=suggestion,
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


def _failure_text(
    exit_code: int, stdout: str, stderr: str, error_code: str | None
) -> tuple[str, str]:
    """Message + suggestion for a failed command.

    stderr, when there is any, is the most useful message. Without it the old
    text was a bare "Command failed", and a non-zero exit with no output at
    all was easy to read as "ran fine, printed nothing" — a board-side
    ``su 0 rm -f a b`` came back 255 with both streams empty and two cleanups
    were taken as done. So name the exit code, and say when nothing came out.
    """
    if stderr.strip():
        return stderr.strip(), _suggest_for(error_code)
    if stdout.strip():
        return f"command failed with exit code {exit_code}", _suggest_for(error_code)
    suggestion = (
        "Do not read the empty output as success: the command may not have run at all. "
        "Append '; echo rc=$?' — if even that prints nothing, the command never started"
    )
    if error_code == "ADB_COMMAND_FAILED":
        # Seen on a real board: its adbd refused the shell request by the
        # command's text and closed the stream — adb then exits 255 with
        # nothing printed, even for `echo <that text>`. The same command ran
        # over the serial console, and base64-wrapped over adb.
        suggestion += (
            ". Over adb this is also how a command looks when the device's adbd refuses it: "
            "some vendor adbd builds reject shell commands by their text (the device log "
            "says so). Try the serial console, run the steps one by one, or put them in a "
            "script and run that"
        )
    return (
        f"command failed with exit code {exit_code} and no output (stdout and stderr both empty)",
        suggestion,
    )


def _suggest_for(code: str | None) -> str:
    mapping = {
        "TIMEOUT_SHELL": "Increase timeout or use stream_read for long-running commands",
        "DEVICE_NOT_FOUND": "Run: alb devices",
        "DEVICE_OFFLINE": "Reconnect device / run: alb status",
        "DEVICE_UNAUTHORIZED": "Accept 'Allow USB debugging' prompt on device",
        "ADB_SERVER_UNREACHABLE": "Check Xshell tunnel: ss -tlnp | grep 5037",
        "ADB_BINARY_NOT_FOUND": "Put adb on PATH, or set ALB_ADB_PATH to the adb executable",
    }
    return mapping.get(code or "", "See docs/errors.md for details")
