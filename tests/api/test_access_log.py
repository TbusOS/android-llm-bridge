"""Access-log credential redaction (ADR-055).

The regression these lock down: a dial-back whose query string carries the
agent token / per-channel secret must not reach the log file readable.
"""

from __future__ import annotations

import logging

from alb.api.access_log import (
    AccessLogRedactor,
    install_access_log_redaction,
    redact_query_secrets,
)

_ACCESS_MSG = '%s - "%s %s HTTP/%s" %d'
# uvicorn/protocols/websockets/websockets_impl.py — WS upgrades log here,
# on uvicorn.error, with the target at args[1] instead of args[2].
_WS_MSG = '%s - "WebSocket %s" [accepted]'


def _record(path: str, *, name: str = "uvicorn.access") -> logging.LogRecord:
    return logging.LogRecord(
        name=name,
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=_ACCESS_MSG,
        args=("10.1.1.1:5555", "GET", path, "1.1", 200),
        exc_info=None,
    )


def _ws_record(path: str) -> logging.LogRecord:
    return logging.LogRecord(
        name="uvicorn.error",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=_WS_MSG,
        args=("10.1.1.1:5555", path),
        exc_info=None,
    )


def test_redacts_token_and_csecret():
    out = redact_query_secrets("/agent/channel?cid=abc&token=sekret&csecret=other")
    assert "sekret" not in out
    assert "other" not in out
    assert out == "/agent/channel?cid=abc&token=***&csecret=***"


def test_keeps_cid_readable():
    """cid is a routing key, not a credential — redacting it would make a
    failing channel impossible to trace through the log."""
    assert "cid=abc123" in redact_query_secrets("/agent/channel?cid=abc123&token=x")


def test_value_stops_at_separators():
    """A pre-formatted line must not have its tail swallowed by the match."""
    line = 'GET /agent/channel?token=abc HTTP/1.1" 200'
    assert redact_query_secrets(line) == 'GET /agent/channel?token=*** HTTP/1.1" 200'


def test_does_not_touch_lookalike_keys():
    """`csrf_token` / `retoken` are different fields; only exact keys match."""
    assert redact_query_secrets("/x?mytoken=keepme") == "/x?mytoken=keepme"


def test_empty_value_still_redacted():
    assert redact_query_secrets("/x?token=") == "/x?token=***"


def test_filter_rewrites_args_not_message():
    rec = _record("/agent/channel?cid=abc&csecret=leaky")
    assert AccessLogRedactor().filter(rec) is True  # never drops the line
    assert rec.msg == _ACCESS_MSG  # structure preserved for JSON shippers
    assert rec.args is not None
    assert rec.args[2] == "/agent/channel?cid=abc&csecret=***"
    assert "leaky" not in rec.getMessage()


def test_filter_leaves_clean_paths_alone():
    rec = _record("/health")
    before = rec.args
    AccessLogRedactor().filter(rec)
    assert rec.args is before  # no needless tuple churn


def test_filter_covers_websocket_record():
    """The regression that unit tests alone missed: the dial-back is a
    WebSocket, and uvicorn logs those on uvicorn.error with the target at
    args[1]. A filter keyed to args[2] leaves the leaking line untouched."""
    rec = _ws_record("/agent/channel?cid=abc&token=leaky&csecret=alsoleaky")
    AccessLogRedactor().filter(rec)
    assert "leaky" not in rec.getMessage()
    assert "alsoleaky" not in rec.getMessage()
    assert rec.getMessage() == (
        '10.1.1.1:5555 - "WebSocket /agent/channel?cid=abc&token=***&csecret=***" [accepted]'
    )


def test_default_sources_include_the_websocket_logger():
    from alb.api.access_log import LOG_SOURCES

    assert "uvicorn.error" in LOG_SOURCES
    assert "uvicorn.access" in LOG_SOURCES


def test_install_is_idempotent():
    logger = logging.getLogger("test.access.idempotent")
    try:
        install_access_log_redaction(("test.access.idempotent",))
        install_access_log_redaction(("test.access.idempotent",))
        assert sum(isinstance(f, AccessLogRedactor) for f in logger.filters) == 1
    finally:
        logger.filters = [f for f in logger.filters if not isinstance(f, AccessLogRedactor)]


def test_lifespan_installs_on_both_uvicorn_loggers():
    """The app must install it on startup — main() is not on every launch
    path (`uvicorn alb.api.server:app` skips it entirely)."""
    from fastapi.testclient import TestClient

    from alb.api.access_log import LOG_SOURCES
    from alb.api.server import create_app

    loggers = [logging.getLogger(n) for n in LOG_SOURCES]
    for lg in loggers:
        lg.filters = [f for f in lg.filters if not isinstance(f, AccessLogRedactor)]
    try:
        with TestClient(create_app()):
            for lg in loggers:
                assert any(isinstance(f, AccessLogRedactor) for f in lg.filters), lg.name
    finally:
        for lg in loggers:
            lg.filters = [f for f in lg.filters if not isinstance(f, AccessLogRedactor)]
