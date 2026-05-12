"""Workspace path helpers.

Convention: all artifacts land under `workspace/devices/<serial>/<category>/`.
See docs/architecture.md §四 for the full scheme.

M0 skeleton; full implementation in M1.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path

# session_id factory format: `<YYYYMMDD>-<8 hex>` (see agent/session.new_session_id).
# The pattern is intentionally tighter than the factory so we reject any
# user-supplied id that could escape the sessions/ root via `..` / absolute
# paths / unicode trickery. See L-035 (path-traversal hardening).
_SAFE_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")

# device serial shape: adb serial / ssh host / serial port basename.  Mirrors
# the conservative shape from `api/uart_stream_route._DEVICE_SAFE_RE` (the
# original N=1 site). Shared here so `workspace_path` can enforce L-035 at
# root layer for the `device=` keyword (a known user-input vector via
# `--device` CLI flag and `?device=` query params).
# Leading char must be alnum so we reject `.` / `..` / `.foo` even though
# the remaining char class allows `.` (for IP-style serials like 192.168.x.x).
_SAFE_DEVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")


class InvalidSessionId(ValueError):
    """Raised when a user-supplied session_id would escape `sessions/`."""


class InvalidDeviceSerial(ValueError):
    """Raised when a user-supplied device serial would escape the workspace."""


def workspace_root() -> Path:
    """Return the workspace root. Configurable via ALB_WORKSPACE env."""
    env = os.environ.get("ALB_WORKSPACE")
    if env:
        return Path(env).expanduser().resolve()
    # Default: <repo>/workspace (dev) or ~/.alb-workspace (installed)
    cwd_ws = Path.cwd() / "workspace"
    if cwd_ws.exists():
        return cwd_ws
    return Path.home() / ".alb-workspace"


def iso_timestamp() -> str:
    """ISO 8601 timestamp safe for filenames (no colons).

    Example: '2026-04-15T10-30-00'
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")


def workspace_path(
    category: str,
    filename: str,
    *,
    device: str | None = None,
    ensure_dir: bool = True,
) -> Path:
    """Build a canonical artifact path.

    Example:
      workspace_path('logs', 'xxx.txt', device='abc123')
      -> /ws/devices/abc123/logs/xxx.txt

    Raises `InvalidDeviceSerial` when `device` would escape the workspace
    (`..`, absolute paths, separators, non-ASCII, > 64 chars).  Mirrors
    the L-035 root-layer-enforce pattern from `session_path`.  Category
    and filename are internal (caller-controlled) and not validated.
    """
    if device and not _SAFE_DEVICE_RE.match(device):
        # Falsy `device` (None / "") falls through to no-device path below.
        raise InvalidDeviceSerial(
            f"invalid device {device!r}: must match [A-Za-z0-9._:-]{{1,64}} "
            f"with alnum leading char (rejected to prevent path traversal)"
        )
    root = workspace_root()
    if device:
        base = root / "devices" / device / category
    else:
        base = root / category
    if ensure_dir:
        base.mkdir(parents=True, exist_ok=True)
    return base / filename


def resolve_capture_path(
    output: Path | str | None,
    default_name: str,
    *,
    default_category: str = "logs",
    device: str | None = None,
) -> Path:
    """Decide where a capture artifact lands.

    Shared by capture_uart / collect_logcat / collect_dmesg / bugreport so all
    `--output / -o` flags behave the same way (N=4 callers · L-020 abstract).

    Rules:
        - output=None        → workspace_path(default_category, default_name, device=device)
        - output is an existing dir or ends with "/"  → <dir>/<default_name>
          (directory is created if missing)
        - otherwise → treat as exact file path (parent is created)
    """
    if output is None:
        return workspace_path(default_category, default_name, device=device)

    p = Path(output).expanduser()
    looks_like_dir = p.is_dir() or str(output).endswith(("/", "\\"))
    if looks_like_dir:
        p.mkdir(parents=True, exist_ok=True)
        return p / default_name
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def session_path(session_id: str, filename: str = "", *, ensure_dir: bool = True) -> Path:
    """Path inside a session directory.

    Rejects any session_id that would escape `<workspace>/sessions/`:

    - empty / dot / `..` / containing `/` or `\\` / absolute paths
    - any character outside ``[A-Za-z0-9_-]``
    - longer than 128 characters

    These checks are at the root layer (not just the CLI) so every caller
    — `chat_cli` / `session_cli` / future Web UI / future MCP tool — gets
    the same protection without duplicating sanitization. Raises
    :class:`InvalidSessionId` (a `ValueError` subclass) on rejection.
    """
    if not _SAFE_SESSION_ID_RE.match(session_id):
        raise InvalidSessionId(
            f"invalid session_id {session_id!r}: must match [A-Za-z0-9][A-Za-z0-9_-]* "
            f"(<= 128 chars); rejected to prevent path traversal"
        )
    root = workspace_root()
    base = root / "sessions" / session_id
    if ensure_dir:
        base.mkdir(parents=True, exist_ok=True)
    # Defence-in-depth: even if regex passes, double-check resolved path is
    # under sessions/. Catches any future regex relaxation that lets a `..`
    # slip through unicode or symlink games.
    sessions_root = (root / "sessions").resolve()
    if not base.resolve().is_relative_to(sessions_root):
        raise InvalidSessionId(
            f"session_id {session_id!r} escapes sessions/ after path resolution"
        )
    return base / filename if filename else base
