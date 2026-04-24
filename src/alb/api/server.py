"""FastAPI server entry point (`alb-api` command).

M2 beta — ships `GET /health` and `POST /chat` (non-streaming).

Streaming (`GET /chat/ws`), OpenAPI fine-tuning, and auth come in M3.

Usage:
    alb-api                       # defaults: 0.0.0.0:8765
    alb-api --port 9000           # (via env ALB_API_PORT)
    uvicorn alb.api.server:app    # bring your own ASGI runner
"""

from __future__ import annotations

import os
import sys

from fastapi import FastAPI

from alb import __version__
from alb.api.chat_route import router as chat_router
from alb.api.metrics_route import router as metrics_router
from alb.api.playground_route import router as playground_router
from alb.api.meta_route import router as meta_router
from alb.api.terminal_route import router as terminal_router
from alb.infra.env_loader import load_env_files

# Load .env.local / .env so ALB_* values reach FastAPI request handlers.
load_env_files()


def create_app() -> FastAPI:
    """Build the FastAPI app (kept as a factory so tests can get a fresh instance)."""
    app = FastAPI(
        title="android-llm-bridge API",
        version=__version__,
        description=(
            "Local HTTP API for alb: health, chat agent, capabilities. "
            "Pairs with the MCP server (`alb-mcp`) and CLI (`alb`) — same "
            "underlying capability layer."
        ),
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"ok": "true", "version": __version__, "api": "alb"}

    app.include_router(chat_router)
    app.include_router(metrics_router)
    app.include_router(playground_router)
    app.include_router(terminal_router)
    app.include_router(meta_router)

    @app.on_event("shutdown")
    async def _stop_streamers() -> None:  # noqa: ANN001 — FastAPI hook
        from alb.capabilities.metrics import shutdown_all_streamers
        await shutdown_all_streamers()

    return app


# Module-level app for `uvicorn alb.api.server:app`
app = create_app()


def main() -> None:
    """Entry point referenced by pyproject.toml `[project.scripts]`."""
    try:
        import uvicorn
    except ImportError:
        print(
            "[alb-api] uvicorn not installed. Run: uv sync  (or: pip install uvicorn[standard])",
            file=sys.stderr,
        )
        sys.exit(1)

    host = os.environ.get("ALB_API_HOST", "0.0.0.0")
    port = int(os.environ.get("ALB_API_PORT", "8765"))
    uvicorn.run("alb.api.server:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
