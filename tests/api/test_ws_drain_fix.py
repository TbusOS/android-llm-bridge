"""Many waiters on a full WebSocket send buffer (ADR-065).

The bench failure this locks down: the hub pushes an image while the agent's
keepalive ping arrives; the pong write and the image write both wait for the
send buffer, websockets' legacy helper asserts there is only one waiter, and
the connection closes with 1011.

`test_original_helper_asserts_on_second_waiter` runs the unpatched helper and
must keep failing the way the bench did — it is what proves the other tests
build the real condition rather than one that never needed the fix.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import warnings
from collections.abc import Iterator
from typing import Any

import pytest

from alb.api import ws_drain_fix
from alb.api.ws_drain_fix import (
    ALREADY_INSTALLED,
    INSTALLED,
    SKIPPED,
    install_ws_drain_fix,
    source_fingerprint,
    uninstall_ws_drain_fix,
)

with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    from websockets.legacy.protocol import WebSocketCommonProtocol


@pytest.fixture
def legacy_cls() -> Iterator[type[WebSocketCommonProtocol]]:
    """The real legacy class, unpatched; previous state restored afterwards.

    Any create_app() lifespan earlier in the session has already patched it
    process-wide, so start from the original explicitly."""
    was_patched = uninstall_ws_drain_fix(WebSocketCommonProtocol)
    yield WebSocketCommonProtocol
    uninstall_ws_drain_fix(WebSocketCommonProtocol)
    if was_patched:
        install_ws_drain_fix(WebSocketCommonProtocol)


async def _two_waiters(
    proto: WebSocketCommonProtocol,
) -> tuple[asyncio.Task[None], asyncio.Task[None]]:
    """Park two tasks on the paused send buffer, the way image push + pong do."""
    proto.pause_writing()
    first = asyncio.create_task(proto._drain_helper())
    await asyncio.sleep(0)
    second = asyncio.create_task(proto._drain_helper())
    await asyncio.sleep(0)
    return first, second


async def test_original_helper_asserts_on_second_waiter(
    legacy_cls: type[WebSocketCommonProtocol],
) -> None:
    proto = legacy_cls()
    first, second = await _two_waiters(proto)
    with pytest.raises(AssertionError):
        await second
    proto.resume_writing()
    await first


def test_installed_websockets_source_is_one_we_checked(
    legacy_cls: type[WebSocketCommonProtocol],
) -> None:
    """Fails on a websockets upgrade whose helper we have not read. Read the
    three methods, compare with 16.0, and add the fingerprint only if the
    waiter handling is still the same — otherwise the hub runs unpatched."""
    fingerprint = source_fingerprint(legacy_cls)
    assert fingerprint in ws_drain_fix._KNOWN_SOURCES, fingerprint


async def test_patched_releases_both_waiters(legacy_cls: type[WebSocketCommonProtocol]) -> None:
    assert install_ws_drain_fix(legacy_cls) == INSTALLED
    proto = legacy_cls()
    first, second = await _two_waiters(proto)
    assert not first.done() and not second.done()
    proto.resume_writing()
    await asyncio.wait_for(asyncio.gather(first, second), 1)


async def test_patched_cancelling_one_waiter_keeps_the_other(
    legacy_cls: type[WebSocketCommonProtocol],
) -> None:
    """Without shield, cancelling the first waiter cancels the shared future
    and the second one gets a CancelledError it never asked for."""
    install_ws_drain_fix(legacy_cls)
    proto = legacy_cls()
    first, second = await _two_waiters(proto)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert not second.done()
    proto.resume_writing()
    await asyncio.wait_for(second, 1)


async def test_patched_connection_lost_fails_every_waiter(
    legacy_cls: type[WebSocketCommonProtocol],
) -> None:
    install_ws_drain_fix(legacy_cls)
    proto = legacy_cls()
    first, second = await _two_waiters(proto)
    proto.connection_lost(ConnectionResetError("gone"))
    for task in (first, second):
        with pytest.raises(ConnectionResetError):
            await asyncio.wait_for(task, 1)


async def test_patched_not_paused_returns_at_once(
    legacy_cls: type[WebSocketCommonProtocol],
) -> None:
    install_ws_drain_fix(legacy_cls)
    proto = legacy_cls()
    await asyncio.wait_for(proto._drain_helper(), 1)


def test_install_twice_is_a_no_op(legacy_cls: type[WebSocketCommonProtocol]) -> None:
    assert install_ws_drain_fix(legacy_cls) == INSTALLED
    patched = legacy_cls.__dict__["_drain_helper"]
    assert install_ws_drain_fix(legacy_cls) == ALREADY_INSTALLED
    assert legacy_cls.__dict__["_drain_helper"] is patched


def test_unknown_source_is_left_alone(caplog: pytest.LogCaptureFixture) -> None:
    class Other:
        async def _drain_helper(self) -> None:  # not the code we read
            return None

        def resume_writing(self) -> None:
            return None

        def connection_lost(self, exc: Exception | None) -> None:
            return None

    original = Other.__dict__["_drain_helper"]
    with caplog.at_level(logging.WARNING, logger="alb.api.ws_drain_fix"):
        assert install_ws_drain_fix(Other) == SKIPPED
    assert Other.__dict__["_drain_helper"] is original
    assert "NOT installed" in caplog.text


def test_hub_startup_installs_it(legacy_cls: type[WebSocketCommonProtocol]) -> None:
    from fastapi.testclient import TestClient

    from alb.api.server import create_app

    assert not getattr(legacy_cls.__dict__["_drain_helper"], ws_drain_fix._MARK, False)
    with TestClient(create_app()):
        assert getattr(legacy_cls.__dict__["_drain_helper"], ws_drain_fix._MARK, False)


@pytest.mark.slow
async def test_long_push_with_pings_survives(
    legacy_cls: type[WebSocketCommonProtocol],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bench shape end to end: real uvicorn with the legacy implementation,
    64 KiB frames read through to_thread, small socket buffers so the sender
    waits on the buffer, 20 ms pings from both ends.

    It also counts how often a waiter arrived while another was parked: that
    is the moment the unpatched helper asserts, so a zero count would mean
    this test never built the condition and passing proves nothing."""
    import uvicorn
    from websockets.asyncio.client import connect

    assert install_ws_drain_fix(legacy_cls) == INSTALLED
    shared = legacy_cls.__dict__["_drain_helper"]
    overlaps = 0

    async def counting(self: Any) -> None:
        nonlocal overlaps
        if self._paused and self._drain_waiter is not None and not self._drain_waiter.done():
            overlaps += 1
        await shared(self)

    monkeypatch.setattr(legacy_cls, "_drain_helper", counting)

    chunk, total = 64 * 1024, 96 << 20

    async def app(scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "websocket":
            return
        await receive()
        await send({"type": "websocket.accept"})
        for _ in range(total // chunk):
            data = await asyncio.to_thread(bytes, chunk)
            await send({"type": "websocket.send", "bytes": data})
        await send({"type": "websocket.close", "code": 1000})

    errors: list[str] = []

    class Catch(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if record.levelno >= logging.ERROR:
                errors.append(record.getMessage())

    catcher = Catch()
    logging.getLogger("uvicorn.error").addHandler(catcher)
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 32 * 1024)
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    srv.setblocking(False)
    port = srv.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            ws="websockets",
            log_level="error",
            lifespan="off",
            ws_per_message_deflate=False,  # as the hub runs (ADR-062)
            ws_ping_interval=0.02,
            ws_ping_timeout=20,
        )
    )
    serving = asyncio.create_task(server.serve(sockets=[srv]))
    got = 0
    try:
        # A helper that loses a waiter deadlocks the push instead of raising;
        # the deadline turns that into a failure (the whole push takes ~3 s).
        async with asyncio.timeout(30):
            # uvicorn exposes startup only as a flag, no event to await.
            while not server.started:  # noqa: ASYNC110
                await asyncio.sleep(0.02)
            cli = socket.socket()
            cli.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 32 * 1024)
            cli.connect(("127.0.0.1", port))
            cli.setblocking(False)
            async with connect(
                f"ws://127.0.0.1:{port}/",
                sock=cli,
                proxy=None,
                max_size=None,
                compression=None,
                ping_interval=0.02,
                ping_timeout=20,
            ) as ws:
                async for msg in ws:
                    got += len(msg)
                    await asyncio.sleep(0.0015)
    finally:
        # force_exit: shutdown otherwise waits for every connection task, and
        # a deadlocked sender never finishes — uvicorn also holds SIGTERM while
        # serving, so an outer `timeout` cannot end the run either.
        server.should_exit = True
        server.force_exit = True
        try:
            async with asyncio.timeout(10):
                await serving
        except TimeoutError:
            serving.cancel()
        logging.getLogger("uvicorn.error").removeHandler(catcher)
    assert errors == []
    assert got == total
    assert overlaps > 0
