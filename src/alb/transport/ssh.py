"""SshTransport — method C (on-device sshd).

Use when the Android device runs sshd (dropbear embedded in the ROM, or
Termux openssh). Unlocks rsync, tmux, sshfs, complex port forwarding,
and true multi-session concurrency — none of which adb can do.

asyncssh is imported lazily so alb works without it if users only need
adb/serial.
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
class SshConnSpec:
    host: str
    port: int = 22
    user: str = "root"
    key_path: str | None = None
    known_hosts: str | None = None  # None -> strict-accept; "" -> skip check
    connect_timeout: int = 15


class SshTransport(Transport):
    name = "ssh"
    supports_boot_log = False
    supports_recovery = False

    def __init__(
        self,
        *,
        host: str,
        port: int = 22,
        user: str = "root",
        key_path: str | None = None,
        known_hosts: str | None = None,
        connect_timeout: int = 15,
    ) -> None:
        self.spec = SshConnSpec(
            host=host,
            port=port,
            user=user,
            key_path=key_path,
            known_hosts=known_hosts,
            connect_timeout=connect_timeout,
        )

    # ── asyncssh lazy import ─────────────────────────────────────
    @staticmethod
    def _load_asyncssh():  # type: ignore[no-untyped-def]
        try:
            import asyncssh  # type: ignore[import-not-found]
        except ImportError as e:
            raise ImportError(
                "asyncssh is required for the SSH transport. "
                "Install with: uv add asyncssh (or re-run scripts/install.sh)."
            ) from e
        return asyncssh

    async def _connect(self):  # type: ignore[no-untyped-def]
        """Open a fresh SSH connection. Caller is responsible for close()."""
        asyncssh = self._load_asyncssh()
        kwargs: dict[str, Any] = {
            "host": self.spec.host,
            "port": self.spec.port,
            "username": self.spec.user,
            "connect_timeout": self.spec.connect_timeout,
        }
        if self.spec.key_path:
            kwargs["client_keys"] = [os.path.expanduser(self.spec.key_path)]
        # Known-hosts handling:
        #   None        -> strict: use default ~/.ssh/known_hosts
        #   ""          -> relaxed: accept unknown hosts (common for fresh boards)
        #   <path>      -> explicit file
        if self.spec.known_hosts == "":
            kwargs["known_hosts"] = None
        elif self.spec.known_hosts is not None:
            kwargs["known_hosts"] = os.path.expanduser(self.spec.known_hosts)
        return await asyncssh.connect(**kwargs)

    # ── Transport interface ──────────────────────────────────────
    async def shell(self, cmd: str, *, timeout: int = 30) -> ShellResult:
        start = perf_counter()
        try:
            conn = await self._connect()
        except ImportError as e:
            return ShellResult(
                ok=False, exit_code=-1, stderr=str(e),
                error_code="SYSTEM_DEPENDENCY_MISSING",
                duration_ms=int((perf_counter() - start) * 1000),
            )
        except Exception as e:  # noqa: BLE001
            return ShellResult(
                ok=False, exit_code=-1, stderr=str(e),
                error_code=_classify_ssh_error(e),
                duration_ms=int((perf_counter() - start) * 1000),
            )

        try:
            try:
                result = await asyncio.wait_for(conn.run(cmd), timeout=timeout)
            except asyncio.TimeoutError:
                return ShellResult(
                    ok=False, exit_code=-1,
                    stderr=f"SSH command timed out after {timeout}s",
                    error_code="TIMEOUT_SHELL",
                    duration_ms=int((perf_counter() - start) * 1000),
                )

            exit_code = result.exit_status or 0
            stdout = (result.stdout or "") if isinstance(result.stdout, str) else result.stdout.decode(errors="replace")
            stderr = (result.stderr or "") if isinstance(result.stderr, str) else result.stderr.decode(errors="replace")

            if exit_code != 0:
                return ShellResult(
                    ok=False, exit_code=exit_code,
                    stdout=stdout, stderr=stderr,
                    duration_ms=int((perf_counter() - start) * 1000),
                    error_code="SSH_COMMAND_FAILED",
                )
            return ShellResult(
                ok=True, exit_code=0,
                stdout=stdout, stderr=stderr,
                duration_ms=int((perf_counter() - start) * 1000),
            )
        finally:
            conn.close()
            try:
                await asyncio.wait_for(conn.wait_closed(), timeout=2)
            except (asyncio.TimeoutError, Exception):
                pass

    async def stream_read(
        self, source: str, **kwargs: Any
    ) -> AsyncIterator[bytes]:
        """Stream output from a long-running command.

        source mapping:
            logcat   -> logcat -v threadtime [filter]
            dmesg    -> dmesg -w  (or cat /proc/kmsg)
            kmsg     -> cat /proc/kmsg
        """
        if source == "logcat":
            cmd = "logcat -v threadtime"
            if kwargs.get("clear"):
                cmd = "logcat -c && " + cmd
            if filt := kwargs.get("filter"):
                cmd += " " + filt
        elif source == "dmesg":
            cmd = "dmesg -w"
        elif source == "kmsg":
            cmd = "cat /proc/kmsg"
        else:
            raise ValueError(f"Unknown stream source: {source}")

        try:
            conn = await self._connect()
        except Exception as e:  # noqa: BLE001
            yield f"[alb ssh open failed: {e}]\n".encode()
            return

        try:
            async with conn.create_process(cmd) as proc:
                async for line in proc.stdout:
                    if isinstance(line, str):
                        yield line.encode("utf-8", errors="replace")
                    else:
                        yield line
        finally:
            conn.close()
            try:
                await asyncio.wait_for(conn.wait_closed(), timeout=2)
            except (asyncio.TimeoutError, Exception):
                pass

    async def push(self, local: Path, remote: str) -> ShellResult:
        if not local.exists():
            return ShellResult(
                ok=False, exit_code=-1,
                stderr=f"Local path not found: {local}",
                error_code="FILE_NOT_FOUND",
            )
        return await self._scp(local=local, remote=remote, download=False)

    async def pull(self, remote: str, local: Path) -> ShellResult:
        local.parent.mkdir(parents=True, exist_ok=True)
        return await self._scp(local=local, remote=remote, download=True)

    async def _scp(self, *, local: Path, remote: str, download: bool) -> ShellResult:
        start = perf_counter()
        try:
            asyncssh = self._load_asyncssh()
            conn = await self._connect()
        except ImportError as e:
            return ShellResult(
                ok=False, exit_code=-1, stderr=str(e),
                error_code="SYSTEM_DEPENDENCY_MISSING",
                duration_ms=int((perf_counter() - start) * 1000),
            )
        except Exception as e:  # noqa: BLE001
            return ShellResult(
                ok=False, exit_code=-1, stderr=str(e),
                error_code=_classify_ssh_error(e),
                duration_ms=int((perf_counter() - start) * 1000),
            )
        try:
            if download:
                await asyncssh.scp((conn, remote), str(local), recurse=True, preserve=True)
            else:
                await asyncssh.scp(str(local), (conn, remote), recurse=True, preserve=True)
            return ShellResult(
                ok=True, exit_code=0,
                duration_ms=int((perf_counter() - start) * 1000),
            )
        except Exception as e:  # noqa: BLE001
            return ShellResult(
                ok=False, exit_code=-1, stderr=str(e),
                error_code="SSH_COMMAND_FAILED",
                duration_ms=int((perf_counter() - start) * 1000),
            )
        finally:
            conn.close()
            try:
                await asyncio.wait_for(conn.wait_closed(), timeout=2)
            except (asyncio.TimeoutError, Exception):
                pass

    async def forward(self, local_port: int, remote_port: int) -> ShellResult:
        """Forward a local TCP port to a remote port (ssh -L equivalent).

        Note: the forwarding channel is attached to a fresh connection that
        the caller must keep alive. For one-shot usage prefer capabilities
        that open their own dedicated forward context. M1 returns
        NOT_IMPLEMENTED until capabilities/network lands in M2.
        """
        return ShellResult(
            ok=False,
            exit_code=-1,
            stderr="forward() not implemented for ssh in M1; use ssh -L manually or wait for M2 network capability",
            error_code="TRANSPORT_NOT_SUPPORTED",
        )

    async def reboot(self, mode: str = "normal") -> ShellResult:
        if mode != "normal":
            return ShellResult(
                ok=False, exit_code=-1,
                stderr=(
                    f"SSH reboot mode '{mode}' not supported. Use adb for "
                    "recovery/bootloader/fastboot/sideload."
                ),
                error_code="TRANSPORT_NOT_SUPPORTED",
            )
        return await self.shell("reboot", timeout=10)

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
                    suggestion="mount -o remount,rw first, or push to /data/local/tmp",
                )
            if remote.startswith(("/dev/block/", "/proc/")):
                return PermissionResult(
                    behavior="deny",
                    reason=f"Writing to kernel-interface path: {remote}",
                    matched_rule="ssh.push.kernel-path",
                    suggestion="Use a regular file path",
                )
        return base

    async def health(self) -> dict[str, Any]:
        info: dict[str, Any] = {
            "transport": "ssh",
            "host": self.spec.host,
            "port": self.spec.port,
            "user": self.spec.user,
        }
        try:
            conn = await self._connect()
            info["ok"] = True
            info["connected"] = True
            try:
                r = await asyncio.wait_for(conn.run("uname -a"), timeout=5)
                info["uname"] = (r.stdout if isinstance(r.stdout, str)
                                 else r.stdout.decode(errors="replace")).strip()
            except Exception:  # noqa: BLE001
                pass
            conn.close()
            try:
                await asyncio.wait_for(conn.wait_closed(), timeout=2)
            except (asyncio.TimeoutError, Exception):
                pass
        except Exception as e:  # noqa: BLE001
            info["ok"] = False
            info["connected"] = False
            info["error"] = str(e)
            info["error_code"] = _classify_ssh_error(e)
        return info

    # ── SSH-specific helper (used by filesync.rsync_sync) ────────
    async def rsync(
        self,
        local_dir: Path,
        remote_dir: str,
        *,
        delete: bool = False,
        extra_args: list[str] | None = None,
    ) -> ShellResult:
        """Wrap local `rsync` using our ssh params.

        Requires:
            - rsync binary on host
            - ssh available (we build a `-e "ssh -i <key> -p <port>"` string)
            - rsync binary on device (usually missing on stock Android, see
              docs/capabilities/filesync.md)
        """
        start = perf_counter()
        if not shutil.which("rsync"):
            return ShellResult(
                ok=False, exit_code=-1,
                stderr="rsync binary not found on host",
                error_code="SYSTEM_DEPENDENCY_MISSING",
            )

        ssh_parts = ["ssh", "-p", str(self.spec.port)]
        if self.spec.key_path:
            ssh_parts += ["-i", os.path.expanduser(self.spec.key_path)]
        if self.spec.known_hosts == "":
            ssh_parts += [
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
            ]
        ssh_cmd = " ".join(ssh_parts)

        args = [
            "rsync", "-avz", "--partial", "--progress",
            "-e", ssh_cmd,
        ]
        if delete:
            args.append("--delete")
        if extra_args:
            args.extend(extra_args)
        src = str(local_dir).rstrip("/") + "/"
        dst = f"{self.spec.user}@{self.spec.host}:{remote_dir.rstrip('/')}/"
        args.extend([src, dst])

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, stderr_b = await proc.communicate()
            duration_ms = int((perf_counter() - start) * 1000)
            stdout = stdout_b.decode("utf-8", errors="replace") if stdout_b else ""
            stderr = stderr_b.decode("utf-8", errors="replace") if stderr_b else ""
            if (proc.returncode or 0) != 0:
                return ShellResult(
                    ok=False,
                    exit_code=proc.returncode or 0,
                    stdout=stdout, stderr=stderr,
                    duration_ms=duration_ms,
                    error_code="SSH_COMMAND_FAILED",
                )
            return ShellResult(
                ok=True, exit_code=0,
                stdout=stdout, stderr=stderr,
                duration_ms=duration_ms,
            )
        except FileNotFoundError:
            return ShellResult(
                ok=False, exit_code=-1,
                stderr="rsync/ssh binary missing from PATH",
                error_code="SYSTEM_DEPENDENCY_MISSING",
                duration_ms=int((perf_counter() - start) * 1000),
            )


# ─── Error classification ──────────────────────────────────────────
def _classify_ssh_error(exc: Exception) -> str:
    msg = str(exc).lower()
    if any(k in msg for k in ("permission denied", "authentication failed", "no authentication methods")):
        return "SSH_AUTH_FAILED"
    if any(k in msg for k in ("host key", "host verification", "known_hosts")):
        return "SSH_AUTH_FAILED"
    if any(k in msg for k in ("connection refused", "no route to host", "network is unreachable",
                                "name or service not known", "getaddrinfo")):
        return "SSH_HOST_UNREACHABLE"
    if "timeout" in msg or "timed out" in msg:
        return "TIMEOUT_CONNECT"
    if "no such file" in msg and "key" in msg:
        return "SSH_KEY_NOT_FOUND"
    return "SSH_COMMAND_FAILED"
