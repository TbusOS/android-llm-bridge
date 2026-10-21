"""Which board the bench's serial console is wired to (ADR-063).

One COM port on the bench host is often moved from board to board, and the
boards speak different baud rates. Before this, the COM port and baud were
read once from hub.env when alb-api started: switching boards meant editing
hub.env and restarting the hub — cutting every other user of the bench.

Now the hub reads a small table of boards (``ALB_BENCH_BOARDS``, a TOML file)::

    [boards.board-a]
    serial_com = "COM27"
    serial_baud = 1500000
    note = "debug UART on the left header"

and ``alb bench board use <name>`` points the serial forwarder at that
board's COM port and baud while the hub runs. The choice is kept in the
workspace (``bench/board.json``), so a restarted hub comes back on it.
Without a table, or with no board chosen, hub.env's ALB_AGENT_SERIAL_COM /
ALB_AGENT_SERIAL_BAUD still apply exactly as before.

The agent already opens whatever COM/baud each serial channel asks for
(ADR-050: "COM/baud is a per-session parameter the hub sends"); only the hub
side was fixed at start-up.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alb.infra.workspace import workspace_root

_log = logging.getLogger(__name__)

BOARDS_ENV = "ALB_BENCH_BOARDS"
_STATE = Path("bench") / "board.json"
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}\Z")
# What the agent hands to pyserial: COM27 on Windows, /dev/ttyUSB0 elsewhere.
_COM_RE = re.compile(r"^[A-Za-z0-9_./-]{1,64}\Z")
_BAUD_MIN, _BAUD_MAX = 300, 12_000_000


class BoardError(Exception):
    """A switch that cannot happen; `code` is an alb.infra.errors code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Board:
    name: str
    serial_com: str
    serial_baud: int
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "serial_com": self.serial_com,
            "serial_baud": self.serial_baud,
            "note": self.note,
        }


def boards_path() -> Path | None:
    raw = os.environ.get(BOARDS_ENV, "").strip()
    return Path(raw).expanduser() if raw else None


def load_boards() -> dict[str, Board]:
    """The board table, or {} when none is configured.

    A table that is set but unreadable or wrong raises BENCH_BOARDS_INVALID
    naming the file and the entry: a typo must not quietly leave the serial
    console on whatever board was there before."""
    path = boards_path()
    if path is None:
        return {}
    try:
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
    except FileNotFoundError:
        raise BoardError(
            "BENCH_BOARDS_INVALID", f"{BOARDS_ENV} names {path}, which does not exist"
        ) from None
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise BoardError("BENCH_BOARDS_INVALID", f"cannot read {path}: {e}") from None
    table = raw.get("boards")
    if not isinstance(table, dict) or not table:
        raise BoardError("BENCH_BOARDS_INVALID", f"{path}: no [boards.<name>] entries")
    boards: dict[str, Board] = {}
    for name, entry in table.items():
        where = f"{path}: [boards.{name}]"
        if not _NAME_RE.match(name):
            raise BoardError(
                "BENCH_BOARDS_INVALID",
                f"{where}: name must be 1-32 of a-z 0-9 _ - (starting with a letter or digit)",
            )
        if not isinstance(entry, dict):
            raise BoardError("BENCH_BOARDS_INVALID", f"{where}: expected a table")
        unknown = sorted(set(entry) - {"serial_com", "serial_baud", "note"})
        if unknown:
            raise BoardError(
                "BENCH_BOARDS_INVALID", f"{where}: unknown key(s) {', '.join(unknown)}"
            )
        com, baud, note = entry.get("serial_com"), entry.get("serial_baud"), entry.get("note", "")
        if not isinstance(com, str) or not _COM_RE.match(com):
            raise BoardError(
                "BENCH_BOARDS_INVALID", f"{where}: serial_com {com!r} is not a port name"
            )
        # bool is an int in Python; `serial_baud = true` is still a mistake.
        if (
            isinstance(baud, bool)
            or not isinstance(baud, int)
            or not _BAUD_MIN <= baud <= _BAUD_MAX
        ):
            raise BoardError(
                "BENCH_BOARDS_INVALID",
                f"{where}: serial_baud {baud!r} is not a number in {_BAUD_MIN}-{_BAUD_MAX}",
            )
        if not isinstance(note, str):
            raise BoardError("BENCH_BOARDS_INVALID", f"{where}: note must be text")
        boards[name] = Board(name, com, baud, note)
    return boards


def _state_path() -> Path:
    return workspace_root() / _STATE


def active_name() -> str | None:
    """The board last chosen with `alb bench board use`, or None."""
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    name = data.get("board") if isinstance(data, dict) else None
    return name if isinstance(name, str) and name else None


def save_active(name: str) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"board": name}) + "\n", encoding="utf-8")
    tmp.replace(path)


def _env_target() -> tuple[str | None, int]:
    from alb.remote.forwarder import DEFAULT_BAUD, _env_int

    return os.environ.get("ALB_AGENT_SERIAL_COM") or None, _env_int(
        "ALB_AGENT_SERIAL_BAUD", DEFAULT_BAUD
    )


def serial_target() -> dict[str, Any]:
    """Where the serial forwarder should point, and why.

    {"com", "baud", "board", "source", "problem"}: the chosen board when it is
    in the table; otherwise hub.env (source "hub.env", board None). `problem`
    says why a chosen board was not used — the table is broken, or the board
    is no longer in it — so the status page can show it instead of the hub
    silently falling back."""
    com, baud = _env_target()
    target: dict[str, Any] = {
        "com": com,
        "baud": baud,
        "board": None,
        "source": "hub.env",
        "problem": None,
    }
    name = active_name()
    if name is None:
        return target
    try:
        boards = load_boards()
    except BoardError as e:
        target["problem"] = f"board {name!r} chosen, but {e.message} — using hub.env"
        return target
    board = boards.get(name)
    if board is None:
        target["problem"] = (
            f"board {name!r} chosen, but it is not in the board table — using hub.env"
        )
        return target
    target.update(com=board.serial_com, baud=board.serial_baud, board=name, source="board")
    return target


def board_view() -> dict[str, Any]:
    """The table, the chosen board and where the serial forwarder points —
    for `alb bench board` and GET /api/bench/board. A broken table is
    reported, not raised: this is what someone looks at to find out why."""
    from alb.remote.forwarder import existing_serial_forwarder

    target = serial_target()
    live = existing_serial_forwarder()
    com, baud = live.target if live is not None else (target["com"], target["baud"])
    try:
        boards = [b.as_dict() for b in load_boards().values()]
        problem = target["problem"]
    except BoardError as e:
        boards, problem = [], e.message
    return {
        "table": str(boards_path()) if boards_path() else None,
        "boards": boards,
        "active": target["board"],
        "source": target["source"],
        "problem": problem,
        "serial": {
            "com": com or None,
            "baud": baud,
            "bound": bool(live is not None and live.is_bound),
            "readers": live.session_refs if live is not None else 0,
        },
    }


async def use_board(name: str, *, force: bool = False) -> dict[str, Any]:
    """Point the serial console at board `name` and remember the choice.

    Refused while a job runs (its timeline reads this console) and while
    anyone reads the console, unless `force` — then those readers are cut
    off and counted. The hub keeps running; adb and fastboot are not
    touched."""
    boards = load_boards()
    if not boards:
        raise BoardError(
            "BENCH_BOARDS_NOT_CONFIGURED",
            f"this hub has no board table — set {BOARDS_ENV} in deploy/hub/hub.env "
            "to a boards TOML file and restart alb-api",
        )
    board = boards.get(name)
    if board is None:
        raise BoardError(
            "BENCH_BOARD_UNKNOWN", f"no board {name!r}; the table has: {', '.join(sorted(boards))}"
        )

    from alb.remote import forwarder as fwd
    from alb.remote.flash import FlashBusyError, get_flash_service

    before = board_view()["serial"]
    try:
        async with get_flash_service().held(f"switching the bench board to {name}"):
            live = fwd.existing_serial_forwarder()
            readers = 0
            if live is not None:
                try:
                    readers = await live.retarget(board.serial_com, board.serial_baud, force=force)
                except fwd.SerialBusyError as e:
                    raise BoardError(
                        "BENCH_SERIAL_BUSY",
                        f"{e.readers} reader(s) on the serial console (capture, shell, web "
                        "console) — end them, or pass force to cut them off",
                    ) from None
            save_active(name)
    except FlashBusyError as e:
        raise BoardError(
            "FASTBOOT_BUSY",
            f"another job is running ({e.job}) — its record reads this serial console; "
            "switch after it ends",
        ) from None
    bound = await fwd.attach_serial_if_agent()
    _log.info(
        "bench board -> %s (%s @ %d); was %s @ %s; %d reader(s) cut off",
        name,
        board.serial_com,
        board.serial_baud,
        before["com"],
        before["baud"],
        readers,
    )
    return {
        "board": board.as_dict(),
        "previous": {"com": before["com"], "baud": before["baud"]},
        "cut_off": readers,
        "bound": bound,
    }


# ── per-board flash allowlists (ADR-063 phase 2) ──────────────────────────


def current_board() -> str | None:
    """The chosen board when it is in the table, else None. Sent with every
    job so the agent applies that board's partition list and identity check."""
    board = serial_target()["board"]
    return board if isinstance(board, str) else None


def parse_agent_boards(raw: Any) -> dict[str, dict[str, Any]]:
    """The `boards` field of an agent's hello, defensively.

    {board: {"partitions": [names] | None, "product": str | None}}. None
    means "not configured for that board" — it is never "any". Anything
    malformed is dropped; an agent that sent nothing has no boards."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for name, entry in raw.items():
        if not isinstance(name, str) or not _NAME_RE.match(name) or not isinstance(entry, dict):
            continue
        parts = entry.get("partitions")
        product = entry.get("product")
        out[name] = {
            "partitions": [str(p) for p in parts] if isinstance(parts, list) else None,
            "product": product if isinstance(product, str) and product else None,
        }
    return out


@dataclass(frozen=True)
class PartitionList:
    """Which partitions the connected agent accepts for the chosen board.

    `names` empty means "any well-formed name" — and only when `problem` is
    "". A `problem` means no partition op can run; callers must check it
    first, because the empty `names` that comes with it reads as "any"."""

    names: list[str]
    problem: str = ""
    # The agent.conf key the list comes from, for "add it to …" hints.
    key: str = "flash_partitions"
    # The board whose list this is; None for an agent with one list for all.
    board: str | None = None


def partition_list(agent: Any, board: str | None) -> PartitionList:
    """The one place the hub decides which list applies (ADR-063 phase 2).

    An agent with no per-board lists: its single `flash_partitions`, as
    before. With per-board lists: the chosen board's list, or a problem when
    no board is chosen or the agent has no list for it. The agent checks the
    same thing again; this is so a flash plan is refused before row 1, and
    a picker offers the names that will be accepted."""
    if agent is None:
        return PartitionList([])
    boards = getattr(agent, "boards", None) or {}
    listed = sorted(b for b, e in boards.items() if e.get("partitions") is not None)
    if not listed:
        return PartitionList(list(getattr(agent, "flash_partitions", []) or []))
    key = f"flash_partitions.{board}" if board else "flash_partitions.<board>"
    if board is None:
        return PartitionList(
            [],
            f"the agent keeps a partition list per board ({', '.join(listed)}) and no bench "
            "board is chosen — `alb bench board use <name>`",
            key,
            board,
        )
    names = boards.get(board, {}).get("partitions")
    if names is None:
        return PartitionList(
            [],
            f"the agent has no partition list for board {board!r} (it has: {', '.join(listed)})"
            f" — add {key}=… to agent.conf on the bench host",
            key,
            board,
        )
    if not names:
        # The agent refuses to start with one; read as "any" it would undo
        # the whole point of the per-board lists.
        return PartitionList([], f"the agent's list for board {board!r} is empty", key, board)
    return PartitionList(list(names), "", key, board)
