"""Error code catalog. See docs/errors.md for full documentation.

PLACEHOLDER — codes will be added as capabilities are implemented in M1.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorSpec:
    code: str
    category: str
    default_message: str
    default_suggestion: str


# Minimal registry for M0/M1 skeleton.
# Expand as capabilities are added.
ERROR_CODES: dict[str, ErrorSpec] = {
    # transport
    "TRANSPORT_NOT_CONFIGURED": ErrorSpec(
        "TRANSPORT_NOT_CONFIGURED",
        "transport",
        "No active transport configured",
        "Run: alb setup adb  (or: alb setup ssh / serial)",
    ),
    "TRANSPORT_NOT_SUPPORTED": ErrorSpec(
        "TRANSPORT_NOT_SUPPORTED",
        "transport",
        "Operation not supported by this transport",
        "Switch transport: alb setup <other-method>",
    ),
    "ADB_BINARY_NOT_FOUND": ErrorSpec(
        "ADB_BINARY_NOT_FOUND",
        "transport",
        "adb binary not found in PATH",
        "Install platform-tools or set ALB_ADB_PATH",
    ),
    "ADB_SERVER_UNREACHABLE": ErrorSpec(
        "ADB_SERVER_UNREACHABLE",
        "transport",
        "Cannot reach adb server",
        "Check Xshell tunnel; run: ss -tlnp | grep 5037",
    ),
    "ADB_COMMAND_FAILED": ErrorSpec(
        "ADB_COMMAND_FAILED",
        "transport",
        "adb command returned a non-zero exit code",
        "Read error.details.stderr for the underlying adb message",
    ),
    "SSH_AUTH_FAILED": ErrorSpec(
        "SSH_AUTH_FAILED",
        "transport",
        "SSH authentication failed",
        "Check key or run: ssh-copy-id <host>",
    ),
    "SERIAL_PORT_NOT_FOUND": ErrorSpec(
        "SERIAL_PORT_NOT_FOUND",
        "transport",
        "Serial port not found",
        "Check ser2net config and Xshell tunnel",
    ),

    # device
    "DEVICE_NOT_FOUND": ErrorSpec(
        "DEVICE_NOT_FOUND",
        "device",
        "Specified device serial not found",
        "Run: alb devices",
    ),
    "DEVICE_UNAUTHORIZED": ErrorSpec(
        "DEVICE_UNAUTHORIZED",
        "device",
        "Device rejected USB debugging",
        "Accept 'Allow USB debugging' on device screen",
    ),
    "DEVICE_OFFLINE": ErrorSpec(
        "DEVICE_OFFLINE",
        "device",
        "Device is offline",
        "Reconnect USB or run: alb devices",
    ),

    # permission
    "PERMISSION_DENIED": ErrorSpec(
        "PERMISSION_DENIED",
        "permission",
        "Command blocked by permission policy",
        "Read error.details.matched_rule; scope narrower or add allow rule",
    ),

    # timeout
    "TIMEOUT_SHELL": ErrorSpec(
        "TIMEOUT_SHELL",
        "timeout",
        "Shell command timed out",
        "Increase timeout param or use stream_read",
    ),
    "TIMEOUT_BOOT": ErrorSpec(
        "TIMEOUT_BOOT",
        "timeout",
        "Device did not finish booting in time",
        "Device may have kernel panic; check UART (method G)",
    ),

    # io
    "FILE_NOT_FOUND": ErrorSpec(
        "FILE_NOT_FOUND",
        "io",
        "Local file not found",
        "Check the path",
    ),
    "WORKSPACE_FULL": ErrorSpec(
        "WORKSPACE_FULL",
        "io",
        "Workspace disk is full",
        "Run: alb workspace clean --older-than 7d",
    ),

    # input
    "INVALID_DURATION": ErrorSpec(
        "INVALID_DURATION",
        "input",
        "duration out of range",
        "Use a value between 1 and 3600 seconds",
    ),
    "INVALID_FILTER": ErrorSpec(
        "INVALID_FILTER",
        "input",
        "filter expression is invalid",
        "See docs/capabilities/logging.md for logcat filter syntax",
    ),
    "INVALID_DEVICE_SERIAL": ErrorSpec(
        "INVALID_DEVICE_SERIAL",
        "input",
        "device serial is malformed",
        "Use a serial returned by alb devices",
    ),
    "FILE_NOT_READABLE": ErrorSpec(
        "FILE_NOT_READABLE",
        "io",
        "local file cannot be read",
        "Check file permissions",
    ),
    "REMOTE_PATH_INVALID": ErrorSpec(
        "REMOTE_PATH_INVALID",
        "io",
        "remote/local path is not allowed",
        "Path must stay inside workspace; avoid traversal",
    ),
    # system
    "SYSTEM_DEPENDENCY_MISSING": ErrorSpec(
        "SYSTEM_DEPENDENCY_MISSING",
        "system",
        "Missing system dependency",
        "Install the missing tool per error.details",
    ),
    # serial state (raised by SerialTransport when shell() is called in
    # a state that can't accept a command). See transport/serial_state.py
    # for the full state taxonomy.
    "BOARD_PANICKED": ErrorSpec(
        "BOARD_PANICKED",
        "transport",
        "Board is in kernel panic; no further commands can run",
        "Capture the tail (alb serial capture --duration 10) and reboot",
    ),
    "BOARD_BOOTING": ErrorSpec(
        "BOARD_BOOTING",
        "transport",
        "Board is still in a boot phase (SPL / kernel / init); shell not yet available",
        "Wait a few seconds and retry, or use alb stream_read to watch progress",
    ),
    "BOARD_UNREACHABLE": ErrorSpec(
        "BOARD_UNREACHABLE",
        "transport",
        "No output observed on UART after handshake",
        "Check power, UART wiring, baud rate, and the bridge / ser2net status",
    ),
    "SERIAL_BAUD_MISMATCH": ErrorSpec(
        "SERIAL_BAUD_MISMATCH",
        "transport",
        "UART stream is mostly non-printable — likely baud-rate mismatch",
        "Try common baud rates (115200 / 921600 / 1500000) or run `alb serial probe`",
    ),
    "BOARD_NEEDS_LOGIN": ErrorSpec(
        "BOARD_NEEDS_LOGIN",
        "transport",
        "Login prompt is waiting for a username",
        "Send a username via `alb serial send`, then retry the command",
    ),
    "BOARD_IN_FASTBOOT": ErrorSpec(
        "BOARD_IN_FASTBOOT",
        "transport",
        "Board is in fastboot mode — shell commands are not applicable",
        "Use adb / fastboot tools to leave fastboot, then retry",
    ),
    # Generic shell error — any transport (adb / ssh / serial) can raise
    # this when the command ran to completion but exited non-zero. The
    # specific exit_code is on ShellResult.exit_code.
    "SHELL_NONZERO_EXIT": ErrorSpec(
        "SHELL_NONZERO_EXIT",
        "transport",
        "Shell command exited with a non-zero status",
        "Inspect exit_code + stderr; fix the command or handle the failure",
    ),
}


def lookup(code: str) -> ErrorSpec | None:
    return ERROR_CODES.get(code)
