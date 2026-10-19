"""`alb port open` / `alb port check` (ADR-057 endpoint resolution + port_channel).

The fake adb server runs on its own thread here because the commands call
``asyncio.run()`` themselves — a fake on the test's loop would never get to
accept. The endpoint reaches the command only through a monkeypatched
``resolve_endpoint``: that is how the tests prove the command asks the
resolver instead of assuming a port.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
from collections.abc import Iterator

import pytest
from typer.testing import CliRunner

from alb.cli import port_cli
from alb.infra.adb_endpoint import AdbEndpoint
from alb.remote.port_channel import PortChannel
from tests.transport.test_adb_socket import FakeAdbServer, FakeAdbThread, free_port

runner = CliRunner()


@pytest.fixture(autouse=True)
def _short_half_close_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    """A closed client keeps its device stream for the grace (port_channel,
    "Half-close"); these tests are not about that wait."""
    monkeypatch.setattr("alb.remote.port_channel.HALF_CLOSE_GRACE_S", 0.05)


@pytest.fixture
def fake() -> Iterator[FakeAdbServer]:
    thread = FakeAdbThread(ports={5039: "echo"})
    yield thread.start()
    thread.stop()


@pytest.fixture
def via_resolver(fake: FakeAdbServer, monkeypatch: pytest.MonkeyPatch) -> list[str | None]:
    """Route the command to the fake *through* resolve_endpoint, and record
    what config value the command handed the resolver."""
    asked: list[str | None] = []

    def resolve(config_socket=None, *, allow_hub=True):
        asked.append(config_socket)
        return AdbEndpoint(f"tcp:127.0.0.1:{fake.port}", "hub")

    monkeypatch.setattr(port_cli, "resolve_endpoint", resolve)
    monkeypatch.setattr(port_cli, "_config_socket", lambda: "tcp:127.0.0.1:1")
    return asked


# ── check ────────────────────────────────────────────────────────────────


def test_check_accepted(fake, via_resolver):
    r = runner.invoke(port_cli.app, ["check", "5039"])
    assert r.exit_code == 0, r.output
    assert f"adb server: tcp:127.0.0.1:{fake.port} (from hub)" in r.output
    assert "[ok] device SERIAL1 (the only one listed, device) accepted tcp:5039" in r.output
    assert fake.requests == ["host:devices", "host:transport-any", "tcp:5039"]
    assert via_resolver == ["tcp:127.0.0.1:1"], "config socket must go through the resolver"


def test_check_refused_by_device_exits_1_with_its_message(fake, via_resolver):
    r = runner.invoke(port_cli.app, ["check", "5040"])
    assert r.exit_code == 1, r.output
    assert "[fail] PORT_DEVICE_REFUSED: device refused tcp:5040: closed" in r.output
    assert "gdbserver :5040" in r.output
    assert "[ok]" not in r.output


def test_check_with_serial_skips_the_listing(fake, via_resolver):
    r = runner.invoke(port_cli.app, ["check", "5039", "--serial", "SERIAL1"])
    assert r.exit_code == 0, r.output
    assert fake.requests == ["host:transport:SERIAL1", "tcp:5039"]
    assert "device SERIAL1 (--serial) accepted" in r.output


def test_check_two_devices_exits_2_and_lists_them(via_resolver, monkeypatch):
    thread = FakeAdbThread(devices=(("SERIAL1", "device"), ("SERIAL2", "offline")))
    two = thread.start()
    monkeypatch.setattr(
        port_cli,
        "resolve_endpoint",
        lambda *a, **k: AdbEndpoint(f"tcp:127.0.0.1:{two.port}", "env"),
    )
    try:
        r = runner.invoke(port_cli.app, ["check", "5039"])
    finally:
        thread.stop()
    assert r.exit_code == 2, r.output
    assert "[fail] PORT_DEVICE_AMBIGUOUS" in r.output
    assert "SERIAL1 (device)" in r.output and "SERIAL2 (offline)" in r.output
    assert "--serial" in r.output
    assert two.requests == ["host:devices"]


def test_check_unix_socket_endpoint_is_refused_clearly(fake, monkeypatch):
    monkeypatch.setattr(port_cli, "_config_socket", lambda: None)
    monkeypatch.setattr(
        port_cli, "resolve_endpoint", lambda *a, **k: AdbEndpoint("unix:/tmp/adb.sock", "env")
    )
    r = runner.invoke(port_cli.app, ["check", "5039"])
    assert r.exit_code == 2, r.output
    assert "[fail] ADB_ENDPOINT_UNSUPPORTED" in r.output and "unix:/tmp/adb.sock" in r.output
    assert fake.requests == []


def test_check_no_adb_server_exits_2(monkeypatch):
    monkeypatch.setattr(port_cli, "_config_socket", lambda: None)
    monkeypatch.setattr(
        port_cli,
        "resolve_endpoint",
        lambda *a, **k: AdbEndpoint(f"tcp:127.0.0.1:{free_port()}", "default"),
    )
    r = runner.invoke(port_cli.app, ["check", "5039"])
    assert r.exit_code == 2, r.output
    assert "[fail] ADB_SERVER_UNREACHABLE" in r.output


@pytest.mark.parametrize(
    "args", [["check", "0"], ["check", "65536"], ["check", "5039", "--serial", "../x"]]
)
def test_check_rejects_bad_arguments_before_asking(fake, via_resolver, args):
    r = runner.invoke(port_cli.app, args)
    assert r.exit_code == 2, r.output
    assert fake.requests == []


# ── open ─────────────────────────────────────────────────────────────────


def _serve_one_round_trip(payload: bytes):
    """Stand-in for the foreground serve loop: one client through the
    listener, then return as if Ctrl-C had been pressed."""

    async def serve(channel: PortChannel) -> None:
        reader, writer = await asyncio.open_connection("127.0.0.1", channel.bound_port)
        writer.write(payload)
        await writer.drain()
        assert await reader.readexactly(len(payload)) == payload
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        for _ in range(200):  # let the close line land before the summary
            if channel.bytes_from_device:
                break
            await asyncio.sleep(0.01)

    return serve


def test_open_round_trip_prints_port_connection_and_summary(fake, via_resolver, monkeypatch):
    local = free_port()
    monkeypatch.setattr(port_cli, "_serve", _serve_one_round_trip(b"hello"))
    r = runner.invoke(port_cli.app, ["open", "5039", "--local", str(local)])
    assert r.exit_code == 0, r.output
    assert f"adb server: tcp:127.0.0.1:{fake.port} (from hub)" in r.output
    assert "device: SERIAL1 (the only one listed, device)" in r.output
    assert f"listening on 127.0.0.1:{local} -> device tcp:5039" in r.output
    assert f"target remote 127.0.0.1:{local}" in r.output
    assert "[1] open 127.0.0.1:" in r.output
    assert "[1] closed · 5 B to device · 5 B from device" in r.output
    assert "stopped · 1 connection(s), 0 refused · 5 B to device · 5 B from device" in r.output
    assert fake.requests == ["host:devices", "host:transport-any", "tcp:5039"]


def test_open_defaults_local_to_the_device_port_when_free(fake, via_resolver, monkeypatch):
    port = free_port()
    fake.ports[port] = "echo"
    monkeypatch.setattr(port_cli, "_serve", _serve_one_round_trip(b"x"))
    r = runner.invoke(port_cli.app, ["open", str(port)])
    assert r.exit_code == 0, r.output
    assert f"listening on 127.0.0.1:{port} -> device tcp:{port}" in r.output
    assert "is in use here" not in r.output


def test_open_moves_off_a_taken_device_port_and_says_so(fake, via_resolver, monkeypatch):
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        fake.ports[port] = "echo"
        monkeypatch.setattr(port_cli, "_serve", _serve_one_round_trip(b"x"))
        r = runner.invoke(port_cli.app, ["open", str(port)])
    assert r.exit_code == 0, r.output
    assert f"-> device tcp:{port}  ({port} is in use here; --local picks another)" in r.output
    assert f"listening on 127.0.0.1:{port} " not in r.output


def test_open_with_serial_binds_to_that_device(fake, via_resolver, monkeypatch):
    monkeypatch.setattr(port_cli, "_serve", _serve_one_round_trip(b"s"))
    r = runner.invoke(
        port_cli.app, ["open", "5039", "--local", str(free_port()), "--serial", "SERIAL1"]
    )
    assert r.exit_code == 0, r.output
    assert "device: SERIAL1 (--serial)" in r.output
    assert fake.requests == ["host:transport:SERIAL1", "tcp:5039"]


def test_open_refuses_non_loopback_bind_without_the_flag(fake, via_resolver, monkeypatch):
    monkeypatch.setattr(port_cli, "_serve", _serve_one_round_trip(b"never"))
    r = runner.invoke(port_cli.app, ["open", "5039", "--bind", "0.0.0.0"])
    assert r.exit_code == 2, r.output
    assert "[fail] PORT_BIND_NOT_LOOPBACK" in r.output and "--allow-remote" in r.output
    assert "listening on" not in r.output
    assert fake.requests == [], "refused before the adb server was asked anything"


def test_open_explicit_busy_local_port_exits_2(fake, via_resolver):
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        r = runner.invoke(port_cli.app, ["open", "5039", "--local", str(taken.getsockname()[1])])
    assert r.exit_code == 2, r.output
    assert "[fail] PORT_LOCAL_BUSY" in r.output and "--local" in r.output


def test_open_ctrl_c_prints_the_summary_and_exits_0(fake, via_resolver, monkeypatch):
    async def interrupted(channel: PortChannel) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(port_cli, "_serve", interrupted)
    r = runner.invoke(port_cli.app, ["open", "5039", "--local", str(free_port())])
    assert r.exit_code == 0, r.output
    assert "stopped · 0 connection(s), 0 refused" in r.output


def test_open_two_devices_exits_2_without_listening(via_resolver, monkeypatch):
    thread = FakeAdbThread(devices=(("SERIAL1", "device"), ("SERIAL2", "device")))
    two = thread.start()
    monkeypatch.setattr(
        port_cli,
        "resolve_endpoint",
        lambda *a, **k: AdbEndpoint(f"tcp:127.0.0.1:{two.port}", "env"),
    )
    try:
        r = runner.invoke(port_cli.app, ["open", "5039", "--local", str(free_port())])
    finally:
        thread.stop()
    assert r.exit_code == 2, r.output
    assert "[fail] PORT_DEVICE_AMBIGUOUS" in r.output
    assert "listening on" not in r.output
