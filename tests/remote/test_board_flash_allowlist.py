"""Per-board flash allowlists and the board identity check (ADR-063 phase 2).

One bench host, one USB cable, boards swapped on it. With one allowlist per
agent, the list had to be the union of every board's partitions, so a name
only one board has was accepted for all of them. The behaviours pinned here:

* the agent applies the list of the board the job names, never the union,
  and never "any name" when no list applies;
* a board with a configured product is asked who it is before anything is
  written, and another board on the cable is refused;
* the hub sends the chosen board with every job and refuses a partition op
  before row 1 when no list applies — the agent checks again either way;
* what the agent advertises is what the hub parses (one test drives the
  real hello over the real route).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alb.remote import bench_board
from alb.remote.bench_board import PartitionList, parse_agent_boards, partition_list
from alb.remote.flash import FlashService
from alb.remote.flash_plan import AgentView, PlanRow, check_agent
from tests.remote.bench_tables import new_agent_ops
from tests.remote.test_flash_service import _Agent, _Channel, _ctl

_AGENT_PATH = Path(__file__).resolve().parents[2] / "clients" / "windows-agent" / "alb_agent.py"


def _load_agent():
    spec = importlib.util.spec_from_file_location("alb_agent_boards_under_test", _AGENT_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


agent = _load_agent()

LISTS = {"board-a": {"boot", "dtbo", "vendor_cfg"}, "board-b": {"boot_a", "boot_b"}}


@pytest.fixture(autouse=True)
def _clean_agent(monkeypatch):
    monkeypatch.setattr(agent, "_FLASH_PARTITIONS", frozenset())
    monkeypatch.setattr(agent, "_BOARD_PARTITIONS", {})
    monkeypatch.setattr(agent, "_BOARD_PRODUCT", {})


def _per_board(monkeypatch, lists=LISTS, products=None):
    monkeypatch.setattr(agent, "_BOARD_PARTITIONS", {b: frozenset(v) for b, v in lists.items()})
    monkeypatch.setattr(agent, "_BOARD_PRODUCT", dict(products or {}))


# ── agent.conf ──────────────────────────────────────────────────────────


def _conf(tmp_path, body: str) -> Path:
    exe = tmp_path / "fastboot"
    exe.write_text("#!/bin/sh\n")
    path = tmp_path / "agent.conf"
    path.write_text(f"hub_url=ws://h/agent/connect\nfastboot_path={exe}\n{body}", encoding="utf-8")
    return path


def test_per_board_lines_reach_the_lists_hello_and_self_check(tmp_path):
    path = _conf(
        tmp_path,
        "flash_partitions.board-a=boot, dtbo\n"
        "fastboot_product.board-a=prod_a\n"
        "flash_partitions.board-b=boot_a\n"
        "fastboot_product.board-c=prod_c\n",
    )
    agent._apply_flash_config(agent._parse_args(["--config", str(path)]))
    assert agent._BOARD_PARTITIONS == {
        "board-a": frozenset({"boot", "dtbo"}),
        "board-b": frozenset({"boot_a"}),
    }
    assert agent._BOARD_PRODUCT == {"board-a": "prod_a", "board-c": "prod_c"}
    view = {
        "board-a": {"partitions": ["boot", "dtbo"], "product": "prod_a"},
        "board-b": {"partitions": ["boot_a"], "product": None},
        # A product without a list: None, which the hub must never read as "any".
        "board-c": {"partitions": None, "product": "prod_c"},
    }
    assert agent._boards_view() == view
    assert json.loads(agent._hello("a", "n", None))["boards"] == view
    assert agent._FLASH_PARTITIONS == frozenset()


@pytest.mark.parametrize(
    ("line", "why"),
    [
        ("flash_partitions.-a=boot", "bad board name"),
        ("flash_partitions.=boot", "bad board name"),
        ("flash_partitions.a.b=boot", "bad board name"),
        # Not quietly lower-cased: the hub's table refuses this name too.
        ("flash_partitions.Board-A=boot", "bad board name"),
        # A truly empty value (not ` , ,`): the loader's "empty = unset" rule
        # dropped it, and the agent came up on "any name". Seen 2026-10-22.
        ("flash_partitions.board-a=", "is empty"),
        ("flash_partitions.board-a=''", "is empty"),
        # Dropped, this one would mean "not checked".
        ("fastboot_product.board-a=", "is empty"),
        ("flash_partitionz.a=boot", "unknown key"),
        ("token.a=x", "unknown key"),
    ],
)
def test_a_wrong_per_board_line_is_a_hard_error(tmp_path, line, why):
    path = _conf(tmp_path, line + "\n")
    with pytest.raises(SystemExit) as e:
        agent._parse_args(["--config", str(path)])
    assert why in str(e.value) and "agent.conf:3" in str(e.value)


@pytest.mark.parametrize(
    ("body", "why"),
    [
        # The union again, or a list silently ignored: refused.
        ("flash_partitions=boot\nflash_partitions.board-a=boot\n", "both set"),
        # Dropped, it would leave the agent on "any name".
        ("flash_partitions.board-a= , ,\n", "lists no partitions"),
    ],
)
def test_settings_that_would_widen_the_list_stop_the_agent(tmp_path, body, why):
    path = _conf(tmp_path, body)
    with pytest.raises(SystemExit) as e:
        agent._apply_flash_config(agent._parse_args(["--config", str(path)]))
    assert why in str(e.value)


def test_a_refused_setting_leaves_the_running_agent_alone(tmp_path, monkeypatch):
    """Checked before the previous instance is stopped: a bad line must not
    leave the bench with no agent at all."""
    path = _conf(tmp_path, "flash_partitions=boot\nflash_partitions.board-a=boot\n")
    args = agent._parse_args(["--config", str(path)])
    stopped: list[object] = []

    def claim(a):
        # Reached = the old agent would be stopped. Raise so a wrong order
        # fails here instead of running on into the connect loop.
        stopped.append(a)
        raise RuntimeError("the previous instance was stopped")

    monkeypatch.setattr(agent, "_parse_args", lambda *a: args)
    monkeypatch.setattr(agent, "_claim_instance", claim)
    monkeypatch.setattr(sys, "argv", ["alb_agent.py"])
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    try:
        with pytest.raises(SystemExit, match="both set"):
            agent.main()
    finally:
        for h in root.handlers[:]:
            if h not in handlers:
                root.removeHandler(h)
                h.close()
        root.setLevel(level)
    assert stopped == []


# ── agent: which list applies ───────────────────────────────────────────


def _flash_wire(partition: str, **extra) -> bytes:
    img = b"image-bytes" * 4
    req = {
        "op": "flash",
        "partition": partition,
        "size": len(img),
        "sha256": hashlib.sha256(img).hexdigest(),
        **extra,
    }
    return _ctl_frame(req) + agent_frame(b"D", img)


def agent_frame(kind: bytes, payload: bytes) -> bytes:
    import struct

    return struct.pack(">cI", kind, len(payload)) + payload


def _ctl_frame(req: dict) -> bytes:
    return agent_frame(b"C", json.dumps(req).encode())


class _Ws:
    """Feeds the agent canned frames and keeps what it sends back."""

    def __init__(self, data: bytes) -> None:
        self._data, self._pos = data, 0
        self.sent: list[dict] = []

    async def recv(self) -> bytes:
        out = self._data[self._pos : self._pos + 4096]
        self._pos += len(out)
        return out

    async def send(self, data: bytes) -> None:
        self.sent.append(json.loads(data[5:]))

    def done(self) -> dict:
        return [m for m in self.sent if m.get("ev") == "done"][-1]

    def accepted(self) -> bool:
        return any(m.get("ev") == "accepted" for m in self.sent)


async def _run(monkeypatch, wire: bytes, *, product: str = "") -> tuple[_Ws, list, list]:
    ran: list[list[str]] = []
    asked: list[str] = []

    async def fake_run(_ws, argv, timeout_s=300.0):
        ran.append(argv)
        return 0, "OKAY", ""

    async def fake_present(_fastboot):
        return True

    async def fake_product(fastboot):
        asked.append(fastboot)
        return product, f"product: {product}" if product else "getvar:product FAILED"

    monkeypatch.setattr(agent, "_run_fastboot", fake_run)
    monkeypatch.setattr(agent, "_device_present", fake_present)
    monkeypatch.setattr(agent, "_device_product", fake_product)
    ws = _Ws(wire)
    await agent._run_job(ws, agent._build_ops("/opt/fastboot"))
    return ws, ran, asked


async def test_a_partition_op_without_a_board_is_refused(monkeypatch):
    _per_board(monkeypatch)
    ws, ran, _ = await _run(monkeypatch, _flash_wire("boot"))
    done = ws.done()
    assert (done["ok"], done["code"]) == (False, "FLASH_BOARD_NOT_LISTED")
    assert "names no board" in done["error"] and "board-a, board-b" in done["error"]
    assert ran == [] and not ws.accepted(), "refused before a byte of the image"


async def test_a_board_without_a_list_is_refused(monkeypatch):
    _per_board(monkeypatch)
    ws, ran, _ = await _run(monkeypatch, _flash_wire("boot", board="board-z"))
    assert ws.done()["code"] == "FLASH_BOARD_NOT_LISTED"
    assert "no partition list for board 'board-z'" in ws.done()["error"]
    assert ran == []


async def test_another_boards_partition_is_refused(monkeypatch):
    """The reason for per-board lists: `boot_a` is fine on board-b and must
    not be written to board-a just because some board has it."""
    _per_board(monkeypatch)
    ws, ran, _ = await _run(monkeypatch, _flash_wire("boot_a", board="board-a"))
    done = ws.done()
    assert done["code"] == "FLASH_PARTITION_REJECTED"
    assert "not on board 'board-a'" in done["error"]
    assert ran == [] and not ws.accepted()


async def test_the_boards_own_partition_is_flashed(monkeypatch):
    _per_board(monkeypatch)
    ws, ran, _ = await _run(monkeypatch, _flash_wire("boot_a", board="board-b"))
    assert ws.done()["ok"] is True
    assert ran and ran[0][:3] == ["/opt/fastboot", "flash", "boot_a"]


async def test_one_list_for_all_boards_works_as_before(monkeypatch):
    """No per-board lines: the board in the request changes nothing."""
    monkeypatch.setattr(agent, "_FLASH_PARTITIONS", frozenset({"boot"}))
    ws, ran, _ = await _run(monkeypatch, _flash_wire("boot", board="board-z"))
    assert ws.done()["ok"] is True and ran
    ws, ran, _ = await _run(monkeypatch, _flash_wire("dtbo", board="board-z"))
    assert ws.done()["code"] == "FLASH_PARTITION_REJECTED" and ran == []


# ── agent: is the board on the cable the board the job names ───────────


async def test_the_right_board_is_flashed(monkeypatch):
    _per_board(monkeypatch, products={"board-b": "prod_b"})
    ws, ran, asked = await _run(
        monkeypatch, _flash_wire("boot_a", board="board-b"), product="prod_b"
    )
    assert ws.done()["ok"] is True and ran
    assert asked == ["/opt/fastboot"], "asked once, with this host's fastboot"


async def test_another_board_on_the_cable_is_not_flashed(monkeypatch):
    _per_board(monkeypatch, products={"board-b": "prod_b"})
    ws, ran, _ = await _run(monkeypatch, _flash_wire("boot_a", board="board-b"), product="prod_a")
    done = ws.done()
    assert done["code"] == "FLASH_BOARD_MISMATCH"
    assert "'prod_a'" in done["error"] and "'prod_b'" in done["error"]
    assert ran == [] and not ws.accepted(), "refused before a byte of the image"


async def test_a_board_that_names_no_product_is_not_erased(monkeypatch):
    _per_board(monkeypatch, products={"board-a": "prod_a"})
    wire = _ctl_frame({"op": "erase", "partition": "dtbo", "board": "board-a"})
    ws, ran, _ = await _run(monkeypatch, wire, product="")
    assert ws.done()["code"] == "FLASH_BOARD_MISMATCH" and "named no product" in ws.done()["error"]
    assert ran == []


async def test_a_question_is_not_held_up_by_the_identity_check(monkeypatch):
    """getvar changes nothing: no product query, whatever is configured."""
    _per_board(monkeypatch, products={"board-a": "prod_a"})
    wire = _ctl_frame({"op": "getvar", "name": "product", "board": "board-a"})
    ws, ran, asked = await _run(monkeypatch, wire, product="prod_x")
    assert ws.done()["ok"] is True and ran and asked == []


async def test_no_product_configured_means_no_query(monkeypatch):
    _per_board(monkeypatch)
    ws, ran, asked = await _run(monkeypatch, _flash_wire("boot", board="board-a"))
    assert ws.done()["ok"] is True and ran and asked == []


@pytest.mark.skipif(sys.platform == "win32", reason="uses a shell script as fastboot")
@pytest.mark.parametrize(
    ("printed", "product"),
    [
        ("product: prod_a\nFinished. Total time: 0.001s", "prod_a"),
        ("(bootloader) product: prod_a  ", "prod_a"),
        ("getvar:product FAILED (remote: 'unknown variable')", ""),
        ("", ""),
    ],
)
async def test_the_product_answer_is_read_from_fastboots_stderr(tmp_path, printed, product):
    script = tmp_path / "fastboot"
    script.write_text(f"#!/bin/sh\ncat >&2 <<'EOF'\n{printed}\nEOF\n", encoding="utf-8")
    script.chmod(0o755)
    got, text = await agent._device_product(str(script))
    assert got == product
    assert text == printed.strip()


# ── hub: what it reads from the hello ───────────────────────────────────


def test_the_hello_field_is_read_defensively():
    raw = {
        "board-a": {"partitions": ["boot"], "product": "prod_a"},
        "board-b": {"partitions": None, "product": ""},
        "Bad Name": {"partitions": ["boot"]},
        "board-c": "not a table",
        "board-d": {"partitions": "boot", "product": 7},
    }
    assert parse_agent_boards(raw) == {
        "board-a": {"partitions": ["boot"], "product": "prod_a"},
        "board-b": {"partitions": None, "product": None},
        "board-d": {"partitions": None, "product": None},
    }
    assert parse_agent_boards(None) == {} and parse_agent_boards([1]) == {}


class _Conn:
    def __init__(self, boards=None, flash_partitions=()):
        self.boards = boards or {}
        self.flash_partitions = list(flash_partitions)


PER_BOARD = {
    "board-a": {"partitions": ["boot", "dtbo"], "product": "prod_a"},
    "board-b": {"partitions": ["boot_a"], "product": None},
    "board-c": {"partitions": None, "product": "prod_c"},
}


@pytest.mark.parametrize(
    ("conn", "board", "names", "problem"),
    [
        (_Conn(flash_partitions=["cfg"]), None, ["cfg"], ""),
        (_Conn(flash_partitions=["cfg"]), "board-a", ["cfg"], ""),
        (_Conn(), "board-a", [], ""),  # one empty list = any name, as before
        (_Conn(PER_BOARD), "board-a", ["boot", "dtbo"], ""),
        (_Conn(PER_BOARD), None, [], "no bench board is chosen"),
        (_Conn(PER_BOARD), "board-c", [], "no partition list for board 'board-c'"),
        (_Conn(PER_BOARD), "board-z", [], "it has: board-a, board-b"),
        (_Conn({"board-a": {"partitions": [], "product": None}}), "board-a", [], "is empty"),
        (None, "board-a", [], ""),
    ],
)
def test_which_list_applies(conn, board, names, problem):
    got = partition_list(conn, board)
    assert got.names == names
    if problem:
        assert problem in got.problem
    else:
        assert got.problem == ""


def test_a_problem_never_comes_with_any_name():
    """Empty `names` means "any well-formed name" only without a problem —
    every per-board miss must carry one."""
    for board in (None, "board-c", "board-z"):
        got = partition_list(_Conn(PER_BOARD), board)
        assert got.names == [] and got.problem, board


# ── hub: what it sends, and what it refuses before sending ──────────────


@pytest.fixture
def chosen(tmp_path, monkeypatch):
    table = tmp_path / "boards.toml"
    table.write_text(
        '[boards.board-a]\nserial_com = "COM1"\nserial_baud = 115200\n'
        '[boards.board-b]\nserial_com = "COM1"\nserial_baud = 1500000\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("ALB_BENCH_BOARDS", str(table))

    def choose(name: str | None) -> None:
        if name is not None:
            bench_board.save_active(name)

    return choose


def _agent(boards=None, flash_partitions=(), replies=None) -> _Agent:
    a = _Agent(_Channel(replies or [_ctl({"ev": "done", "ok": True, "rc": 0})]))
    a.boards = boards or {}  # type: ignore[attr-defined]
    a.flash_partitions = list(flash_partitions)  # type: ignore[attr-defined]
    a.op_table = new_agent_ops()  # type: ignore[attr-defined]
    a.ops_reported = True  # type: ignore[attr-defined]
    return a


async def test_no_board_chosen_refuses_a_partition_op_before_any_channel(chosen):
    a = _agent(PER_BOARD)
    r = await FlashService(lambda: a).run_op("erase", {"partition": "boot"})
    assert (r.ok, r.code) == (False, "FLASH_BOARD_NOT_LISTED")
    assert "alb bench board use" in r.error
    assert a.opened == []


async def test_the_chosen_board_rides_with_the_request(chosen):
    chosen("board-b")
    a = _agent(PER_BOARD)
    r = await FlashService(lambda: a).run_op("erase", {"partition": "boot_a"})
    assert r.ok, r.error
    assert a.channel.request() == {"op": "erase", "partition": "boot_a", "board": "board-b"}


async def test_another_boards_partition_is_refused_on_the_hub(chosen):
    chosen("board-a")
    a = _agent(PER_BOARD)
    r = await FlashService(lambda: a).run_op("erase", {"partition": "boot_a"})
    assert r.code == "FLASH_PARTITION_REJECTED" and a.opened == []


async def test_an_op_without_a_partition_runs_with_no_board_chosen(chosen):
    """`reboot` names no partition, so no list is needed; the agent still
    checks the product of a chosen board — here there is none to check."""
    a = _agent(PER_BOARD)
    r = await FlashService(lambda: a).run_op("reboot", {"target": ""})
    assert r.ok, r.error
    assert "board" not in a.channel.request()


async def test_an_agent_with_one_list_still_gets_the_board(chosen):
    chosen("board-a")
    a = _agent(flash_partitions=["misc"])
    r = await FlashService(lambda: a).run_op("erase", {"partition": "misc"})
    assert r.ok and a.channel.request()["board"] == "board-a"


async def test_status_says_why_nothing_may_be_written(chosen):
    svc = FlashService(lambda: _agent(PER_BOARD))
    st = svc.status()
    assert st["partitions"] == [] and "no bench board is chosen" in st["partitions_problem"]
    chosen("board-a")
    st = svc.status()
    assert (st["partitions"], st["partitions_problem"], st["board"]) == (
        ["boot", "dtbo"],
        "",
        "board-a",
    )


def _row(line: int, partition: str) -> PlanRow:
    return PlanRow(line=line, action="erase", partition=partition)


def test_a_plan_with_no_list_applying_is_refused_before_row_one():
    view = AgentView(
        caps=["adb", "fastboot"],
        partitions=[],
        partitions_problem="the agent keeps a partition list per board and no bench board is chosen",
    )
    problems = check_agent([_row(3, "boot"), _row(5, "dtbo")], view)
    assert any("no bench board is chosen (line 3, 5)" in p for p in problems), problems


def test_a_plan_off_the_boards_list_names_the_board_key():
    allowed = partition_list(_Conn(PER_BOARD), "board-a")
    view = AgentView(
        caps=["adb", "fastboot"],
        partitions=allowed.names,
        partitions_problem=allowed.problem,
        partitions_key=allowed.key,
    )
    problems = check_agent([_row(3, "boot"), _row(4, "boot_a")], view)
    assert any("boot_a (line 4)" in p and "flash_partitions.board-a" in p for p in problems)


def test_partition_list_dataclass_defaults_are_the_old_meaning():
    assert PartitionList([]) == PartitionList([], "", "flash_partitions", None)


# ── across the wire: the real agent's hello, through the real route ─────


def test_the_real_hello_lands_as_the_hubs_lists(monkeypatch):
    from alb.api.server import create_app
    from alb.remote import protocol as p
    from alb.remote.registry import get_agent_registry

    monkeypatch.setenv("ALB_ADB_FORWARD_PORT", "0")
    monkeypatch.delenv("ALB_AGENT_TOKEN", raising=False)
    _per_board(monkeypatch, products={"board-a": "prod_a"})
    with TestClient(create_app()) as c, c.websocket_connect("/agent/connect") as ws:
        ws.send_text(agent._hello("agent-boards", "bench", None))
        assert p.decode_control(ws.receive_text())["verb"] == p.Verb.HELLO_OK.value
        conn = get_agent_registry().current_agent()
        assert conn is not None and conn.agent_id == "agent-boards"
        assert conn.boards == {
            "board-a": {"partitions": ["boot", "dtbo", "vendor_cfg"], "product": "prod_a"},
            "board-b": {"partitions": ["boot_a", "boot_b"], "product": None},
        }
        assert partition_list(conn, "board-b").names == ["boot_a", "boot_b"]
        assert partition_list(conn, None).problem
