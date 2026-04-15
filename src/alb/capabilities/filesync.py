"""filesync capability — push / pull between host and device.

M1: routes through the active Transport.push / .pull.
M2 will add rsync (ssh-only) and smart routing by size.
See docs/capabilities/filesync.md.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alb.infra.result import Result, fail, ok
from alb.infra.workspace import iso_timestamp, workspace_path
from alb.transport.base import Transport


@dataclass(frozen=True)
class PushResult:
    local: str
    remote: str
    bytes_transferred: int
    verified: bool
    duration_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "local": self.local,
            "remote": self.remote,
            "bytes_transferred": self.bytes_transferred,
            "verified": self.verified,
            "duration_ms": self.duration_ms,
        }


@dataclass(frozen=True)
class PullResult:
    remote: str
    local: str
    duration_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "remote": self.remote,
            "local": self.local,
            "duration_ms": self.duration_ms,
        }


async def push(
    transport: Transport,
    local: Path,
    remote: str,
    *,
    verify: bool = False,
) -> Result[PushResult]:
    """Push a local file (or directory) to the device.

    LLM: verify=True adds an md5 round-trip (slower). Default off.
    """
    if not local.exists():
        return fail(
            code="FILE_NOT_FOUND",
            message=f"Local path not found: {local}",
            suggestion="Check the path",
            category="io",
        )

    perm = await transport.check_permissions(
        "filesync.push",
        {"local": str(local), "remote": remote},
    )
    if perm.behavior == "deny":
        return fail(
            code="PERMISSION_DENIED",
            message=perm.reason or "push blocked",
            suggestion=perm.suggestion or "",
            category="permission",
            details={"matched_rule": perm.matched_rule},
        )
    if perm.behavior == "ask":
        return fail(
            code="PERMISSION_DENIED",
            message=perm.reason or "push needs confirmation",
            suggestion=perm.suggestion or "Re-run with --allow-dangerous",
            category="permission",
            details={"behavior": "ask", "matched_rule": perm.matched_rule},
        )

    r = await transport.push(local, remote)
    if not r.ok:
        return fail(
            code=r.error_code or "ADB_COMMAND_FAILED",
            message=r.stderr.strip() or "push failed",
            suggestion="Check device storage and remote path",
            category="transport",
            details={"stderr": r.stderr, "exit_code": r.exit_code},
            timing_ms=r.duration_ms,
        )

    verified = False
    if verify and local.is_file():
        verified = await _verify_md5(transport, local, remote)

    return ok(
        data=PushResult(
            local=str(local),
            remote=remote,
            bytes_transferred=local.stat().st_size if local.is_file() else 0,
            verified=verified,
            duration_ms=r.duration_ms,
        ),
        timing_ms=r.duration_ms,
    )


async def pull(
    transport: Transport,
    remote: str,
    local: Path | None = None,
    *,
    device: str | None = None,
) -> Result[PullResult]:
    """Pull a remote file/dir to local.

    If `local` is None, lands in workspace/devices/<serial>/pulls/<basename>-<ts>.
    """
    if local is None:
        base = remote.rstrip("/").rsplit("/", 1)[-1] or "pull"
        local = workspace_path(
            "pulls",
            f"{base}-{iso_timestamp()}",
            device=device,
        )

    r = await transport.pull(remote, local)
    if not r.ok:
        return fail(
            code=r.error_code or "ADB_COMMAND_FAILED",
            message=r.stderr.strip() or "pull failed",
            suggestion="Check the remote path exists",
            category="transport",
            details={"stderr": r.stderr, "exit_code": r.exit_code},
            timing_ms=r.duration_ms,
        )

    return ok(
        data=PullResult(
            remote=remote,
            local=str(local),
            duration_ms=r.duration_ms,
        ),
        artifacts=[local],
        timing_ms=r.duration_ms,
    )


async def rsync_sync(
    transport: Transport,
    local_dir: Path,
    remote_dir: str,
    *,
    delete: bool = False,
    extra_args: list[str] | None = None,
) -> Result[dict[str, Any]]:
    """Incremental directory sync. Requires SshTransport (method C).

    Much faster than alb_push for large directories with few changes.
    """
    if transport.name != "ssh":
        return fail(
            code="TRANSPORT_NOT_SUPPORTED",
            message=f"rsync requires ssh transport, got {transport.name}",
            suggestion="Run: alb setup ssh  (or set ALB_TRANSPORT=ssh)",
            category="transport",
        )
    if not local_dir.exists():
        return fail(
            code="FILE_NOT_FOUND",
            message=f"Local directory not found: {local_dir}",
            suggestion="Check the path",
            category="io",
        )

    perm = await transport.check_permissions(
        "filesync.rsync",
        {"local_dir": str(local_dir), "remote_dir": remote_dir, "delete": delete},
    )
    if perm.behavior == "deny":
        return fail(
            code="PERMISSION_DENIED",
            message=perm.reason or "rsync blocked",
            suggestion=perm.suggestion or "",
            category="permission",
        )
    if perm.behavior == "ask":
        return fail(
            code="PERMISSION_DENIED",
            message=perm.reason or "rsync needs confirmation",
            suggestion=perm.suggestion or "Re-run with --allow-dangerous",
            category="permission",
            details={"behavior": "ask"},
        )

    # SshTransport exposes .rsync — avoid importing ssh transport here to
    # keep this module transport-agnostic at import time.
    if not hasattr(transport, "rsync"):
        return fail(
            code="TRANSPORT_NOT_SUPPORTED",
            message="Active ssh transport does not expose rsync()",
            suggestion="Upgrade alb; this should not happen",
            category="capability",
        )

    r = await transport.rsync(
        local_dir, remote_dir, delete=delete, extra_args=extra_args
    )
    if not r.ok:
        return fail(
            code=r.error_code or "SSH_COMMAND_FAILED",
            message=(r.stderr or "").strip() or "rsync failed",
            suggestion="Check rsync is installed on both host and device, and the remote path is writable",
            category="transport",
            details={"stdout": r.stdout, "stderr": r.stderr, "exit_code": r.exit_code},
            timing_ms=r.duration_ms,
        )

    return ok(
        data={
            "local_dir": str(local_dir),
            "remote_dir": remote_dir,
            "delete": delete,
            "stdout_tail": "\n".join(r.stdout.splitlines()[-10:]) if r.stdout else "",
            "duration_ms": r.duration_ms,
        },
        timing_ms=r.duration_ms,
    )


# ─── Helpers ───────────────────────────────────────────────────────
async def _verify_md5(transport: Transport, local: Path, remote: str) -> bool:
    local_md5 = hashlib.md5(local.read_bytes()).hexdigest()
    r = await transport.shell(f"md5sum {remote}", timeout=30)
    if not r.ok:
        return False
    remote_md5 = r.stdout.strip().split()[0] if r.stdout.strip() else ""
    return bool(remote_md5) and remote_md5 == local_md5
