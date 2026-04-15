"""AdbTransport — method A (USB) and B (adb over WiFi).

Wraps the `adb` binary. Respects ADB_SERVER_SOCKET so the Xshell reverse-tunnel
scenario (A, see docs/methods/01-ssh-tunnel-adb.md) works transparently.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

from alb.infra.permissions import PermissionResult, default_check
from alb.transport.base import ShellResult, Transport


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
        server_socket: value to pass via ADB_SERVER_SOCKET env — required for
            scenario A (Xshell reverse tunnel to a Windows-side adb server).
    """

    name = "adb"
    supports_boot_log = False
    supports_recovery = True

    def __init__(
        self,
        serial: str | None = None,
        bin_path: str = "adb",
        server_socket: str | None = None,
    ) -> None:
        self.serial = serial
        self._bin = shutil.which(bin_path) or bin_path
        self._server_socket = server_socket or os.environ.get("ADB_SERVER_SOCKET")

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
        start = perf_counter()
        try:
            proc = await asyncio.create_subprocess_exec(
                *self._base_cmd(),
                *args,
                stdin=asyncio.subprocess.PIPE if stdin else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._env(),
            )
        except FileNotFoundError:
            return ShellResult(
                ok=False,
                exit_code=-1,
                stderr=f"adb binary not found: {self._bin}",
                error_code="ADB_BINARY_NOT_FOUND",
                duration_ms=int((perf_counter() - start) * 1000),
            )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=stdin),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return ShellResult(
                ok=False,
                exit_code=-1,
                stderr=f"adb command timed out after {timeout}s",
                error_code="TIMEOUT_SHELL",
                duration_ms=int((perf_counter() - start) * 1000),
            )

        duration_ms = int((perf_counter() - start) * 1000)
        exit_code = proc.returncode or 0
        out = stdout.decode("utf-8", errors="replace") if stdout else ""
        err = stderr.decode("utf-8", errors="replace") if stderr else ""

        if exit_code != 0:
            code = _classify_stderr(err)
            return ShellResult(
                ok=False,
                exit_code=exit_code,
                stdout=out,
                stderr=err,
                duration_ms=duration_ms,
                error_code=code,
            )

        return ShellResult(
            ok=True,
            exit_code=exit_code,
            stdout=out,
            stderr=err,
            duration_ms=duration_ms,
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

        proc = await asyncio.create_subprocess_exec(
            *self._base_cmd(),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=self._env(),
        )
        try:
            assert proc.stdout is not None
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                yield line
        finally:
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()

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
