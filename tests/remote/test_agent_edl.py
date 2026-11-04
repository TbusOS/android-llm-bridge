"""The agent's Qualcomm EDL / 9008 ops (ADR-066): edl-sahara, edl-program,
edl-reset.

The vendor tools are replaced by one fake script that prints what the real
tools printed on the bench (tests/remote/edl_outputs/, recorded 2026-11-03,
paths and serial numbers replaced) and, like the real tools, opens the files
it is given relative to its working directory. So these tests run the real
subprocess path of the agent — argv, working directory, output relay,
success lines, exit codes, timeout kill — with only the board missing.

ALB_AGENT_UNDER_TEST points the module at another copy of alb_agent.py: the
reverse runs feed it deliberately weakened copies, each of which must make
at least one test here fail.
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

_DEFAULT_AGENT = Path(__file__).resolve().parents[2] / "clients" / "windows-agent" / "alb_agent.py"


def _load_agent() -> Any:
    path = Path(os.environ.get("ALB_AGENT_UNDER_TEST") or _DEFAULT_AGENT)
    spec = importlib.util.spec_from_file_location("alb_agent_edl_under_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


agent = _load_agent()

_OUTPUTS = Path(__file__).resolve().parent / "edl_outputs"
_HDR = struct.Struct(">cI")

# The real tool printed CRLF lines; the fake prints the recorded bytes as-is.
_FAKE_TOOL = r"""
import json, os, sys, time
argv = sys.argv[1:]
log = os.environ.get("FAKE_EDL_LOG")
if log:
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"argv": argv, "cwd": os.getcwd(), "pid": os.getpid()}) + "\n")
time.sleep(float(os.environ.get("FAKE_EDL_SLEEP") or 0))
# Open what the real tool opens, relative to the working directory: the
# programmer for Sahara, each --sendxml entry for fh_loader.
need = []
if "-s" in argv:
    need.append(argv[argv.index("-s") + 1].split(":", 1)[1])
for a in argv:
    if a.startswith("--sendxml="):
        need += a.split("=", 1)[1].split(",")
for name in need:
    if not os.path.isfile(name):
        sys.stdout.write("ERROR: could not find file %r\r\n" % name)
        sys.stdout.flush()
        sys.exit(1)
if any(a == "--reset" or a.startswith("--sendxml=") for a in argv):
    with open("port_trace.txt", "w") as fh:  # fh_loader writes this into its cwd
        fh.write("trace\n")
out = os.environ.get("FAKE_EDL_OUTPUT")
if out:
    sys.stdout.buffer.write(open(out, "rb").read())
extra = os.environ.get("FAKE_EDL_EXTRA_HEX")
if extra:
    sys.stdout.buffer.write(bytes.fromhex(extra))
sys.stdout.flush()
sys.exit(int(os.environ.get("FAKE_EDL_RC") or 0))
"""

# What Win32_PnPEntity gives for a board in 9008 next to the board's debug
# console (a USB serial adapter). The QDLoader name with "(COM53)" is what
# `alb bench usb` showed on the bench (2026-11-03).
_EDL_PNP = {
    "Name": "Qualcomm HS-USB QDLoader 9008 (COM53)",
    "PNPDeviceID": "USB\\VID_05C6&PID_9008\\5&1A2B3C4D&0&3",
    "Status": "OK",
    "ConfigManagerErrorCode": 0,
    "PNPClass": "Ports",
    "Service": "qcusbser",
    "Manufacturer": "Qualcomm Incorporated",
    "CompatibleID": ["USB\\Class_ff&SubClass_ff&Prot_ff"],
}
_CONSOLE_PNP = {
    "Name": "USB Serial Port (COM27)",
    "PNPDeviceID": "USB\\VID_0403&PID_6001\\A50285BI",
    "Status": "OK",
    "ConfigManagerErrorCode": 0,
    "PNPClass": "Ports",
    "Service": "FTSER2K",
    "Manufacturer": "FTDI",
    "CompatibleID": None,
}


def _exists(path: str | Path) -> bool:
    return Path(path).exists()


def _usb(*entries: dict[str, Any]) -> list[dict[str, Any]]:
    """Through the agent's real parser, as on the bench."""
    devices: list[dict[str, Any]] = agent._parse_usb_json(json.dumps(list(entries)))
    return devices


class _Ws:
    def __init__(self, req: dict[str, Any]) -> None:
        payload = json.dumps(req).encode()
        self._data = _HDR.pack(b"C", len(payload)) + payload
        self._sent = False
        self.sent: list[dict[str, Any]] = []

    async def recv(self) -> bytes:
        if self._sent:
            return b""
        self._sent = True
        return self._data

    async def send(self, data: bytes) -> None:
        kind, length = _HDR.unpack(data[:5])
        assert kind == b"C"
        self.sent.append(json.loads(data[5 : 5 + length]))

    def done(self) -> dict[str, Any]:
        msgs = [m for m in self.sent if m.get("ev") == "done"]
        assert msgs, f"no done frame; got {self.sent}"
        return msgs[-1]

    def progress(self) -> list[str]:
        return [m["text"] for m in self.sent if m.get("ev") == "progress"]


@dataclass
class _Bench:
    root: Path  # edl_root
    pkg: Path  # the one unpacked package under it
    tmp: Path
    exe: dict[str, Path]  # the fake tools
    log: Path  # one JSON line per tool run: argv, cwd, pid
    usb: list[list[dict[str, Any]]]  # [0] = what the USB listing returns
    ops: dict[str, Any]
    monkeypatch: pytest.MonkeyPatch

    def calls(self) -> list[dict[str, Any]]:
        if not self.log.exists():
            return []
        return [json.loads(ln) for ln in self.log.read_text(encoding="utf-8").splitlines()]

    def output(self, name: str, rc: int = 0) -> None:
        """What the next tool run prints (a recorded output) and its exit code."""
        self.monkeypatch.setenv("FAKE_EDL_OUTPUT", str(_OUTPUTS / name))
        self.monkeypatch.setenv("FAKE_EDL_RC", str(rc))


@pytest.fixture
def bench(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Bench:
    """An edl_root with one unpacked package, the fake tools, a 9008 board on COM53."""
    root = tmp_path / "packages"
    pkg = root / "board-a" / "fac"
    pkg.mkdir(parents=True)
    for name in ("prog_firehose_ddr.elf", "rawprogram0_develop.xml", "patch0.xml", "abl.elf"):
        (pkg / name).write_bytes(b"x")
    tools = tmp_path / "tools"
    tools.mkdir()
    exe: dict[str, Path] = {}
    for name in ("QSaharaServer", "fh_loader"):
        path = tools / name
        path.write_text(f"#!{sys.executable}\n{_FAKE_TOOL}", encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        exe[name] = path
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_EDL_LOG", str(log))
    for var in ("FAKE_EDL_OUTPUT", "FAKE_EDL_RC", "FAKE_EDL_SLEEP", "FAKE_EDL_EXTRA_HEX"):
        monkeypatch.delenv(var, raising=False)
    usb = [_usb(_CONSOLE_PNP, _EDL_PNP)]

    async def fake_usb(_timeout_s: float) -> list[dict[str, Any]]:
        return usb[0]

    monkeypatch.setattr(agent, "_edl_usb_devices", fake_usb)
    tools_cfg, problem = agent._resolve_edl(
        str(exe["QSaharaServer"]), str(exe["fh_loader"]), str(root)
    )
    assert problem == "" and tools_cfg is not None
    ops = agent._build_ops("", {}, tools_cfg)
    return _Bench(root, pkg, tmp_path, exe, log, usb, ops, monkeypatch)


async def _run(bench: _Bench, **req: Any) -> _Ws:
    ws = _Ws(req)
    await agent._run_job(ws, bench.ops, hint=req["op"])
    return ws


# ── the table ───────────────────────────────────────────────────────────


def test_no_edl_rows_without_edl_config() -> None:
    assert not agent._EDL_OP_NAMES & set(agent._build_ops("/opt/fastboot", {}))


def test_edl_rows_and_what_they_tell_the_hub(bench: _Bench) -> None:
    rows = {n: s for n, s in bench.ops.items() if n.startswith("edl-")}
    assert list(rows) == ["edl-sahara", "edl-program", "edl-reset"]
    for spec in rows.values():
        d = spec.describe()
        assert (d["group"], d["needs"], d["record"]) == ("edl", "edl-device", "events")
        assert d["mutates"] and d["exclusive"] and not d["image"]
        assert spec.builtin is not None and spec.argv == ()
        json.dumps(d)
    assert [p.name for p in rows["edl-sahara"].params] == ["dir", "programmer", "port"]
    assert [p.name for p in rows["edl-program"].params] == ["dir", "xml", "memory", "port"]
    assert [p.name for p in rows["edl-reset"].params] == ["port"]
    assert rows["edl-program"].params[2].default == "emmc"
    # under the hub's 30-minute job limit, so the agent's own kill comes first
    assert rows["edl-program"].timeout_s < 30 * 60


def test_the_hub_reads_the_rows_and_applies_the_same_shape_rules(bench: _Bench) -> None:
    from alb.remote.bench_ops import check_params, parse_ops

    table = parse_ops([s.describe() for s in bench.ops.values()])
    assert table is not None
    prog = table["edl-program"]
    ok, code, problem = check_params(prog, {"dir": "board-a/fac", "xml": "a.xml,patch0.xml"}, [])
    assert (code, problem) == ("", "") and ok["memory"] == "emmc"
    for bad in ({"dir": "../x", "xml": "a.xml"}, {"dir": "pkg", "xml": "a.xml,../b.xml"}):
        _, code, problem = check_params(prog, bad, [])
        assert code == "BENCH_PARAM_REJECTED" and problem, bad


@pytest.mark.parametrize(
    ("sahara", "firehose", "root", "problem"),
    [
        ("", "", "", ""),
        ("S", "", "", "edl_firehose, edl_root not set"),
        ("S", "F", "missing-dir", "edl_root"),
        ("missing", "F", "R", "edl_sahara"),
    ],
)
def test_resolve_edl_says_why_the_rows_are_absent(
    tmp_path: Path, sahara: str, firehose: str, root: str, problem: str
) -> None:
    real = {"S": tmp_path / "s.exe", "F": tmp_path / "f.exe", "R": tmp_path}
    for key in ("S", "F"):
        real[key].write_text("")

    def val(v: str) -> str:
        return str(real[v]) if v in real else (str(tmp_path / v) if v else "")

    tools, why = agent._resolve_edl(val(sahara), val(firehose), val(root))
    assert tools is None
    assert problem in why if problem else why == ""


def test_agent_conf_keys_reach_the_table_and_hello(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bench: _Bench
) -> None:
    conf = tmp_path / "agent.conf"
    conf.write_text(
        "hub_url=ws://h/agent/connect\n"
        f"edl_sahara={bench.exe['QSaharaServer']}\n"
        f"edl_firehose={bench.exe['fh_loader']}\n"
        f"edl_root={bench.root}\n",
        encoding="utf-8",
    )
    for name in (
        "_OPS",
        "_FASTBOOT_PATH",
        "_FLASH_PARTITIONS",
        "_BOARD_PARTITIONS",
        "_BOARD_PRODUCT",
        "_EDL",
        "_EDL_PROBLEM",
    ):
        monkeypatch.setattr(agent, name, getattr(agent, name))
    monkeypatch.setattr(agent, "_AGENT_LOG_PATH", None)
    agent._apply_flash_config(agent._parse_args(["--config", str(conf)]))
    hello = json.loads(agent._hello("a", "n", None))
    assert {"edl-sahara", "edl-program", "edl-reset"} <= {op["name"] for op in hello["ops"]}
    assert agent._EDL_PROBLEM == ""


# ── parameter shapes (agent side) ───────────────────────────────────────


@pytest.mark.parametrize("value", ["board-a/fac", "board-a\\fac", "pkg", "my pkg_v1.2"])
def test_dir_shapes_accepted(bench: _Bench, value: str) -> None:
    assert bench.ops["edl-program"].params[0].resolve({"dir": value}) == value


@pytest.mark.parametrize(
    "value",
    ["../x", "a/../b", "/etc", "C:\\x", "\\\\server\\share", "a//b", ".hidden", "a\nb", ""],
)
def test_dir_shapes_rejected(bench: _Bench, value: str) -> None:
    with pytest.raises(agent._JobRejected) as exc_info:
        bench.ops["edl-program"].params[0].resolve({"dir": value})
    assert exc_info.value.code == "BENCH_PARAM_REJECTED"


@pytest.mark.parametrize("value", ["a.xml,../b.xml", "a.xml;b.xml", "a.txt", "dir/a.xml", "a.xml,"])
def test_xml_shapes_rejected(bench: _Bench, value: str) -> None:
    with pytest.raises(agent._JobRejected):
        bench.ops["edl-program"].params[1].resolve({"xml": value})


@pytest.mark.parametrize("value", ["COM0", "com53", "COM53 ", "COM1000", "\\\\.\\COM53"])
def test_port_shapes_rejected(bench: _Bench, value: str) -> None:
    with pytest.raises(agent._JobRejected):
        bench.ops["edl-reset"].params[0].resolve({"port": value})


# ── package directory: inside edl_root, existing ────────────────────────


async def test_a_link_inside_the_root_that_points_outside_is_refused(bench: _Bench) -> None:
    outside = bench.tmp / "elsewhere"
    outside.mkdir()
    (outside / "prog_firehose_ddr.elf").write_bytes(b"x")
    (bench.root / "escape").symlink_to(outside, target_is_directory=True)
    ws = await _run(bench, op="edl-sahara", dir="escape")
    assert ws.done()["code"] == "EDL_PACKAGE_REJECTED"
    assert "outside edl_root" in ws.done()["error"]
    assert bench.calls() == [], "nothing may run for a refused package"


async def test_a_missing_dir_or_xml_is_refused_before_any_tool_runs(bench: _Bench) -> None:
    ws = await _run(bench, op="edl-sahara", dir="board-b/fac")
    assert ws.done()["code"] == "EDL_PACKAGE_REJECTED"
    ws = await _run(
        bench, op="edl-program", dir="board-a/fac", xml="rawprogram0_develop.xml,nope.xml"
    )
    assert ws.done()["code"] == "EDL_PACKAGE_REJECTED" and "nope.xml" in ws.done()["error"]
    assert bench.calls() == []


async def test_programmer_defaults_to_the_only_one_and_refuses_a_guess(bench: _Bench) -> None:
    (bench.pkg / "prog_firehose_lite.elf").write_bytes(b"x")
    ws = await _run(bench, op="edl-sahara", dir="board-a/fac")
    assert ws.done()["code"] == "EDL_PACKAGE_REJECTED"
    assert "prog_firehose_ddr.elf, prog_firehose_lite.elf" in ws.done()["error"]
    bench.output("sahara_ok.txt")
    ws = await _run(bench, op="edl-sahara", dir="board-a/fac", programmer="prog_firehose_lite.elf")
    assert ws.done()["ok"], ws.done()
    assert bench.calls()[-1]["argv"][-1] == "13:prog_firehose_lite.elf"


# ── the 9008 port ───────────────────────────────────────────────────────


def test_only_05c6_9008_devices_give_a_port() -> None:
    ports, portless = agent._edl_ports(_usb(_CONSOLE_PNP, _EDL_PNP))
    assert (ports, portless) == (["COM53"], [])


@pytest.mark.parametrize(
    ("devices", "requested", "code", "words"),
    [
        ([_CONSOLE_PNP], "", "EDL_NO_DEVICE", "no board in 9008"),
        ([{**_EDL_PNP, "Name": "QUSB_BULK"}], "", "EDL_NO_DEVICE", "driver"),
        (
            [_EDL_PNP, {**_EDL_PNP, "Name": "Qualcomm HS-USB QDLoader 9008 (COM9)"}],
            "",
            "EDL_PORT_AMBIGUOUS",
            "COM9, COM53",
        ),
        ([_CONSOLE_PNP, _EDL_PNP], "COM27", "EDL_NO_DEVICE", "not a 9008 port"),
    ],
)
async def test_port_problems_are_refused_and_named(
    bench: _Bench, devices: list[dict[str, Any]], requested: str, code: str, words: str
) -> None:
    bench.usb[0] = _usb(*devices)
    ws = await _run(bench, op="edl-reset", port=requested)
    assert ws.done()["code"] == code
    assert words in ws.done()["error"]
    assert bench.calls() == []


async def test_port_picks_among_several_9008_boards(bench: _Bench) -> None:
    bench.usb[0] = _usb(_EDL_PNP, {**_EDL_PNP, "Name": "Qualcomm HS-USB QDLoader 9008 (COM9)"})
    bench.output("reset_ok.txt")
    ws = await _run(bench, op="edl-reset", port="COM9")
    assert ws.done()["ok"], ws.done()
    assert bench.calls()[-1]["argv"][0] == "--port=\\\\.\\COM9"


# ── running the tools: argv, cwd, success lines, output ─────────────────


async def test_sahara_runs_like_the_bench_did(bench: _Bench) -> None:
    bench.output("sahara_ok.txt")
    ws = await _run(bench, op="edl-sahara", dir="board-a/fac")
    done = ws.done()
    assert done["ok"], done
    (call,) = bench.calls()
    # 2026-11-03 by hand: QSaharaServer.exe -p \\.\COM53 -s 13:prog_firehose_ddr.elf, in the package
    assert call["argv"] == ["-p", "\\\\.\\COM53", "-s", "13:prog_firehose_ddr.elf"]
    assert Path(call["cwd"]) == bench.pkg.resolve()
    assert done["data"]["port"] == "COM53" and done["data"]["programmer"] == "prog_firehose_ddr.elf"
    assert "Sahara protocol completed" in done["stdout"]
    assert any("Sahara protocol completed" in t for t in ws.progress()), "output relayed live"


async def test_program_runs_like_the_bench_did(bench: _Bench) -> None:
    bench.output("firehose_full_head_tail_ok.txt")
    ws = await _run(
        bench, op="edl-program", dir="board-a/fac", xml="rawprogram0_develop.xml,patch0.xml"
    )
    done = ws.done()
    assert done["ok"], done
    (call,) = bench.calls()
    assert call["argv"] == [
        "--port=\\\\.\\COM53",
        "--sendxml=rawprogram0_develop.xml,patch0.xml",
        f"--search_path={bench.pkg.resolve()}",
        "--memoryname=emmc",
        "--noprompt",
    ]
    assert Path(call["cwd"]) == bench.pkg.resolve()
    # zero.bin partial-image notes are normal for a factory package: reported, not failed
    assert done["data"]["warnings"] is True
    assert done["data"]["xml"] == ["rawprogram0_develop.xml", "patch0.xml"]
    assert "{All Finished Successfully}" in done["stdout"]
    assert len(done["stdout"].splitlines()) <= agent._EDL_TAIL_LINES


async def test_reset_runs_in_a_scratch_dir_that_is_removed(bench: _Bench) -> None:
    bench.output("reset_ok.txt")
    ws = await _run(bench, op="edl-reset")
    assert ws.done()["ok"], ws.done()
    (call,) = bench.calls()
    assert call["argv"] == ["--port=\\\\.\\COM53", "--reset", "--noprompt"]
    assert not _exists(call["cwd"]), "port_trace.txt and its directory are cleaned up"
    assert not _exists(bench.pkg / "port_trace.txt")


async def test_exit_0_without_the_success_lines_is_a_failure(bench: _Bench) -> None:
    bench.output("reset_ok.txt")  # fh_loader's lines, not Sahara's
    ws = await _run(bench, op="edl-sahara", dir="board-a/fac")
    done = ws.done()
    assert not done["ok"] and done["code"] == "EDL_SAHARA_FAILED"
    assert "exit code 0 without" in done["error"]


async def test_an_xml_name_that_is_a_directory_is_refused(bench: _Bench) -> None:
    (bench.pkg / "rawprogram0_develop.xml").unlink()
    (bench.pkg / "rawprogram0_develop.xml").mkdir()
    ws = await _run(bench, op="edl-program", dir="board-a/fac", xml="rawprogram0_develop.xml")
    assert ws.done()["code"] == "EDL_PACKAGE_REJECTED"
    assert bench.calls() == []


async def test_tool_failure_keeps_the_tools_output_in_the_done_frame(
    bench: _Bench, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench.output("firehose_abl_ok.txt", rc=1)
    monkeypatch.setenv("FAKE_EDL_EXTRA_HEX", b"ERROR: target did not answer\r\n".hex())
    ws = await _run(bench, op="edl-program", dir="board-a/fac", xml="rawprogram0_develop.xml")
    done = ws.done()
    assert (done["ok"], done["code"], done["rc"]) == (False, "EDL_FIREHOSE_FAILED", 1)
    assert "ERROR: target did not answer" in done["error"]
    assert "ERROR: target did not answer" in done["stdout"]


async def test_tool_output_in_the_console_code_page_is_decoded(
    bench: _Bench, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench.output("reset_ok.txt", rc=1)
    monkeypatch.setenv(
        "FAKE_EDL_EXTRA_HEX", "4552524f523a20d5d2b2bbb5bd0d0a"
    )  # "ERROR: 找不到" in GBK
    ws = await _run(bench, op="edl-reset")
    assert "ERROR: 找不到" in ws.done()["error"]


async def test_a_hung_tool_is_killed_at_the_timeout(
    bench: _Bench, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench.output("sahara_ok.txt")
    monkeypatch.setenv("FAKE_EDL_SLEEP", "30")
    monkeypatch.setattr(agent, "_EDL_SAHARA_TIMEOUT_S", 0.5)
    ws = await _run(bench, op="edl-sahara", dir="board-a/fac")
    done = ws.done()
    assert done["code"] == "EDL_SAHARA_FAILED" and "killed after" in done["stdout"]
    (call,) = bench.calls()
    with pytest.raises(ProcessLookupError):
        os.kill(call["pid"], 0)


async def test_an_edl_op_on_an_agent_without_edl_tools_says_what_to_configure() -> None:
    ws = _Ws({"op": "edl-sahara", "dir": "x"})
    await agent._run_job(ws, agent._build_ops("", {}), hint="edl-sahara")
    done = ws.done()
    assert done["code"] == "BENCH_OP_UNKNOWN" and "edl_root" in done["error"]
