"""alb's own logger actually emits under uvicorn (ADR-057).

The defect these guard: a log line that is written but discarded before
formatting looks identical to a log line that was never added. The channel-open
logging in ADR-057 shipped in exactly that state for one run.
"""

from __future__ import annotations

import logging

import pytest

from alb.api.log_setup import _ROOT_LOGGER, install_alb_logging


@pytest.fixture(autouse=True)
def _pristine_alb_logger():
    """Restore the logger afterwards — it is process-global, and a test that
    leaves it configured makes the next one pass for the wrong reason."""
    logger = logging.getLogger(_ROOT_LOGGER)
    saved = (logger.level, list(logger.handlers), logger.propagate)
    for attr in ("_alb_logging_installed",):
        if hasattr(logger, attr):
            delattr(logger, attr)
    logger.handlers = []
    logger.setLevel(logging.NOTSET)
    logger.propagate = True
    yield logger
    logger.level, logger.handlers, logger.propagate = saved[0], saved[1], saved[2]
    if hasattr(logger, "_alb_logging_installed"):
        delattr(logger, "_alb_logging_installed")


def test_info_is_dropped_before_install(_pristine_alb_logger, monkeypatch):
    """The pre-ADR-057 state, asserted so the fix cannot be quietly undone:
    stdlib defaults leave alb.* at WARNING through root."""
    monkeypatch.setattr(logging.getLogger(), "level", logging.WARNING)
    assert not logging.getLogger("alb.remote.forwarder").isEnabledFor(logging.INFO)


def test_info_survives_after_install(_pristine_alb_logger):
    install_alb_logging()
    assert logging.getLogger("alb.remote.forwarder").isEnabledFor(logging.INFO)


def test_a_record_reaches_a_handler(_pristine_alb_logger):
    """isEnabledFor() is necessary but not sufficient — a level with no handler
    still produces nothing. Assert on emitted records, not on configuration."""
    install_alb_logging()
    emitted: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            emitted.append(record)

    _pristine_alb_logger.addHandler(_Capture())
    logging.getLogger("alb.remote.forwarder").info("tcp channel opened -> %s", "127.0.0.1:5037")
    assert [r.getMessage() for r in emitted] == ["tcp channel opened -> 127.0.0.1:5037"]


def test_is_idempotent(_pristine_alb_logger):
    install_alb_logging()
    n = len(_pristine_alb_logger.handlers)
    install_alb_logging()
    install_alb_logging()
    assert len(_pristine_alb_logger.handlers) == n, "repeat installs would duplicate every line"


def test_reuses_uvicorns_handler(_pristine_alb_logger):
    """Same destination and formatting as the request log the operator is
    already reading."""
    uv = logging.getLogger("uvicorn")
    saved = list(uv.handlers)
    marker = logging.StreamHandler()
    uv.handlers = [marker]
    try:
        install_alb_logging()
        assert marker in _pristine_alb_logger.handlers
    finally:
        uv.handlers = saved


def test_level_override_from_env(_pristine_alb_logger, monkeypatch):
    monkeypatch.setenv("ALB_LOG_LEVEL", "WARNING")
    install_alb_logging()
    assert not logging.getLogger("alb.remote.forwarder").isEnabledFor(logging.INFO)


def test_unknown_level_name_falls_back_to_info(_pristine_alb_logger, monkeypatch):
    """A typo must not silence the hub — that is the failure being fixed."""
    monkeypatch.setenv("ALB_LOG_LEVEL", "verbose-please")
    install_alb_logging()
    assert logging.getLogger("alb.remote.forwarder").isEnabledFor(logging.INFO)
