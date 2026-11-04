"""Let more than one task wait for a full send buffer on a hub WebSocket (ADR-065).

What breaks without it: uvicorn serves WebSockets with the *legacy* protocol
of the ``websockets`` package whenever that package is installed
(``uvicorn/protocols/websockets/auto.py``). Its flow-control helper
``WebSocketCommonProtocol._drain_helper`` keeps ONE future for "the send
buffer has room again" and asserts that no other task is already waiting on
it. Two waiters are normal on the hub: while it pushes an image to the bench
agent (64 KiB frames, send buffer full most of the time) the agent's
keepalive ping arrives, the reader task answers with a pong, and writing the
pong also has to wait for the buffer. The second wait trips
``assert waiter is None or waiter.cancelled()``, the reader task dies
("data transfer failed" in the hub log) and the connection closes with 1011.
Seen once on the bench on 2026-11-03, at 80 % of a 2.2 GB super.img.

The helper says "Copied from asyncio.FlowControlMixin" — from an asyncio
that still had one waiter. asyncio itself keeps a deque of waiters now
(``asyncio/streams.py`` on Python 3.11). The replacement here gets the same
behaviour by changing only ``_drain_helper``: every waiter awaits the one
shared future, behind ``asyncio.shield`` so that cancelling one waiter does
not cancel the future the others are waiting on. ``resume_writing`` and
``connection_lost`` are left as they are: they already resolve (or fail)
that one future, which now wakes every waiter instead of one.

Why patch rather than configure around it:

* Turning keepalive pings off removes the second writer, but also the only
  way the hub notices a bench agent that vanished without closing — for
  every WebSocket, not just image pushes.
* uvicorn's other implementation (``ws="websockets-sansio"``) dropped the
  same transfer with "no close frame" when pings were on.

Patching another package's private method is only safe while that method
is the code we read. So the patch goes in only when the source of the three
methods it relies on matches a version checked by hand
(``_KNOWN_SOURCES``). Any other version — a new ``websockets`` that fixed
the helper, renamed the fields, or dropped the legacy module — gets no patch
and one WARNING line at startup. That failure mode is the old behaviour
(an occasional 1011 on long pushes), never a broken WebSocket.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
import warnings
from typing import Any

log = logging.getLogger(__name__)

# The methods whose behaviour the replacement depends on, hashed together.
_CHECKED_METHODS = ("_drain_helper", "resume_writing", "connection_lost")

# sha256 of the concatenated inspect.getsource() of _CHECKED_METHODS.
# Every entry below was read side by side with the 16.0 source: the
# differences are a log guard and a type annotation in connection_lost; the
# waiter handling is identical.
_KNOWN_SOURCES = {
    "4331aac9e829e95890702252a592fafd88f0af6356aadee9d46532d38b5012df": "12.0",
    "126d74cb94b7d069d79748bb8f15f39d539448413172ecaceec6c7ac6e38bcfc": "13.1-15.0.1",
    "5a0106f99cf50beda4e910dbb4bc30ef3da31dee1a8f62e554aa2ac677170295": "16.0",
}

# Marks our replacement so a second install is a no-op (create_app() runs
# once per test; the lifespan installs on every start).
_MARK = "_alb_shared_drain_waiter"

INSTALLED = "installed"
ALREADY_INSTALLED = "already-installed"
NOT_APPLICABLE = "not-applicable"
SKIPPED = "skipped"

# The method each patched class had before, so uninstall can put it back.
_originals: dict[type, Any] = {}


async def _shared_drain_helper(self: Any) -> None:
    """Drop-in for the legacy ``_drain_helper`` that admits many waiters."""
    if self.connection_lost_waiter.done():
        raise ConnectionResetError("Connection lost")
    if not self._paused:
        return
    waiter = self._drain_waiter
    if waiter is None or waiter.done():
        waiter = self.loop.create_future()
        self._drain_waiter = waiter
    # shield: a waiter that is cancelled (e.g. its sender gave up) must not
    # cancel the future every other waiter is parked on.
    await asyncio.shield(waiter)


setattr(_shared_drain_helper, _MARK, True)


def source_fingerprint(cls: type) -> str:
    """sha256 over the source of the methods the replacement relies on."""
    digest = hashlib.sha256()
    for name in _CHECKED_METHODS:
        digest.update(inspect.getsource(getattr(cls, name)).encode())
    return digest.hexdigest()


def _legacy_protocol_class() -> type | None:
    try:
        with warnings.catch_warnings():
            # websockets >= 14 warns on importing the legacy package. uvicorn
            # has already imported it by the time the hub starts; tests may not.
            warnings.simplefilter("ignore", DeprecationWarning)
            from websockets.legacy.protocol import WebSocketCommonProtocol
    except ImportError:
        return None
    return WebSocketCommonProtocol


def install_ws_drain_fix(cls: type | None = None) -> str:
    """Replace the legacy ``_drain_helper`` when its source is a known one.

    Returns one of INSTALLED / ALREADY_INSTALLED / NOT_APPLICABLE / SKIPPED.
    ``cls`` is for tests; the hub passes nothing and patches websockets'
    ``WebSocketCommonProtocol``.
    """
    if cls is None:
        cls = _legacy_protocol_class()
        if cls is None:
            log.info("ws drain fix: websockets legacy protocol not importable, nothing to patch")
            return NOT_APPLICABLE
    current = cls.__dict__.get("_drain_helper")
    if getattr(current, _MARK, False):
        return ALREADY_INSTALLED
    try:
        fingerprint = source_fingerprint(cls)
    except (AttributeError, OSError, TypeError) as exc:
        log.warning(
            "ws drain fix NOT installed: cannot read %s source (%s); long pushes"
            " may still close with 1011 (ADR-065)",
            cls.__qualname__,
            exc,
        )
        return SKIPPED
    known = _KNOWN_SOURCES.get(fingerprint)
    if known is None:
        log.warning(
            "ws drain fix NOT installed: %s source fingerprint %s is not one we"
            " checked; long pushes may still close with 1011 (ADR-065)",
            cls.__qualname__,
            fingerprint[:12],
        )
        return SKIPPED
    _originals[cls] = current
    cls._drain_helper = _shared_drain_helper  # type: ignore[attr-defined]
    log.info("ws drain fix installed (websockets legacy source as in %s)", known)
    return INSTALLED


def uninstall_ws_drain_fix(cls: type | None = None) -> bool:
    """Put the original ``_drain_helper`` back. True if something was undone.

    The hub never calls this; tests use it to compare against the original.
    """
    if cls is None:
        cls = _legacy_protocol_class()
        if cls is None:
            return False
    original = _originals.pop(cls, None)
    if original is None:
        return False
    cls._drain_helper = original  # type: ignore[attr-defined]
    return True
