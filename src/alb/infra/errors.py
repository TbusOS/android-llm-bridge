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
        "Check ser2net config and the reverse tunnel",
    ),
    "SERIAL_PORT_BUSY": ErrorSpec(
        "SERIAL_PORT_BUSY",
        "transport",
        "Serial port is held exclusively by another program",
        "Close the terminal emulator / flashing tool holding the port, then retry "
        "(concurrent alb readers share one link and do NOT conflict)",
    ),
    "SERIAL_SEND_UNCONFIRMED": ErrorSpec(
        "SERIAL_SEND_UNCONFIRMED",
        "transport",
        "Serial write was accepted locally but the link died before the bytes could reach the UART",
        "Check the bridge / agent is up (alb serial health), then resend",
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
    "INVALID_SOURCE": ErrorSpec(
        "INVALID_SOURCE",
        "input",
        "unknown or empty log source list",
        "Choose at least one of: logcat, dmesg, uart",
    ),
    "TIMELINE_NO_SOURCE": ErrorSpec(
        "TIMELINE_NO_SOURCE",
        "transport",
        "None of the requested timeline sources could be read; nothing was captured",
        "Read the notes: dmesg usually needs `alb adb root`, uart needs `alb setup serial`, "
        "logcat needs an adb device",
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
    "SERIAL_LINK_REFUSED": ErrorSpec(
        "SERIAL_LINK_REFUSED",
        "transport",
        "The serial endpoint accepted the connection then closed it without a byte — "
        "the bridge never reached the UART",
        "Not a board fault. Check the bridge: the alb-api log carries the agent's own "
        "reason (usually another program holds the port), or `alb serial health`",
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
    # ─── fastboot / flash (ADR-056) ──────────────────────────────────
    # Five distinct codes rather than one FLASH_FAILED, because the fixes
    # have nothing in common: install a tool, wait for someone else, put the
    # board in the right mode, re-copy the image, read the tool's output.
    # Collapsing them would put the caller back to reading prose.
    "FASTBOOT_UNAVAILABLE": ErrorSpec(
        "FASTBOOT_UNAVAILABLE",
        "transport",
        "The connected agent has no fastboot executable",
        "Install platform-tools on the agent host, or set fastboot_path in "
        "agent.conf, then restart the agent",
    ),
    "FASTBOOT_BUSY": ErrorSpec(
        "FASTBOOT_BUSY",
        "transport",
        "Another flash job is already running on this agent",
        "Wait for it to finish — the device can only serve one job at a time; "
        "`/agent/status` shows the job in flight",
    ),
    "FASTBOOT_NO_DEVICE": ErrorSpec(
        "FASTBOOT_NO_DEVICE",
        "transport",
        "No device is visible to fastboot on the agent host",
        # bootloader, not fastboot: `reboot fastboot` lands in fastbootd, the
        # userspace one, which only writes dynamic partitions and refuses the
        # rest with a message that sends people off to unlock the board.
        "Put the board in bootloader fastboot (`alb power reboot bootloader "
        "--allow-dangerous --no-wait-boot`); if it is there and still not "
        "listed, run `alb bench usb` to tell a missing driver from a cable "
        "that is not on the agent host",
    ),
    "FLASH_IMAGE_CORRUPT": ErrorSpec(
        "FLASH_IMAGE_CORRUPT",
        "transport",
        "The image did not survive the transfer intact (digest mismatch)",
        "Retry — nothing was written to the device, the digest is checked "
        "before fastboot is invoked",
    ),
    "FLASH_PARTITION_REJECTED": ErrorSpec(
        "FLASH_PARTITION_REJECTED",
        "transport",
        "The agent refused the requested partition name",
        "Use a partition on the agent's allowlist, or widen it in agent.conf "
        "if you are certain",
    ),
    "FLASH_OEM_REJECTED": ErrorSpec(
        "FLASH_OEM_REJECTED",
        "transport",
        "The agent refused the oem command — it is not on the agent's allowlist",
        "Only commands checked to be reversible are allowed (see _OEM_COMMANDS in "
        "alb_agent.py); some vendor oem commands are one-way, so widen that list "
        "only after confirming the command can be undone",
    ),
    "FLASH_VAR_REJECTED": ErrorSpec(
        "FLASH_VAR_REJECTED",
        "transport",
        "The agent refused the getvar variable name (it is not a well-formed name)",
        "Use a plain variable name such as `partition-size:boot_a`, or none "
        "for `getvar all`",
    ),
    "FLASH_FAILED": ErrorSpec(
        "FLASH_FAILED",
        "transport",
        "fastboot ran but reported a failure",
        "Read the captured fastboot output; if the board is now half-written, "
        "re-flash before rebooting it",
    ),
    "FLASH_AGENT_NO_SPACE": ErrorSpec(
        "FLASH_AGENT_NO_SPACE",
        "transport",
        "The agent host has too little free temp space to receive the image",
        "Free disk space on the agent host (the image is staged in its temp "
        "directory before fastboot runs) — nothing was transferred or written",
    ),
    # A flash plan (alb flash plan) is checked as a whole before any row runs.
    # Two codes because the remedies differ: fix the manifest / skips / agent,
    # versus simply look at the plan again.
    "FLASH_PLAN_REJECTED": ErrorSpec(
        "FLASH_PLAN_REJECTED",
        "transport",
        "The flash plan has problems; no row was executed",
        "Run `alb flash plan <manifest> --dry-run` to see every problem with "
        "its line number",
    ),
    "FLASH_PLAN_CHANGED": ErrorSpec(
        "FLASH_PLAN_CHANGED",
        "transport",
        "The plan differs from the one that was confirmed (an image, the "
        "manifest or the skips changed); no row was executed",
        "Run the command again and confirm the plan as it is now",
    ),
    # ─── serial break-in (alb serial break-in) ───────────────────────
    # Stopping a bootloader at its countdown over UART. Only "stopped at a
    # prompt" is success; the two timeouts differ in what to try next.
    "BREAKIN_PATTERN_NOT_SEEN": ErrorSpec(
        "BREAKIN_PATTERN_NOT_SEEN",
        "timeout",
        "The bootloader countdown text never appeared on the UART within the timeout",
        "Read the tail: if the board went straight into the kernel, this bootloader does not "
        "offer a key-press stop or prints other text (pass --expect); if nothing arrived, "
        "check that the board actually reset and the UART / baud (alb serial status)",
    ),
    "BREAKIN_NO_PROMPT": ErrorSpec(
        "BREAKIN_NO_PROMPT",
        "timeout",
        "The countdown appeared and keys were sent, but no console prompt followed",
        "Run `alb serial status` to see where the board is; at an unrecognised prompt pass "
        "--prompt; if it kept booting try --send '\\r' or '\\x03' and a smaller --repeat-ms",
    ),
    "SERIAL_LINK_LOST": ErrorSpec(
        "SERIAL_LINK_LOST",
        "transport",
        "The serial link dropped during the attempt and could not be reopened",
        "Check the bridge / agent (alb serial health); nothing about the board is implied",
    ),
    "BREAKIN_BAD_ARGUMENT": ErrorSpec(
        "BREAKIN_BAD_ARGUMENT",
        "input",
        "A break-in option is not usable (unknown reboot mode, nothing to send)",
        "See `alb serial break-in --help`",
    ),
    # ─── adbd (alb adb root / unroot / remount) · pstore ─────────────
    # The adbd replies matched here are AOSP's own (adb daemon
    # restart_service.cpp); the verdict is still the uid read back after the
    # restart, never the wording.
    "ADB_ROOT_REFUSED": ErrorSpec(
        "ADB_ROOT_REFUSED",
        "device",
        "adbd refused to run as root (production build), or kept its uid after `adb root`",
        "For single commands prefix `su 0 <cmd>` (toybox su takes the uid first, not "
        "`su -c`); for a root adbd flash a userdebug / eng build",
    ),
    "ADBD_RESTART_TIMEOUT": ErrorSpec(
        "ADBD_RESTART_TIMEOUT",
        "timeout",
        "adbd did not come back with the requested uid within the wait",
        "Run `alb devices`; over a tunnel the reconnect can take longer — retry with a "
        "larger --timeout",
    ),
    "ADB_REMOUNT_FAILED": ErrorSpec(
        "ADB_REMOUNT_FAILED",
        "device",
        "adb remount reported a failure (output passed through verbatim)",
        "Run `alb adb root` first; if the output mentions verity, disable it yourself with "
        "`adb disable-verity` and reboot — alb never runs that automatically",
    ),
    "ROOT_REQUIRED": ErrorSpec(
        "ROOT_REQUIRED",
        "permission",
        "The device path cannot be read by the shell user",
        "Run `alb adb root`, then retry",
    ),
    "PSTORE_PULL_FAILED": ErrorSpec(
        "PSTORE_PULL_FAILED",
        "transport",
        "Crash records were listed on the device but none could be pulled",
        "Read each file's error in the result; run `alb adb root` if they say permission "
        "denied, otherwise check `alb devices`",
    ),
    # ─── device port channel (alb port) ──────────────────────────────
    # A TCP stream to a port ON the device, opened through the adb server's
    # own protocol (host:transport + tcp:<port>) — no `adb forward`, whose
    # listener would sit on the bench host and be unreachable from here.
    "PORT_DEVICE_REFUSED": ErrorSpec(
        "PORT_DEVICE_REFUSED",
        "device",
        "The device refused the connection to that port (nothing is listening there)",
        "Start the server on the device first (e.g. `gdbserver :<port> --attach <pid>`), "
        "then `alb port check <port>`",
    ),
    "PORT_DEVICE_AMBIGUOUS": ErrorSpec(
        "PORT_DEVICE_AMBIGUOUS",
        "device",
        "More than one device is attached; the target must be named",
        "Run `alb devices` and pass `--serial <one of them>`",
    ),
    "PORT_BIND_NOT_LOOPBACK": ErrorSpec(
        "PORT_BIND_NOT_LOOPBACK",
        "input",
        "Refusing to expose a device port on a non-loopback address",
        "Bind 127.0.0.1, or pass `--allow-remote` if sharing it on the network is intended",
    ),
    "PORT_LOCAL_BUSY": ErrorSpec(
        "PORT_LOCAL_BUSY",
        "transport",
        "The requested local port is already in use",
        "Pick another with `--local`, or omit it to take a free port",
    ),
    "ADB_ENDPOINT_UNSUPPORTED": ErrorSpec(
        "ADB_ENDPOINT_UNSUPPORTED",
        "transport",
        "The resolved adb server endpoint is not a tcp: socket",
        "Set ADB_SERVER_SOCKET or config `server_socket` to `tcp:<host>:<port>`",
    ),
    "ADB_PROTOCOL_ERROR": ErrorSpec(
        "ADB_PROTOCOL_ERROR",
        "transport",
        "The endpoint did not answer like an adb server (bad reply, hung up, or timed out)",
        "Check that the endpoint is an adb server: `alb status`",
    ),
    # ─── bench op table (ADR-059) ────────────────────────────────────
    # Four codes, four different fixes: pick an op the agent has, fix a
    # value, update the agent, or look at the bench host's disk.
    "BENCH_OP_UNKNOWN": ErrorSpec(
        "BENCH_OP_UNKNOWN",
        "transport",
        "The agent on the bench host has no such op",
        "Run `alb bench ops` to see what this agent offers",
    ),
    "BENCH_PARAM_REJECTED": ErrorSpec(
        "BENCH_PARAM_REJECTED",
        "transport",
        "A parameter value was refused (wrong type, out of range, or not one "
        "of the allowed choices)",
        "Run `alb bench ops` — each op lists its parameters with their kinds "
        "and allowed values",
    ),
    "BENCH_AGENT_TOO_OLD": ErrorSpec(
        "BENCH_AGENT_TOO_OLD",
        "transport",
        "The agent on the bench host predates this op",
        "Copy the current alb_agent.py to the bench host and restart the agent",
    ),
    "BENCH_LOG_UNAVAILABLE": ErrorSpec(
        "BENCH_LOG_UNAVAILABLE",
        "transport",
        "The log source is configured on the agent but cannot be read right now",
        "Check that the file exists on the bench host and the agent's account "
        "may read it; `alb bench ops` lists the sources this agent offers",
    ),
}


def lookup(code: str) -> ErrorSpec | None:
    return ERROR_CODES.get(code)
