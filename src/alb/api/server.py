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
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from alb import __version__
from alb.api.app_route import router as app_route_router
from alb.api.audit_route import router as audit_router
from alb.api.chat_route import router as chat_router
from alb.api.devices_route import router as devices_router
from alb.api.diag_route import router as diag_router
from alb.api.doctor_route import router as doctor_router
from alb.api.files_route import router as files_router
from alb.api.info_route import router as info_router
from alb.api.log_search_route import router as log_search_router
from alb.api.metrics_route import router as metrics_router
from alb.api.metrics_summary_route import router as metrics_summary_router
from alb.api.playground_route import router as playground_router
from alb.api.meta_route import router as meta_router
from alb.api.power_route import router as power_router
from alb.api.sessions_route import router as sessions_router
from alb.api.terminal_route import router as terminal_router
from alb.api.tools_route import router as tools_router
from alb.api.logcat_stream_route import router as logcat_stream_router
from alb.api.screenshots_route import router as screenshots_router
from alb.api.uart_route import router as uart_router
from alb.api.uart_stream_route import router as uart_stream_router
from alb.api.ui_static import mount_ui
from alb.infra.env_loader import load_env_files

# Load .env.local / .env so ALB_* values reach FastAPI request handlers.
load_env_files()


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """App startup / shutdown (ADR-049 migrates the old @app.on_event
    shutdown hooks here — single ordered teardown, no deprecation).

    Startup: attach the MetricStore read model to the event bus so it
    projects every `tps_sample` for the dashboard's O(window) metric
    queries. attach() is idempotent, so a per-test create_app() that
    re-enters the lifespan against a shared bus can't double-register.
    """
    from alb.infra.event_bus import get_bus
    from alb.infra.metric_store import get_metric_store

    get_metric_store().attach(get_bus())
    try:
        yield
    finally:
        from alb.capabilities.metrics import shutdown_all_streamers
        await shutdown_all_streamers()
        # DEBT-019: backends keep a long-lived httpx.AsyncClient open for
        # connection reuse; close on shutdown so we don't leave half-open
        # TCP sockets in tests / dev reload.
        from alb.agent.backends import close_probe_cache
        await close_probe_cache()
        get_metric_store().detach()


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
        lifespan=_lifespan,
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"ok": "true", "version": __version__, "api": "alb"}

    app.include_router(app_route_router)
    app.include_router(audit_router)
    app.include_router(chat_router)
    app.include_router(devices_router)
    app.include_router(diag_router)
    app.include_router(doctor_router)
    app.include_router(files_router)
    app.include_router(info_router)
    app.include_router(log_search_router)
    app.include_router(metrics_router)
    app.include_router(metrics_summary_router)
    app.include_router(playground_router)
    app.include_router(power_router)
    app.include_router(screenshots_router)
    app.include_router(sessions_router)
    app.include_router(terminal_router)
    app.include_router(tools_router)
    app.include_router(uart_router)
    app.include_router(uart_stream_router)
    app.include_router(logcat_stream_router)
    app.include_router(meta_router)

    # Best-effort mount of the React UI at /app. Missing bundle =
    # fresh clone before `cd web && npm run build`; silently skip so
    # the API keeps working.
    mount_ui(app)

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
    if host == "0.0.0.0":
        # Security audit 2026-05-02 finding MID 2: alb-api binds all
        # interfaces by default + has no auth. Anyone with network
        # reachability can call POST /devices/{s}/files/push, write
        # to UART, etc. Default kept (loud-deprecating later via
        # ADR-034 seed) but a startup banner now flags the risk.
        print(
            "[alb-api] WARNING: bound 0.0.0.0 with no auth — file push,"
            " adb shell, UART write endpoints reachable from the LAN."
            " For local-only dev set: ALB_API_HOST=127.0.0.1",
            file=sys.stderr,
        )
    uvicorn.run("alb.api.server:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
