"""HybridTransport — smart router that picks the best sub-transport per op.

Motivation (see docs/architecture.md §二 and ADR-004):
    Real debugging sessions rarely use one channel.  `adb` is great for
    flashing and recovery, `ssh` wins on rsync and tmux, `serial` is the
    only one that can see u-boot / kernel panics.  Forcing the caller to
    pick manually is error-prone, so we route per-operation instead.

Routing matrix (primary is the user-declared default, alternates fill gaps):

    shell                           → primary
    stream_read("uart")             → serial (only one capable)
    stream_read("logcat")           → adb → ssh            (serial skipped)
    stream_read("dmesg"/"kmsg")     → adb → ssh → serial
    push / pull                     → ssh → adb            (serial deny)
    forward                         → adb → ssh            (serial deny)
    reboot recovery/bootloader/     → adb (only adb can)
    fastboot/sideload
    reboot normal                   → primary

check_permissions is delegated to the primary sub-transport — callers who
need strict per-transport rules (e.g. "never ssh.shell this pattern")
should compose their own transport directly rather than via hybrid.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from alb.infra.permissions import PermissionResult
from alb.transport.base import ShellResult, Transport

# ─── Routing constants ───────────────────────────────────────────────

_BOOTLOADER_MODES = frozenset({"recovery", "bootloader", "fastboot", "sideload"})

# Preferred order per operation (first hit wins).  "serial" absence means
# that transport is never a candidate for that op.
_STREAM_PREFERENCE: dict[str, tuple[str, ...]] = {
    "uart": ("serial",),
    "logcat": ("adb", "ssh"),
    "dmesg": ("adb", "ssh", "serial"),
    "kmsg": ("adb", "ssh", "serial"),
}
_FILE_PREFERENCE: tuple[str, ...] = ("ssh", "adb")
_FORWARD_PREFERENCE: tuple[str, ...] = ("adb", "ssh")


# ─── Helpers ─────────────────────────────────────────────────────────


def _no_route(op: str, details: str = "") -> ShellResult:
    msg = f"hybrid: no sub-transport can handle {op}"
    if details:
        msg += f" ({details})"
    return ShellResult(
        ok=False,
        exit_code=-1,
        stderr=msg,
        error_code="TRANSPORT_NOT_SUPPORTED",
    )


# ─── Transport ───────────────────────────────────────────────────────


class HybridTransport(Transport):
    """Wrap a primary transport and a set of alternates; route per op.

    Args:
        primary: the default transport; handles shell and anything the
            routing matrix doesn't otherwise specify.
        alternates: additional transports to consider when the matrix
            prefers them.  Order is not significant; selection is by
            `.name` attribute ("adb" / "ssh" / "serial").

    Example::

        hybrid = HybridTransport(
            primary=AdbTransport(serial="abc123"),
            alternates=[
                SshTransport(host="192.168.1.10"),
                SerialTransport(port="/dev/ttyUSB0"),
            ],
        )
        # logcat → adb, rsync-style push → ssh, boot log → serial
        await hybrid.stream_read("logcat")
        await hybrid.push(Path("build.apk"), "/data/local/tmp/")
        await hybrid.stream_read("uart")
    """

    name = "hybrid"

    def __init__(
        self,
        primary: Transport,
        alternates: list[Transport] | None = None,
    ) -> None:
        if primary is None:
            raise ValueError("HybridTransport requires a primary transport")
        self.primary = primary
        self.alternates: list[Transport] = list(alternates or [])

        # Computed capability flags — True if *any* sub-transport supports.
        all_ts = self._all()
        self.supports_boot_log = any(t.supports_boot_log for t in all_ts)
        self.supports_recovery = any(t.supports_recovery for t in all_ts)

    # ── Internal helpers ──────────────────────────────────────────
    def _all(self) -> list[Transport]:
        return [self.primary, *self.alternates]

    def _by_name(self, name: str) -> Transport | None:
        """Return the first sub-transport with matching `.name`, else None."""
        for t in self._all():
            if t.name == name:
                return t
        return None

    def _pick_by_preference(self, order: tuple[str, ...]) -> Transport | None:
        for name in order:
            t = self._by_name(name)
            if t is not None:
                return t
        return None

    def pick_for(self, op: str, hint: Any = None) -> Transport | None:
        """Return the sub-transport that should handle `op`, or None if none can.

        Public so callers and tests can inspect routing decisions without
        actually invoking the op.
        """
        if op == "shell":
            return self.primary

        if op == "stream_read":
            source = hint if isinstance(hint, str) else ""
            order = _STREAM_PREFERENCE.get(source)
            if order is None:
                # Unknown source — try primary, it will raise/fail per its own contract.
                return self.primary
            return self._pick_by_preference(order)

        if op in ("push", "pull"):
            return self._pick_by_preference(_FILE_PREFERENCE)

        if op == "forward":
            return self._pick_by_preference(_FORWARD_PREFERENCE)

        if op == "reboot":
            mode = hint if isinstance(hint, str) else "normal"
            if mode in _BOOTLOADER_MODES:
                return self._by_name("adb")  # only adb can reach bootloader-land
            return self.primary

        # Unknown op — conservative default.
        return self.primary

    # ── Transport ABC implementations ────────────────────────────
    async def shell(self, cmd: str, *, timeout: int = 30) -> ShellResult:
        t = self.pick_for("shell", cmd)
        if t is None:
            return _no_route("shell")
        return await t.shell(cmd, timeout=timeout)

    async def stream_read(
        self, source: str, **kwargs: Any
    ) -> AsyncIterator[bytes]:
        t = self.pick_for("stream_read", source)
        if t is None:
            raise NotImplementedError(
                f"hybrid: no sub-transport provides stream_read({source!r})"
            )
        async for chunk in t.stream_read(source, **kwargs):
            yield chunk

    async def push(self, local: Path, remote: str) -> ShellResult:
        t = self.pick_for("push")
        if t is None:
            return _no_route("push", "need adb or ssh sub-transport")
        return await t.push(local, remote)

    async def pull(self, remote: str, local: Path) -> ShellResult:
        t = self.pick_for("pull")
        if t is None:
            return _no_route("pull", "need adb or ssh sub-transport")
        return await t.pull(remote, local)

    async def forward(self, local_port: int, remote_port: int) -> ShellResult:
        t = self.pick_for("forward")
        if t is None:
            return _no_route("forward", "need adb or ssh sub-transport")
        return await t.forward(local_port, remote_port)

    async def reboot(self, mode: str = "normal") -> ShellResult:
        t = self.pick_for("reboot", mode)
        if t is None:
            return _no_route("reboot", f"mode={mode!r} requires adb")
        return await t.reboot(mode)

    async def check_permissions(
        self, action: str, input_data: dict[str, Any]
    ) -> PermissionResult:
        """Delegate to the primary sub-transport.

        Note: sub-transport picked by pick_for() will also run its own
        check at execution time, so transport-specific rules (e.g. ssh
        rejecting /system/ writes) are still enforced.
        """
        return await self.primary.check_permissions(action, input_data)

    async def health(self) -> dict[str, Any]:
        """Aggregate health snapshots from every sub-transport."""
        snapshots: dict[str, Any] = {}
        for t in self._all():
            try:
                snapshots[t.name] = await t.health()
            except Exception as exc:  # pragma: no cover — defensive
                snapshots[t.name] = {"reachable": False, "error": str(exc)}
        return {
            "transport": "hybrid",
            "primary": self.primary.name,
            "alternates": [t.name for t in self.alternates],
            "sub": snapshots,
        }
