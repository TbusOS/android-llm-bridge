"""Make alb's own log lines reach the console under uvicorn (ADR-057).

## Why this is needed at all

uvicorn applies its own ``dictConfig`` before importing the app. That config
attaches a handler to the ``uvicorn`` logger tree and sets its level — and
touches nothing else. The root logger keeps stdlib defaults: level WARNING, no
handler.

Every module in alb does ``logging.getLogger(__name__)``, so ``alb.*`` loggers
sit at NOTSET and inherit root's WARNING. The consequence is narrow and easy to
miss: ``_log.warning`` from alb code appears (via the last-resort handler),
``_log.info`` **never does, anywhere, on any launch path**. The hub's log was
therefore made of uvicorn request lines and alb warnings only.

That is how ADR-057's channel-open logging would have shipped as a no-op: the
line is written, the record is discarded before formatting, and the log looks
exactly as silent as before the fix. It was caught by checking the log after
the change instead of assuming — the same class of defect the fix was for.

## Why the lifespan is the call site

Same reasoning as ADR-055's redactor: uvicorn configures logging *before*
importing the app, and the hub is also launched as ``uvicorn alb.api.server:app``
which never runs our ``main()``. Application startup is the one hook that is
after logging setup on every launch path.

Scope is deliberately the server only. The CLI has its own output discipline
(rich tables, structured results); turning on INFO logging there would
interleave library chatter with command output for no gain.
"""

from __future__ import annotations

import logging
import os

__all__ = ["install_alb_logging"]

_ROOT_LOGGER = "alb"
_MARKER = "_alb_logging_installed"


def _configured_level() -> int:
    """``ALB_LOG_LEVEL`` (name or number), defaulting to INFO.

    INFO rather than WARNING because the events alb logs at INFO are
    operator-meaningful and rare — a channel opening, an agent connecting. The
    volume argument for defaulting to WARNING applies to libraries that narrate
    every request; it does not apply here, and the cost of the quiet default
    was a day of debugging a working tunnel.
    """
    raw = os.environ.get("ALB_LOG_LEVEL", "").strip()
    if not raw:
        return logging.INFO
    if raw.isdigit():
        return int(raw)
    return getattr(logging, raw.upper(), logging.INFO)


def install_alb_logging(level: int | None = None) -> None:
    """Give the ``alb`` logger a level and a handler. Idempotent.

    Reuses uvicorn's handler when one exists so alb's lines land in the same
    place, with the same formatting, as the request log an operator is already
    reading — a second stream with a different shape reads as a different
    program's output.
    """
    logger = logging.getLogger(_ROOT_LOGGER)
    if getattr(logger, _MARKER, False):
        return

    logger.setLevel(level if level is not None else _configured_level())

    if not logger.handlers:
        uvicorn_logger = logging.getLogger("uvicorn")
        handler = (
            uvicorn_logger.handlers[0] if uvicorn_logger.handlers else logging.StreamHandler()
        )
        logger.addHandler(handler)
        # We own a handler now, so propagation would print twice anywhere root
        # also has one (pytest's caplog, a CLI that called basicConfig).
        logger.propagate = False

    setattr(logger, _MARKER, True)
