"""Workspace path helpers.

Convention: all artifacts land under `workspace/devices/<serial>/<category>/`.
See docs/architecture.md §四 for the full scheme.

M0 skeleton; full implementation in M1.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path


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
    """
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
    """Path inside a session directory."""
    root = workspace_root()
    base = root / "sessions" / session_id
    if ensure_dir:
        base.mkdir(parents=True, exist_ok=True)
    return base / filename if filename else base
