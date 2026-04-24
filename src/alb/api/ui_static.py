"""Mount the built Web UI (docs/app/) at /app on the FastAPI app.

The source for this bundle lives under `web/` and is built via `vite
build` into `docs/app/`. Both the source and the built output are
committed so:
  - End users who `pip install alb` never need node to use the UI
  - GitHub Pages can serve the same `docs/app/` directly
  - Offline installs ship a ready-to-serve UI in the wheel

If `docs/app/` is missing (e.g. a fresh clone before the first build),
the mount is skipped and /app returns 404 — the CLI and MCP paths are
unaffected.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles


def _repo_docs_app_dir() -> Path | None:
    """Return docs/app relative to the installed alb package, or None."""
    # When installed via pip, __file__ is inside site-packages/alb/...
    # docs/ is only in the source tree, so look upward for pyproject.toml.
    here = Path(__file__).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / "pyproject.toml").exists():
            app_dir = candidate / "docs" / "app"
            if app_dir.is_dir() and (app_dir / "index.html").is_file():
                return app_dir
            return None
    return None


def mount_ui(app: FastAPI, *, url_prefix: str = "/app") -> bool:
    """Mount the React bundle. Returns True if a bundle was found."""
    app_dir = _repo_docs_app_dir()
    if app_dir is None:
        return False
    app.mount(
        url_prefix,
        StaticFiles(directory=str(app_dir), html=True),
        name="alb-ui",
    )
    return True
