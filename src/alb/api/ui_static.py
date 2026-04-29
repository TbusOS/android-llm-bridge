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

SPA fallback (DEBT-014, 2026-04-29): TanStack Router uses HTML5
history mode, so direct hits to `/app/dashboard` / `/app/inspect` /
etc. on browser refresh or shared deep-links must serve `index.html`
to let the client-side router resolve the route. `SPAStaticFiles`
extends StaticFiles to do this without rewriting real 404s for
missing assets.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.responses import FileResponse, Response
from starlette.types import Scope


class SPAStaticFiles(StaticFiles):
    """StaticFiles that serves index.html for unknown SPA route paths.

    Two mechanisms cover orthogonal paths and do NOT overlap:
      - `html=True` (the StaticFiles flag) handles the bare `/app/` →
        `index.html` mapping (directory index lookup).
      - `SPAStaticFiles.get_response` handles `/app/<route>` deep links
        like `/app/dashboard`, `/app/inspect`, `/app/sessions/abc-123`.

    Why a custom subclass and not a FastAPI catch-all route: a sibling
    catch-all `@app.get("/app/{path:path}")` would have to be ordered
    relative to `app.mount("/app", ...)`, and any ordering bug routes
    real assets (`/app/assets/index-XYZ.js`) through the HTML fallback,
    silently breaking caching and content types. Subclassing keeps the
    fallback inside the same StaticFiles handler, so asset lookups and
    SPA fallback share one path resolution pass.

    Heuristic: a missing path is treated as an SPA route iff its last
    segment has no `.`. Anything with a dot (`index-XYZ.js`,
    `favicon.ico`, `bundle.min.js.map`) propagates the 404 unchanged
    so broken asset URLs surface as real errors instead of degenerating
    into a confusing white-page render.

    **Cross-repo invariant**: TanStack Router routes (`web/src/router.tsx`)
    MUST NOT contain `.` in any path segment, or this heuristic
    misclassifies them as asset paths and 404s. See
    `.claude/knowledge/architecture.md` 关键不变量 段。

    Error propagation: only 404 from a missing path gets rewritten;
    401 (PermissionError), 405 (non-GET/HEAD), and OSError propagate
    unchanged.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except HTTPException as e:
            if e.status_code != 404:
                raise
            tail = path.rsplit("/", 1)[-1]
            if "." in tail:
                # Looks like a missing asset — let the real 404 surface.
                raise
            return FileResponse(Path(str(self.directory)) / "index.html")


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
        SPAStaticFiles(directory=str(app_dir), html=True),
        name="alb-ui",
    )
    return True
