"""Which board the bench's serial console points at (ADR-063).

The behaviours worth pinning: a switch reaches the agent (the next serial
open asks for the new COM/baud) without restarting the hub; a switch never
lands under someone reading the console or under a running job unless asked
to; the choice survives a restart; a broken or missing table never leaves
the console silently on the wrong board.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from alb.remote import bench_board, forwarder
from alb.remote.bench_board import BoardError, load_boards, serial_target, use_board
from alb.remote.flash import get_flash_service, reset_flash_service

TABLE = """
[boards.board-a]
serial_com = "COM27"
serial_baud = 1500000
note = "left header"

[boards.board-b]
serial_com = "COM27"
serial_baud = 115200
"""


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for var in ("ALB_BENCH_BOARDS", "ALB_AGENT_SERIAL_COM", "ALB_AGENT_SERIAL_BAUD"):
        monkeypatch.delenv(var, raising=False)
    reset_flash_service()
    yield
    reset_flash_service()


@pytest.fixture
def table(tmp_path, monkeypatch):
    path = tmp_path / "boards.toml"
    path.write_text(TABLE, encoding="utf-8")
    monkeypatch.setenv("ALB_BENCH_BOARDS", str(path))
    return path


# ── the table ───────────────────────────────────────────────────────────


def test_the_table_is_read(table):
    boards = load_boards()
    assert sorted(boards) == ["board-a", "board-b"]
    assert (boards["board-a"].serial_com, boards["board-a"].serial_baud) == ("COM27", 1500000)
    assert boards["board-a"].note == "left header"


def test_no_table_configured_is_an_empty_table():
    assert load_boards() == {}


@pytest.mark.parametrize(
    ("body", "why"),
    [
        ('[boards.Board]\nserial_com = "COM1"\nserial_baud = 115200\n', "name must be"),
        ('[boards.x]\nserial_com = "COM1"\nserial_baud = true\n', "serial_baud"),
        ('[boards.x]\nserial_com = "COM1"\nserial_baud = 50\n', "serial_baud"),
        ('[boards.x]\nserial_com = "COM1"\nserial_baud = "115200"\n', "serial_baud"),
        ('[boards.x]\nserial_com = "COM 1"\nserial_baud = 115200\n', "serial_com"),
        ("[boards.x]\nserial_baud = 115200\n", "serial_com"),
        ('[boards.x]\nserial_com = "COM1"\nserial_baud = 115200\nbuad = 9600\n', "unknown key"),
        ("[other]\nx = 1\n", "no [boards.<name>]"),
        ("[boards.x\n", "cannot read"),
    ],
)
def test_a_wrong_table_names_the_file_and_the_entry(tmp_path, monkeypatch, body, why):
    path = tmp_path / "boards.toml"
    path.write_text(body, encoding="utf-8")
    monkeypatch.setenv("ALB_BENCH_BOARDS", str(path))
    with pytest.raises(BoardError) as e:
        load_boards()
    assert e.value.code == "BENCH_BOARDS_INVALID"
    assert why in e.value.message and str(path) in e.value.message


def test_a_table_that_is_not_there_is_an_error_not_an_empty_table(tmp_path, monkeypatch):
    monkeypatch.setenv("ALB_BENCH_BOARDS", str(tmp_path / "nope.toml"))
    with pytest.raises(BoardError, match="does not exist"):
        load_boards()


# ── where the forwarder points ──────────────────────────────────────────


def test_without_a_choice_hub_env_applies_as_before(table, monkeypatch):
    monkeypatch.setenv("ALB_AGENT_SERIAL_COM", "COM4")
    monkeypatch.setenv("ALB_AGENT_SERIAL_BAUD", "921600")
    t = serial_target()
    assert (t["com"], t["baud"], t["board"], t["source"], t["problem"]) == (
        "COM4",
        921600,
        None,
        "hub.env",
        None,
    )
    assert forwarder.serial_com() == "COM4" and forwarder.serial_baud() == 921600


def test_a_chosen_board_wins_over_hub_env(table, monkeypatch):
    monkeypatch.setenv("ALB_AGENT_SERIAL_COM", "COM4")
    bench_board.save_active("board-b")
    t = serial_target()
    assert (t["com"], t["baud"], t["board"], t["source"]) == ("COM27", 115200, "board-b", "board")
    assert forwarder.serial_configured()


def test_a_chosen_board_gone_from_the_table_falls_back_and_says_so(table, monkeypatch):
    monkeypatch.setenv("ALB_AGENT_SERIAL_COM", "COM4")
    bench_board.save_active("board-z")
    t = serial_target()
    assert (t["com"], t["source"]) == ("COM4", "hub.env")
    assert "board-z" in t["problem"] and "not in the board table" in t["problem"]


def test_a_broken_table_falls_back_and_says_so(table, monkeypatch):
    bench_board.save_active("board-a")
    table.write_text("[boards.x\n", encoding="utf-8")
    t = serial_target()
    assert t["source"] == "hub.env" and "cannot read" in t["problem"]


# ── the forwarder itself ────────────────────────────────────────────────


class _Session:
    """Stands in for a live _SerialSession with `refs` readers."""

    def __init__(self, refs: int) -> None:
        self.refs = refs
        self.closed = asyncio.Event()
        self.aclosed = False

    async def aclose(self) -> None:
        self.aclosed = True
        self.closed.set()


class _Channel:
    async def recv(self) -> bytes:
        await asyncio.Event().wait()
        return b""

    async def send(self, data: bytes) -> None:
        pass

    async def aclose(self) -> None:
        pass


class _Agent:
    def __init__(self) -> None:
        self.opened: list[dict] = []

    async def open_data_channel(self, *, ctype, role, params, timeout):  # noqa: ASYNC109
        self.opened.append(dict(params))
        return _Channel()


async def test_the_next_open_asks_the_agent_for_the_new_port_and_baud():
    agent = _Agent()
    fwd = forwarder.SerialForwarder(lambda: agent, com="COM27", baud=1500000, port=0)
    assert await fwd.retarget("COM27", 115200) == 0
    assert fwd.target == ("COM27", 115200)
    session = await fwd._acquire()
    try:
        assert agent.opened == [{"com": "COM27", "baud": 115200}]
    finally:
        await session.aclose()


async def test_a_console_in_use_is_not_switched_unless_forced():
    fwd = forwarder.SerialForwarder(lambda: None, com="COM27", baud=1500000, port=0)
    live = _Session(refs=2)
    fwd._session = live  # type: ignore[assignment]
    with pytest.raises(forwarder.SerialBusyError) as e:
        await fwd.retarget("COM27", 115200)
    assert e.value.readers == 2
    assert fwd.target == ("COM27", 1500000) and not live.aclosed, "nothing changed"

    assert await fwd.retarget("COM27", 115200, force=True) == 2
    assert live.aclosed and fwd._session is None
    assert fwd.target == ("COM27", 115200)


# ── switching ───────────────────────────────────────────────────────────


async def test_no_table_means_no_switch():
    with pytest.raises(BoardError) as e:
        await use_board("board-a")
    assert e.value.code == "BENCH_BOARDS_NOT_CONFIGURED"


async def test_an_unknown_board_lists_the_known_ones(table):
    with pytest.raises(BoardError) as e:
        await use_board("board-c")
    assert e.value.code == "BENCH_BOARD_UNKNOWN"
    assert "board-a, board-b" in e.value.message


async def test_the_choice_is_remembered_across_a_restart(table):
    out = await use_board("board-b")
    assert out["board"]["serial_baud"] == 115200 and out["bound"] is False
    # a fresh process reads the same workspace
    assert bench_board.active_name() == "board-b"
    assert serial_target()["baud"] == 115200


async def test_a_running_job_refuses_the_switch(table):
    async with get_flash_service().held("flash plan x"):
        with pytest.raises(BoardError) as e:
            await use_board("board-b", force=True)
    assert e.value.code == "FASTBOOT_BUSY" and "flash plan x" in e.value.message
    assert bench_board.active_name() is None, "not remembered either"


async def test_the_switch_holds_off_jobs_while_it_runs(table):
    svc = get_flash_service()
    seen: list[bool] = []
    real = forwarder.SerialForwarder.retarget

    async def spy(self, com, baud, *, force=False):
        seen.append(svc.busy)
        return await real(self, com, baud, force=force)

    fwd = forwarder.get_serial_forwarder()
    fwd.retarget = spy.__get__(fwd)  # type: ignore[method-assign]
    await use_board("board-b")
    assert seen == [True] and not svc.busy


async def test_readers_refuse_the_switch_and_force_cuts_them_off(table):
    fwd = forwarder.get_serial_forwarder()
    live = _Session(refs=1)
    fwd._session = live  # type: ignore[assignment]
    with pytest.raises(BoardError) as e:
        await use_board("board-b")
    assert e.value.code == "BENCH_SERIAL_BUSY" and "1 reader" in e.value.message
    assert bench_board.active_name() is None

    out = await use_board("board-b", force=True)
    assert out["cut_off"] == 1 and live.aclosed
    assert fwd.target == ("COM27", 115200)


async def test_the_status_shows_what_the_forwarder_asks_for(table):
    fwd = forwarder.get_serial_forwarder()
    await use_board("board-b")
    serial = forwarder.forwarder_status()["serial"]
    assert (serial["com"], serial["baud"], serial["board"]) == ("COM27", 115200, "board-b")
    assert fwd.target == ("COM27", 115200)


# ── API ─────────────────────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    from alb.api.server import create_app

    with TestClient(create_app()) as c:
        yield c


def test_api_lists_switches_and_refuses_with_a_code(table, client):
    view = client.get("/api/bench/board").json()
    assert view["ok"] and [b["name"] for b in view["boards"]] == ["board-a", "board-b"]
    assert view["active"] is None and view["source"] == "hub.env"

    done = client.post("/api/bench/board", json={"name": "board-a"}).json()
    assert done["ok"] and done["active"] == "board-a"
    assert done["data"]["board"]["serial_baud"] == 1500000

    bad = client.post("/api/bench/board", json={"name": "board-c"}).json()
    assert (bad["ok"], bad["code"]) == (False, "BENCH_BOARD_UNKNOWN")
