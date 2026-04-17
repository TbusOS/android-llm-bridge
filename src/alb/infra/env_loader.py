"""Load `.env.local` / `.env` files at CLI/API startup.

alb's deployment-specific settings (Ollama URL, default model, API port,
SSH host, …) are all `ALB_*` environment variables read via typer's
`envvar=` parameter or direct `os.environ.get`.

In addition to shell `export` and Claude Code / Cursor MCP config, we
support a `.env.local` file at the project root — the canonical pattern
borrowed from Django / Next.js / Supabase / most modern dev stacks:

    .env.example   # committed — template with safe localhost defaults
    .env.local     # gitignored — your real deployment values
    .env           # committed — shared defaults (optional)

Priority (highest first):
    1. Existing shell environment (`export FOO=bar` in ~/.bashrc etc.)
    2. `.env.local` at project root
    3. `.env` at project root
    4. Library / CLI flag defaults

This module never overwrites an environment variable that is already
present — the shell always wins. This keeps `export X=...` / `X=... alb …`
predictable.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_env_files(roots: list[Path] | None = None) -> list[Path]:
    """Load `.env.local` then `.env` from the given roots into `os.environ`.

    Args:
        roots: candidate directories. Default: current working directory and,
            if different, the project root inferred from this file's location
            (`<repo>/src/alb/infra/env_loader.py` → `<repo>`).

    Returns:
        List of files that were actually loaded (for logging/debugging).

    Never overwrites variables already present in `os.environ`.
    """
    if roots is None:
        roots = list(_default_roots())

    loaded: list[Path] = []
    # .env.local has higher priority than .env; load .local first so .env
    # won't overwrite anything .local set (both use "don't override shell"
    # policy, but order still matters for vars unique to .env).
    for name in (".env.local", ".env"):
        for root in roots:
            candidate = root / name
            if candidate.is_file():
                _load_one(candidate)
                loaded.append(candidate)
                break  # one file per name — first root wins
    return loaded


def _default_roots() -> list[Path]:
    """CWD + inferred repo root. Dedup while preserving order."""
    seen: set[Path] = set()
    out: list[Path] = []
    for r in (Path.cwd(), _repo_root()):
        r = r.resolve()
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def _repo_root() -> Path:
    """Infer repo root from this file: .../<repo>/src/alb/infra/env_loader.py"""
    return Path(__file__).resolve().parents[3]


def _load_one(path: Path) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        # Strip optional `export ` prefix (dotenv convention)
        if s.startswith("export "):
            s = s[len("export ") :].lstrip()
        if "=" not in s:
            continue
        k, _, v = s.partition("=")
        k = k.strip()
        v = v.strip()
        # Strip surrounding quotes
        if len(v) >= 2 and (
            (v[0] == v[-1] == '"') or (v[0] == v[-1] == "'")
        ):
            v = v[1:-1]
        if not k:
            continue
        if k in os.environ:
            continue  # shell wins
        os.environ[k] = v
