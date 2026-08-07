"""Keep credentials out of the server logs (ADR-055).

Why this exists: uvicorn prints the full request target — path AND query
string — for every connection it serves. The agent's data-plane dial-back
used to carry the agent token and the per-channel secret as query params, so
each channel open wrote two live credentials into the hub's log file in
clear text, where they survive log rotation, `tail` in a terminal, and any
log shipped to someone for troubleshooting.

Two loggers, not one. HTTP requests go to `uvicorn.access` as
`'%s - "%s %s HTTP/%s" %d'` (the target is args[2]); **WebSocket** upgrades
go to `uvicorn.error` as `'%s - "WebSocket %s" [accepted]'` (target in
args[1]) — see uvicorn/protocols/websockets/websockets_impl.py. The
dial-back is a WebSocket, so covering only the access logger would have
missed the exact line that leaks. Hence: both loggers, and every string
argument scanned rather than one hard-coded index.

Rotating those credentials is expensive (they live in three places: the hub
env file, the agent config on the operator's machine, and the staging copy),
so the log must never capture one to begin with.

`agent_route` now reads them from headers, but this filter is not redundant:
the query form stays accepted so an agent that has not been redeployed yet
keeps working, and THOSE dial-backs are what this scrubs. It also covers any
future route that ends up with a secret in a query string.
"""

from __future__ import annotations

import logging
import re

REDACTED = "***"

# Query keys whose VALUE is a credential. Kept broad on purpose: the cost of
# redacting one extra field is a less useful log line, the cost of missing
# one is a credential rotation.
_SECRET_KEYS = (
    "token",
    "csecret",
    "secret",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "access_token",
)

# Value runs to the next separator. `"` and whitespace are terminators too so
# a pre-formatted log line ('GET /x?token=abc HTTP/1.1') redacts correctly
# instead of swallowing the rest of the line.
_QUERY_RE = re.compile(
    r"(?i)(?<![\w-])(" + "|".join(_SECRET_KEYS) + r")=([^&\s\"']*)",
)


def redact_query_secrets(text: str) -> str:
    """Replace `<secret-key>=<value>` with `<secret-key>=***` in a URL or a
    whole log line. Anything that is not a known secret key is untouched, so
    `?cid=` (a routing key, not a credential) stays readable — losing it
    would make channel troubleshooting impossible."""
    return _QUERY_RE.sub(lambda m: f"{m.group(1)}={REDACTED}", text)


# Both loggers uvicorn writes request targets to. `uvicorn.error` is not
# only for errors — it is where the WebSocket upgrade line lands, which is
# the one that carries the dial-back credentials.
LOG_SOURCES = ("uvicorn.access", "uvicorn.error")


class AccessLogRedactor(logging.Filter):
    """Rewrites any request target a uvicorn log record carries.

    Scans every string argument instead of a fixed index: the HTTP record
    puts the target at args[2] and the WebSocket record at args[1], and a
    filter that hard-codes one of them silently misses the other. Rewriting
    the ARG rather than the formatted message keeps the record structured for
    any other handler (JSON log shippers read `record.args`, not the rendered
    text). The `record.msg` branch is the fallback for runners that hand us a
    pre-formatted line instead.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple):
            cleaned = tuple(
                redact_query_secrets(a) if isinstance(a, str) and "=" in a else a for a in args
            )
            if cleaned != args:
                record.args = cleaned
        elif not args and isinstance(record.msg, str) and "=" in record.msg:
            record.msg = redact_query_secrets(record.msg)
        return True  # a filter that returns False would DROP the line


def install_access_log_redaction(logger_names: tuple[str, ...] = LOG_SOURCES) -> None:
    """Attach the redactor to uvicorn's request loggers. Idempotent.

    Called from the app lifespan rather than from `main()` on purpose:
    uvicorn applies its own `dictConfig` before importing the app, and the
    hub is also launched as `uvicorn alb.api.server:app`, which never runs
    our `main()`. Startup is the one point that is after logging setup on
    every launch path.
    """
    for name in logger_names:
        logger = logging.getLogger(name)
        if any(isinstance(f, AccessLogRedactor) for f in logger.filters):
            continue
        logger.addFilter(AccessLogRedactor())
