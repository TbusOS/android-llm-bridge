"""The hub side of vendor-tool runs (ADR-068).

The `argv` parameter kind, the codes the hub uses for tool-run's params, and
the files a tool wrote coming back into the job's record directory. The last
part runs the hub's FlashService against the REAL agent code in this process
— the two ends joined by an in-memory pipe, a fake vendor tool on the far
side — so what is pinned is the frame format both ends actually speak.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

from alb.remote.bench_ops import ParamSpec, check_params, parse_ops
from alb.remote.flash import FlashService, _label
from alb.remote.flash_timeline import FlashTimeline

_AGENT_PATH = Path(__file__).resolve().parents[2] / "clients" / "windows-agent" / "alb_agent.py"


def _load_agent() -> Any:
    spec = importlib.util.spec_from_file_location("alb_agent_tool_hub_test", _AGENT_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


agent = _load_agent()


# ── the argv kind ───────────────────────────────────────────────────────

_ARGS = ParamSpec("args", "argv", required=False, default=[], max_items=3, max_len=8)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (["-p", "x"], ["-p", "x"]),
        ('["-p", "x"]', ["-p", "x"]),  # `alb bench run tool-run args='[...]'`
        ([], []),
        (None, []),
        ("", []),
        (["", "a b"], ["", "a b"]),
    ],
)
def test_argv_values_accepted(value: Any, expected: list[str]) -> None:
    assert _ARGS.check(value, []) == (expected, "")


@pytest.mark.parametrize(
    ("value", "problem"),
    [
        ("-p x", "JSON list"),
        ('{"a": 1}', "list of strings"),
        ([1], "list of strings"),
        (["a", "b", "c", "d"], "at most 3"),
        (["123456789"], "longer than 8"),
        (["a\tb"], "control character"),
        (["a\x00"], "control character"),
    ],
)
def test_argv_values_refused(value: Any, problem: str) -> None:
    normalised, why = _ARGS.check(value, [])
    assert normalised is None and problem in why


def _agent_table(tmp_path: Path) -> dict[str, Any]:
    exe = tmp_path / "tool.exe"
    exe.write_text("")
    tools, problems = agent._resolve_tools(
        {"vt": {"tool_exe": str(exe), "tool_root": str(tmp_path)}}
    )
    assert not problems
    table: dict[str, Any] = agent._build_ops("", {}, None, tools)
    return table


def test_the_hub_reads_the_agent_rows_and_uses_the_agent_codes(tmp_path: Path) -> None:
    table = parse_ops([s.describe() for s in _agent_table(tmp_path).values()])
    assert table is not None
    run = table["tool-run"]
    assert run.param("args") is not None and run.param("args").max_items == 64  # type: ignore[union-attr]
    ok, code, problem = check_params(run, {"tool": "vt", "args": ["getvar", "product"]}, [])
    assert (code, problem) == ("", "") and ok["args"] == ["getvar", "product"]
    for params, want in (
        ({"tool": "nope"}, "BENCH_TOOL_UNKNOWN"),
        ({"tool": "vt", "pkg": "../x"}, "BENCH_TOOL_PACKAGE_REJECTED"),
        ({"tool": "vt", "args": ["a\nb"]}, "BENCH_TOOL_ARG_REJECTED"),
        ({"tool": "vt", "port": "COM0"}, "BENCH_TOOL_ARG_REJECTED"),
    ):
        _, code, problem = check_params(run, params, [])
        assert code == want and problem, params


def test_the_record_directory_name_leaves_the_arguments_out() -> None:
    label = _label("tool-run", {"tool": "vt", "pkg": "board-a/v1", "args": ["-p", "{com}"]})
    assert label == "tool-run vt board-a/v1"


# ── keeping the files ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name",
    ["", "../x", "a/../b", "/abs", "a//b", ".", "C:x", "a\\b", "line\nbreak", "logs/"],
)
def test_file_names_the_hub_will_not_write(tmp_path: Path, name: str) -> None:
    tl = FlashTimeline(tmp_path)
    try:
        assert tl.save_file_piece(name, 0, b"x", end=True) is False
    finally:
        asyncio.run(tl.aclose())
    assert not (tmp_path / "files").exists() or not any((tmp_path / "files").rglob("*"))


def test_pieces_are_appended_in_order_only(tmp_path: Path) -> None:
    tl = FlashTimeline(tmp_path)
    try:
        assert tl.save_file_piece("logs/a.txt", 0, b"abc", end=False)
        assert tl.save_file_piece("logs/a.txt", 5, b"zz", end=False) is False  # a gap
        assert tl.save_file_piece("logs/a.txt", 3, b"de", end=True)
    finally:
        asyncio.run(tl.aclose())
    assert (tmp_path / "files" / "logs" / "a.txt").read_bytes() == b"abcde"
    events = [json.loads(ln) for ln in (tmp_path / "timeline.jsonl").read_text().splitlines()]
    assert [e for e in events if e.get("ev") == "file"] == [
        {
            "t": events[-1]["t"],
            "src": "meta",
            "ev": "file",
            "name": "logs/a.txt",
            "size": 5,
            "path": "files/logs/a.txt",
        }
    ]


# ── end to end: FlashService ⇄ the real agent code ──────────────────────

_FAKE_TOOL = r"""
import os, sys
with open("tool.log", "w") as fh:
    fh.write("detail for " + " ".join(sys.argv[1:]) + "\n")
print("writing system")
print("part system: ok")
print("FAILED" if os.environ.get("FAKE_FAIL") else "all done")
sys.exit(0)
"""


class _End:
    """One end of an in-memory duplex pipe: what one side sends, the other
    receives; b"" is end-of-stream."""

    def __init__(self, inbox: asyncio.Queue[bytes], outbox: asyncio.Queue[bytes]) -> None:
        self._in, self._out = inbox, outbox

    async def send(self, data: bytes) -> None:
        await self._out.put(bytes(data))

    async def recv(self) -> bytes:
        return await self._in.get()

    async def aclose(self) -> None:
        await self._out.put(b"")


class _LiveAgent:
    """What the hub sees as the connected agent; each job channel is served
    by the agent module's own _run_job."""

    def __init__(self, ops: dict[str, Any]) -> None:
        self.agent_id = "agent-1"
        self.name = "bench"
        self.caps = ["adb"]
        self.flash_partitions: list[str] = []
        self.ops = ops
        self.op_table = parse_ops([s.describe() for s in ops.values()])
        self.ops_reported = True
        self.tasks: list[asyncio.Task[None]] = []

    async def open_data_channel(
        self,
        *,
        ctype: str,
        role: str,
        params: dict[str, Any],
        timeout: float,  # noqa: ASYNC109 — the hub's call signature
    ) -> _End:
        to_agent: asyncio.Queue[bytes] = asyncio.Queue()
        to_hub: asyncio.Queue[bytes] = asyncio.Queue()
        hub_end, agent_end = _End(to_hub, to_agent), _End(to_agent, to_hub)

        async def serve() -> None:
            try:
                await agent._run_job(agent_end, self.ops, hint=params["op"])
            finally:
                await agent_end.aclose()

        self.tasks.append(asyncio.create_task(serve()))
        return hub_end


@pytest.fixture
def live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _LiveAgent:
    if sys.platform == "win32":
        pytest.skip("the fake tool is a #! script")
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.delenv("FAKE_FAIL", raising=False)
    root = tmp_path / "packages"
    (root / "board-a" / "v1").mkdir(parents=True)
    exe = tmp_path / "vendortool"
    exe.write_text(f"#!{sys.executable}\n{_FAKE_TOOL}", encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    tools, _ = agent._resolve_tools(
        {
            "vt": {
                "tool_exe": str(exe),
                "tool_root": str(root),
                "tool_ok": "all done",
                "tool_fail": "FAILED",
                "tool_note": "^part ",
            }
        }
    )
    return _LiveAgent(agent._build_ops("", {}, None, tools))


async def test_a_tool_run_end_to_end(live: _LiveAgent) -> None:
    events: list[Any] = []
    svc = FlashService(lambda: live)
    result = await svc.run_op(
        "tool-run",
        {"tool": "vt", "pkg": "board-a/v1", "args": ["-p", "x y"]},
        on_event=events.append,
    )
    await asyncio.gather(*live.tasks)
    assert result.ok, result.as_dict()
    assert [e.text for e in events] == ["writing system", "part system: ok", "all done"]
    assert result.data["judged_by"] == "exit_code+ok_pattern"
    assert result.data["highlights"] == ["part system: ok"]
    record = Path(result.artifacts)
    assert record.name.startswith("tool-run-vt-board-a-v1-")
    assert Path(result.data["files_dir"]) == record / "files"
    assert (record / "files" / "tool.log").read_text() == "detail for -p x y\n"
    timeline = [json.loads(ln) for ln in (record / "timeline.jsonl").read_text().splitlines()]
    assert timeline[0]["args"] == ["-p", "x y"], "the full request is in the record"
    assert [e["name"] for e in timeline if e.get("ev") == "file"] == ["tool.log"]
    job = json.loads((record / "job.json").read_text())
    assert job["data"]["files"][0]["name"] == "tool.log"


async def test_a_failed_run_still_brings_its_files_and_says_who_judged(
    live: _LiveAgent, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_FAIL", "1")
    result = await FlashService(lambda: live).run_op(
        "tool-run", {"tool": "vt", "pkg": "board-a/v1"}
    )
    await asyncio.gather(*live.tasks)
    assert (result.ok, result.code) == (False, "BENCH_TOOL_FAILED")
    assert result.data["judged_by"] == "fail_pattern"
    assert (Path(result.artifacts) / "files" / "tool.log").is_file()


async def test_tool_list_through_the_hub(live: _LiveAgent) -> None:
    result = await FlashService(lambda: live).run_op("tool-list", {})
    await asyncio.gather(*live.tasks)
    assert result.ok and [t["name"] for t in result.data["tools"]] == ["vt"]
    assert result.artifacts == "", "a listing is not recorded"


async def test_the_hub_refuses_a_bad_argument_without_a_channel(live: _LiveAgent) -> None:
    result = await FlashService(lambda: live).run_op("tool-run", {"tool": "vt", "args": ["a\nb"]})
    assert result.code == "BENCH_TOOL_ARG_REJECTED"
    assert live.tasks == []


async def test_an_agent_without_tools_is_answered_with_how_to_register() -> None:
    lonely = _LiveAgent(agent._build_ops("", {}))
    result = await FlashService(lambda: lonely).run_op("tool-run", {"tool": "vt"})
    assert result.code == "BENCH_OP_UNKNOWN" and "tool_exe.<name>" in result.error
