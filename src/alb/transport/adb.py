"""AdbTransport — method A (USB) and B (adb over WiFi).

Wraps the `adb` binary. Which adb *server* it talks to is decided by
:mod:`alb.infra.adb_endpoint` (ADR-057), not by this module — the reverse-tunnel
scenario (A, see docs/methods/01-ssh-tunnel-adb.md) and alb's own forwarder both
put a second adb server on the machine, and picking the wrong one produces an
empty device list rather than an error.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shutil
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

from alb.infra.adb_endpoint import AdbEndpoint, endpoint_conflict
from alb.infra.permissions import PermissionResult, default_check
from alb.infra.process import run as proc_run, spawn_stream

if False:  # TYPE_CHECKING shim
    from alb.transport.interactive import InteractiveShell  # noqa: F401
from alb.transport.base import ShellResult, Transport, TransferEvent


@dataclass(frozen=True)
class AdbDevice:
    serial: str
    state: str  # "device" / "offline" / "unauthorized" / ...
    product: str = ""
    model: str = ""
    transport_id: str = ""


class AdbBinaryMissing(RuntimeError):
    pass


class AdbTransport(Transport):
    """adb-based transport.

    Args:
        serial: target device serial (None lets adb use the only device).
        bin_path: path to the adb executable. Falls back to PATH lookup.
        server_socket: value to pass via ADB_SERVER_SOCKET env — which adb
            server to talk to. Callers should get this from
            :func:`alb.infra.adb_endpoint.resolve_endpoint` rather than
            inventing it; see ADR-057 for why the environment alone is not a
            safe answer.
        server_socket_source: where that value came from (config / env / hub /
            default). Reported by :meth:`health` and otherwise unused — an adb
            server that answers proves nothing about being the *right* one, so
            a diagnosis that cannot name the source cannot be acted on.
    """

    name = "adb"
    supports_boot_log = False
    supports_recovery = True

    def __init__(
        self,
        serial: str | None = None,
        bin_path: str = "adb",
        server_socket: str | None = None,
        server_socket_source: str = "",
    ) -> None:
        self.serial = serial
        self._bin = shutil.which(bin_path) or bin_path
        self._server_socket = server_socket or os.environ.get("ADB_SERVER_SOCKET")
        # Direct constructions (tests, one-off scripts) still get an honest
        # label rather than a blank one.
        if server_socket_source:
            self._server_socket_source = server_socket_source
        elif server_socket:
            self._server_socket_source = "explicit"
        elif self._server_socket:
            self._server_socket_source = "env"
        else:
            self._server_socket_source = "default"

    # ── Internal ──────────────────────────────────────────────────
    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        if self._server_socket:
            env["ADB_SERVER_SOCKET"] = self._server_socket
        return env

    def _base_cmd(self) -> list[str]:
        cmd = [self._bin]
        if self.serial:
            cmd += ["-s", self.serial]
        return cmd

    async def _run(
        self,
        args: list[str],
        *,
        timeout: int = 30,
        stdin: bytes | None = None,
    ) -> ShellResult:
        """Run an adb subcommand via the unified ProcessRunner.

        Maps generic :class:`ProcessResult` → adb-specific
        :class:`ShellResult` with transport-level error codes.
        """
        r = await proc_run(
            *self._base_cmd(),
            *args,
            timeout=timeout,
            stdin=stdin,
            env=self._env(),
        )

        if r.binary_missing:
            return ShellResult(
                ok=False,
                exit_code=-1,
                stderr=f"adb binary not found: {self._bin}",
                error_code="ADB_BINARY_NOT_FOUND",
                duration_ms=r.duration_ms,
            )

        if r.timed_out:
            return ShellResult(
                ok=False,
                exit_code=-1,
                stderr=f"adb command timed out after {timeout}s",
                error_code="TIMEOUT_SHELL",
                duration_ms=r.duration_ms,
            )

        if r.exit_code != 0:
            return ShellResult(
                ok=False,
                exit_code=r.exit_code,
                stdout=r.stdout,
                stderr=r.stderr,
                duration_ms=r.duration_ms,
                error_code=_classify_stderr(r.stderr),
            )

        return ShellResult(
            ok=True,
            exit_code=0,
            stdout=r.stdout,
            stderr=r.stderr,
            duration_ms=r.duration_ms,
        )

    # ── Transport interface ───────────────────────────────────────
    async def shell(self, cmd: str, *, timeout: int = 30) -> ShellResult:
        return await self._run(["shell", cmd], timeout=timeout)

    async def stream_read(
        self, source: str, **kwargs: Any
    ) -> AsyncIterator[bytes]:
        """Stream logcat / dmesg / kmsg output line by line.

        source: "logcat" | "dmesg" | "kmsg"
        Optional kwargs:
            filter: str — logcat filter spec (e.g. "*:E")
            clear: bool — run logcat -c first (logcat only)
        """
        if source == "logcat":
            args = ["logcat", "-v", "threadtime"]
            if kwargs.get("clear"):
                # logcat -c doesn't stream; run as a pre-step
                await self._run(["logcat", "-c"], timeout=5)
            if filt := kwargs.get("filter"):
                args += _parse_logcat_filter(filt)
        elif source == "dmesg":
            args = ["shell", "dmesg", "-w"]
        elif source == "kmsg":
            args = ["shell", "cat", "/proc/kmsg"]
        else:
            raise ValueError(f"Unknown stream source: {source}")

        async with spawn_stream(
            *self._base_cmd(),
            *args,
            env=self._env(),
        ) as proc:
            assert proc.stdout is not None
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                yield line

    async def interactive_shell(
        self,
        *,
        rows: int = 24,
        cols: int = 80,
    ) -> "InteractiveShell":
        """Spawn `adb shell` attached to a fresh PTY.

        adb's shell is line-discipline-aware when the client side is a
        TTY; piping stdin through a PTY gets us the same behavior we'd
        see at a terminal. The caller (Web Terminal WS) shuttles bytes
        in both directions and is responsible for `await shell.close()`.
        """
        from alb.transport.interactive import open_pty_subprocess

        return await open_pty_subprocess(
            *self._base_cmd(),
            "shell",
            env=self._env(),
            rows=rows,
            cols=cols,
        )

    async def push(self, local: Path, remote: str) -> ShellResult:
        if not local.exists():
            return ShellResult(
                ok=False,
                exit_code=-1,
                stderr=f"Local path not found: {local}",
                error_code="FILE_NOT_FOUND",
            )
        return await self._run(["push", str(local), remote], timeout=600)

    async def pull(self, remote: str, local: Path) -> ShellResult:
        local.parent.mkdir(parents=True, exist_ok=True)
        return await self._run(["pull", remote, str(local)], timeout=600)

    async def push_stream(
        self, local: Path, remote: str
    ) -> AsyncIterator[TransferEvent]:
        """Stream push progress · cancel via aclose() on the generator.

        adb push prints `[ N%] /path` lines to stderr in pipe mode and
        a final summary `1 file pushed. ...` to stdout. We tail both,
        emit `kind="progress"` for each `[N%]` we see, then a single
        `kind="done"` after the process exits. spawn_stream's finally
        guarantees subprocess teardown when the consumer stops iterating.

        MID-6 (functional audit 2026-05-02): users can finally cancel
        a hung push by closing the WS / aborting the iteration.
        """
        if not local.exists():
            yield TransferEvent(
                kind="done",
                ok=False,
                error=f"local path not found: {local}",
            )
            return

        start = perf_counter()
        args = [*self._base_cmd(), "push", str(local), remote]
        # Nested async generators: aclose() on the outer doesn't auto-
        # propagate to the inner. Explicit try/finally + aclose ensures
        # cancel cleanup runs synchronously when the consumer aborts
        # iteration, instead of waiting for GC.
        inner = _stream_transfer(args, env=self._env(), start=start)
        try:
            async for event in inner:
                yield event
        finally:
            await inner.aclose()

    async def pull_stream(
        self, remote: str, local: Path
    ) -> AsyncIterator[TransferEvent]:
        """Stream pull progress. Same contract as push_stream."""
        local.parent.mkdir(parents=True, exist_ok=True)

        start = perf_counter()
        args = [*self._base_cmd(), "pull", remote, str(local)]
        inner = _stream_transfer(args, env=self._env(), start=start)
        try:
            async for event in inner:
                yield event
        finally:
            await inner.aclose()

    async def forward(self, local_port: int, remote_port: int) -> ShellResult:
        return await self._run(
            ["forward", f"tcp:{local_port}", f"tcp:{remote_port}"],
        )

    async def reboot(self, mode: str = "normal") -> ShellResult:
        arg = "" if mode == "normal" else mode
        return await self._run(
            ["reboot", arg] if arg else ["reboot"],
            timeout=30,
        )

    async def check_permissions(
        self, action: str, input_data: dict[str, Any]
    ) -> PermissionResult:
        base = await default_check(self.name, action, input_data)
        if base.behavior == "deny":
            return base

        if action in ("filesync.push", "push"):
            remote = input_data.get("remote", "")
            if remote.startswith(("/system/", "/vendor/", "/product/", "/odm/")):
                return PermissionResult(
                    behavior="ask",
                    reason=f"Pushing to read-only system path: {remote}",
                    suggestion="mount -o remount,rw first, or use /data/local/tmp/",
                )
            if remote.startswith(("/dev/block/", "/proc/")):
                return PermissionResult(
                    behavior="deny",
                    reason=f"Writing to kernel-interface path: {remote}",
                    matched_rule="adb.push.kernel-path",
                    suggestion="Use a regular file path; /dev/block/* can brick the device",
                )

        if action in ("power.reboot", "reboot"):
            mode = input_data.get("mode", "normal")
            if mode in ("recovery", "bootloader", "fastboot", "sideload"):
                return PermissionResult(
                    behavior="ask",
                    reason=f"Rebooting to '{mode}' — device may not return automatically",
                    suggestion="Confirm you have a way back (another adb connection / UART)",
                )

        return base

    async def health(self) -> dict[str, Any]:
        # 1. adb binary exists
        bin_ok = bool(shutil.which(self._bin))
        info: dict[str, Any] = {
            "transport": "adb",
            "bin_path": self._bin,
            "bin_found": bin_ok,
            "server_socket": self._server_socket,
            "server_socket_source": self._server_socket_source,
        }
        if not bin_ok:
            info["ok"] = False
            info["error"] = "ADB_BINARY_NOT_FOUND"
            return info

        # 2. adb version
        r = await self._run(["version"], timeout=5)
        info["version"] = r.stdout.splitlines()[0] if r.ok else None

        # 3. adb server reachable?
        r = await self._run(["devices"], timeout=5)
        info["server_reachable"] = r.ok
        if r.ok:
            info["devices"] = parse_devices_output(r.stdout)
        info["ok"] = r.ok

        # 4. reachable but empty — the one case where "healthy" was a lie
        #    (ADR-057). Reaching *an* adb server says nothing about reaching
        #    the right one, and this is exactly where the operator stops
        #    looking. Only asked when the list is empty, and off the event
        #    loop because the probe may be a blocking HTTP call.
        if r.ok and not info.get("devices"):
            conflict = await asyncio.to_thread(
                endpoint_conflict,
                AdbEndpoint(self._server_socket, self._server_socket_source),
            )
            if conflict:
                info["hint"] = conflict
                info["ok"] = False
        if not r.ok:
            info["error"] = r.error_code or "ADB_SERVER_UNREACHABLE"
        return info

    # ── Convenience ───────────────────────────────────────────────
    async def devices(self) -> list[AdbDevice]:
        r = await self._run(["devices", "-l"], timeout=5)
        if not r.ok:
            return []
        return parse_devices_output(r.stdout)


# ─── Helpers ───────────────────────────────────────────────────────
def parse_devices_output(stdout: str) -> list[AdbDevice]:
    """Parse `adb devices -l` output."""
    devices: list[AdbDevice] = []
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line or line.startswith("List of devices") or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        kv = {k: v for k, v in (_split_kv(t) for t in parts[2:]) if k}
        devices.append(
            AdbDevice(
                serial=serial,
                state=state,
                product=kv.get("product", ""),
                model=kv.get("model", ""),
                transport_id=kv.get("transport_id", ""),
            )
        )
    return devices


def _split_kv(token: str) -> tuple[str, str]:
    if ":" in token:
        k, _, v = token.partition(":")
        return k, v
    return "", ""


def _parse_logcat_filter(filt: str) -> list[str]:
    """logcat filter may be either a full '-s'-style spec or a single tag."""
    # if the caller gives the full "*:E" etc, pass through as additional args
    return filt.split()


def _classify_stderr(stderr: str) -> str:
    low = stderr.lower()
    if "no devices/emulators found" in low:
        return "DEVICE_NOT_FOUND"
    if "device offline" in low:
        return "DEVICE_OFFLINE"
    if "unauthorized" in low:
        return "DEVICE_UNAUTHORIZED"
    if "cannot connect to daemon" in low or "connection refused" in low:
        return "ADB_SERVER_UNREACHABLE"
    if "command not found" in low:
        return "ADB_BINARY_NOT_FOUND"
    return "ADB_COMMAND_FAILED"


# ── Streaming transfer parser (MID-6) ────────────────────────────────

# Matches adb's progress format: "[ 12%] /sdcard/file" or "[100%] /sdcard/file"
# Some adb builds emit "[%5d] %s" (right-aligned), others "[%3d%%] %s".
# Both forms are captured here.
_ADB_PROGRESS_RE = re.compile(
    r"\[\s*(?P<pct>\d{1,3})\s*%\s*\]\s+(?P<file>.+?)\s*$"
)
# Matches final summary line, e.g. "/path: 1 file pushed. 12.3 MB/s
# (12345 bytes in 0.123s)" — used to pin bytes_transferred even when
# the per-file progress lines didn't surface (e.g. small files).
_ADB_SUMMARY_RE = re.compile(
    r"\((?P<bytes>\d+) bytes in", re.IGNORECASE
)


async def _stream_transfer(
    args: list[str],
    *,
    env: dict[str, str] | None,
    start: float,
) -> AsyncIterator[TransferEvent]:
    """Spawn adb push/pull and yield TransferEvent updates.

    Wire format observation (Android Platform Tools 30+):
      - Per-file progress `[ N%] /path` lands on STDERR (not stdout)
        even when stdout is a pipe — adb intentionally splits them
        so callers redirecting stdout get a clean final summary.
      - Final summary `1 file pushed/pulled. ... (NNNN bytes in ...)`
        lands on STDOUT.

    We tail stderr inline (yield progress as we parse), then drain
    stdout for the summary, then yield the terminal done event.

    Cancel: when the consumer breaks or aclose()s the generator,
    the finally block escalates SIGTERM → SIGKILL on the adb
    subprocess. No leaked processes.
    """
    last_bytes = 0
    last_file: str | None = None
    last_percent: float | None = None
    error_lines: list[str] = []
    final_bytes = 0

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except (FileNotFoundError, NotADirectoryError) as e:
        yield TransferEvent(
            kind="done", ok=False,
            error=f"adb binary missing or not executable: {e}",
            duration_ms=int((perf_counter() - start) * 1000),
        )
        return

    assert proc.stdout is not None and proc.stderr is not None

    async def _drain_stdout() -> None:
        """Background reader for stdout — captures final summary line."""
        nonlocal final_bytes
        try:
            async for raw in proc.stdout:  # type: ignore[union-attr]
                line = raw.decode("utf-8", errors="replace").rstrip()
                m = _ADB_SUMMARY_RE.search(line)
                if m:
                    final_bytes = int(m.group("bytes"))
        except Exception:  # noqa: BLE001 — drain best-effort
            pass

    stdout_task = asyncio.create_task(_drain_stdout())

    try:
        # Inline read of stderr — yield progress events as they arrive.
        async for raw in proc.stderr:
            line = raw.decode("utf-8", errors="replace").rstrip()
            if not line:
                continue
            m = _ADB_PROGRESS_RE.search(line)
            if m:
                last_percent = float(m.group("pct"))
                last_file = m.group("file").strip()
                yield TransferEvent(
                    kind="progress",
                    percent=last_percent,
                    file=last_file,
                    bytes_transferred=last_bytes,
                )
            else:
                error_lines.append(line)

        # stderr EOF — process is finishing; drain stdout summary.
        await proc.wait()
        with contextlib.suppress(asyncio.CancelledError):
            await stdout_task
        rc = proc.returncode if proc.returncode is not None else -1
        ok = rc == 0
        bytes_xfer = final_bytes if final_bytes else last_bytes
        yield TransferEvent(
            kind="done",
            ok=ok,
            bytes_transferred=bytes_xfer,
            percent=100.0 if ok else last_percent,
            file=last_file,
            duration_ms=int((perf_counter() - start) * 1000),
            error=None if ok else (
                "; ".join(error_lines[-3:])[:500] if error_lines
                else f"adb exited with code {rc}"
            ),
        )
    finally:
        # Consumer cancelled mid-iteration → terminate adb so no
        # zombie push/pull continues in the background. Also covers
        # the happy path's already-exited proc (no-op).
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        proc.kill()
                    await proc.wait()
        if not stdout_task.done():
            stdout_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stdout_task
