"""Safe filesystem path resolution helpers (DEBT-032 / L-020 抽 base).

Architecture-reviewer 2026-05-08 found `_safe_resolve_screenshot`
(screenshots_route.py) and `_safe_resolve_capture` (uart_route.py) at
N=3 with `_resolve_workspace_path` (files_route.py) all using the same
``base.resolve() + relative_to() + symlink reject`` pattern, with each
docstring pointing at the others. L-020 N=3 抽 base 阈值触发 → public
``resolve_under()`` helper here is the single source of truth for the
"caller-supplied name slot inside a known dir" case.

Note ``files_route._resolve_workspace_path`` stays in place — it
accepts multi-segment relative paths (e.g. ``devices/<serial>/pulls/x``)
so its name-validation gate is different (allows ``/``, rejects ``..``).
This module's ``resolve_under`` is for the **single-filename slot**
case: caller has a fixed parent dir + an opaque user-supplied name
that should land *immediately under* the parent, not in a subtree.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from fastapi import HTTPException


def resolve_under(
    base: Path,
    name: str,
    *,
    is_valid_name: Callable[[str], bool] | None = None,
    not_found_detail: str = "not found",
    invalid_name_detail: str = "invalid name",
    escape_detail: str = "path escapes base dir",
    symlink_detail: str = "symlinks not allowed",
) -> Path:
    """Return a Path safely resolved as ``base / name``, raising
    ``HTTPException`` on any policy violation.

    Three-layer defence (mirrors the previous per-route helpers):

    1. **Name gate** — caller's optional ``is_valid_name`` predicate
       runs first. Reject ``..`` / ``/`` / ``\\\\`` / wrong suffix etc.
       Default (None) accepts any non-traversal name.
    2. **Symlink reject** — entries that *are* symlinks are refused
       outright (defence in depth: even if the link target is inside
       the base, the symlink itself is something the operator did not
       create via the official capture/screenshot flow, so treating it
       as data is a leakage risk).
    3. **Resolve + ``relative_to(base)``** — strict-mode resolve, then
       assert the resolved path stays under the resolved base. Catches
       symlinks-into-base (rare) and any race where the symlink check
       passed but ``Path.resolve`` reveals an out-of-tree target.

    Args:
        base: parent directory the file must live in.
        name: caller-supplied filename. Must NOT contain path separators
            (the predicate or default check rejects those).
        is_valid_name: optional predicate; returns False to trigger 400.
        not_found_detail/invalid_name_detail/escape_detail/symlink_detail:
            HTTP 4xx detail strings, customisable per call site so log
            messages match the legacy per-route wording.

    Returns:
        ``base / name`` resolved to an absolute Path that is provably
        a regular file inside ``base``.

    Raises:
        HTTPException(400): name fails predicate / has separators / is
            a symlink / resolves outside base.
        HTTPException(404): base or target does not exist; target is
            not a regular file.
    """
    # Default name gate — refuse any path-traversal characters even
    # when caller didn't pass a predicate.
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(status_code=400, detail=invalid_name_detail)
    if is_valid_name is not None and not is_valid_name(name):
        raise HTTPException(status_code=400, detail=invalid_name_detail)

    candidate = base / name
    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(status_code=404, detail=not_found_detail)
    if candidate.is_symlink():
        raise HTTPException(status_code=400, detail=symlink_detail)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(base.resolve())
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=escape_detail) from exc
    return resolved
