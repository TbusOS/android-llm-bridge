"""Transport ABC — the architectural core interface.

All concrete transports (adb, ssh, serial, hybrid) implement this ABC.
See docs/architecture.md §二 for the full design.

M0 skeleton; implementations land in M1.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from alb.infra.permissions import PermissionResult, default_check

# Forward declaration — concrete import deferred to runtime to keep
# the ABC import lightweight (interactive PTY pulls in fcntl/termios).
if False:  # TYPE_CHECKING shim, no runtime cost
    from alb.transport.interactive import InteractiveShell  # noqa: F401


@dataclass(frozen=True)
class ShellResult:
    """Result of a shell command execution via any transport."""

    ok: bool
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    artifacts: list[Path] = field(default_factory=list)
    error_code: str | None = None  # Matches docs/errors.md


class Transport(ABC):
    """All concrete transports (adb / ssh / serial / hybrid) implement this ABC.

    Key guarantees:
    - All methods are async (asyncio).
    - Errors are returned structurally (don't raise).
    - `shell` and `stream_read` are separate — keeps read/write paths isolated
      (borrowed from Claude Code's HybridTransport design).
    - Implementations must call `check_permissions` before any state-changing op.
    """

    name: str = "base"
    supports_boot_log: bool = False  # only serial (G) = True
    supports_recovery: bool = False  # only adb (A) = True

    # ── Basic ops ─────────────────────────────────────────────────
    @abstractmethod
    async def shell(self, cmd: str, *, timeout: int = 30) -> ShellResult:
        """Execute a command and return the result.

        MUST call check_permissions first for state-changing commands.
        """

    @abstractmethod
    async def stream_read(
        self, source: str, **kwargs: Any
    ) -> AsyncIterator[bytes]:
        """Stream bytes from a source (logcat / dmesg / kmsg / uart).

        The caller decides where the bytes go (file sink, event bus, etc.).
        """

    # ── File transfer ─────────────────────────────────────────────
    @abstractmethod
    async def push(self, local: Path, remote: str) -> ShellResult:
        """Push a local file/dir to the device."""

    @abstractmethod
    async def pull(self, remote: str, local: Path) -> ShellResult:
        """Pull a remote file/dir to local."""

    # ── Port forwarding (optional) ────────────────────────────────
    async def forward(self, local_port: int, remote_port: int) -> ShellResult:
        """Forward a TCP port. Not supported by serial."""
        raise NotImplementedError(f"{self.name} does not support port forwarding")

    # ── Interactive PTY shell (M3 — Web Terminal feeds this) ──────
    async def interactive_shell(
        self,
        *,
        rows: int = 24,
        cols: int = 80,
    ) -> "InteractiveShell":
        """Open a PTY-backed bidirectional shell session.

        Default raises NotImplementedError. Subclasses that can fork
        an interactive shell (adb / serial / ssh) override this and
        return an InteractiveShell. Caller is responsible for calling
        `await shell.close()` when done — wrapping with `async with`
        is not provided here because the shell itself is the resource.
        """
        raise NotImplementedError(
            f"{self.name} does not support interactive_shell()"
        )

    # ── Device control ────────────────────────────────────────────
    @abstractmethod
    async def reboot(self, mode: str = "normal") -> ShellResult:
        """Reboot to `mode`: normal / recovery / bootloader / fastboot / sideload.

        Some modes may not be supported by all transports (e.g. recovery requires adb).
        """

    # ── Permission hook ───────────────────────────────────────────
    async def check_permissions(
        self, action: str, input_data: dict[str, Any]
    ) -> PermissionResult:
        """Return an allow/ask/deny decision for `action` with `input_data`.

        Default impl consults the global permission engine; subclasses can
        override to add transport-specific rules (e.g. ssh.push rejecting /system/).
        """
        return await default_check(self.name, action, input_data)

    # ── Health / info ─────────────────────────────────────────────
    @abstractmethod
    async def health(self) -> dict[str, Any]:
        """Connectivity & state snapshot for `alb status`."""
