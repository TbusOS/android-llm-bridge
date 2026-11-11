"""The agent's vendor-tool ops (ADR-068): tool-run and tool-list.

A vendor download-mode tool is registered in agent.conf and run with the
caller's own arguments. The tool here is a fake Python script that does what
the environment tells it — print given bytes, exit with a code, sleep, write
files into its working directory, start a child that keeps its output open —
so these tests run the agent's real subprocess path: argv without a shell,
working directory, output relay and decoding, the three-way verdict, the
timeout that ends the whole process tree, and the files brought back.

ALB_AGENT_UNDER_TEST points the module at another copy of alb_agent.py: the
reverse runs feed it deliberately weakened copies, each of which must make
at least one test here fail.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import stat
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any

import pytest

_DEFAULT_AGENT = Path(__file__).resolve().parents[2] / "clients" / "windows-agent" / "alb_agent.py"


def _load_agent() -> Any:
    path = Path(os.environ.get("ALB_AGENT_UNDER_TEST") or _DEFAULT_AGENT)
    spec = importlib.util.spec_from_file_location("alb_agent_tools_under_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


agent = _load_agent()

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the fake tool is a #! script")

_HDR = struct.Struct(">cI")

_FAKE_TOOL = r"""
import json, os, subprocess, sys, time
argv = sys.argv[1:]
stdin = sys.stdin.read() if os.environ.get("FAKE_TOOL_READ_STDIN") else None
log = os.environ.get("FAKE_TOOL_LOG")
if log:
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"argv": argv, "cwd": os.getcwd(), "stdin": stdin}) + "\n")
for name in (os.environ.get("FAKE_TOOL_NEED") or "").split(","):
    if name and not os.path.isfile(name):
        sys.stdout.write("ERROR: cannot open %s\n" % name)
        sys.stdout.flush()
        sys.exit(2)
child = os.environ.get("FAKE_TOOL_CHILD")
if child:
    # inherits our stdout, like a vendor tool's USB worker process
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    with open(child, "w") as fh:
        fh.write(str(p.pid))
writes = os.environ.get("FAKE_TOOL_WRITE")
if writes:
    for name, size in json.loads(writes).items():
        if os.path.dirname(name):
            os.makedirs(os.path.dirname(name), exist_ok=True)
        with open(name, "wb") as fh:
            fh.write(b"L" * size)
out = os.environ.get("FAKE_TOOL_OUT_HEX")
if out:
    sys.stdout.buffer.write(bytes.fromhex(out))
    sys.stdout.flush()
time.sleep(float(os.environ.get("FAKE_TOOL_SLEEP") or 0))
sys.exit(int(os.environ.get("FAKE_TOOL_RC") or 0))
"""

_FAKE_VARS = (
    "FAKE_TOOL_READ_STDIN",
    "FAKE_TOOL_NEED",
    "FAKE_TOOL_CHILD",
    "FAKE_TOOL_WRITE",
    "FAKE_TOOL_OUT_HEX",
    "FAKE_TOOL_SLEEP",
    "FAKE_TOOL_RC",
)

# What Win32_PnPEntity gives for a board in a vendor download mode with a
# serial port, next to the board's debug console.
_DL_PNP = {
    "Name": "Vendor USB Download Port (COM41)",
    "PNPDeviceID": "USB\\VID_1A2B&PID_3C4D\\6&2C3D&0&2",
    "Status": "OK",
    "ConfigManagerErrorCode": 0,
    "PNPClass": "Ports",
    "Service": "vendorusb",
    "Manufacturer": "Vendor",
    "CompatibleID": None,
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
    devices: list[dict[str, Any]] = agent._parse_usb_json(json.dumps(list(entries)))
    return devices


class _Ws:
    def __init__(self, req: dict[str, Any]) -> None:
        payload = json.dumps(req).encode()
        self._data = _HDR.pack(b"C", len(payload)) + payload
        self._given = False
        self.sent: list[dict[str, Any]] = []

    async def recv(self) -> bytes:
        if self._given:
            return b""
        self._given = True
        return self._data

    async def send(self, data: bytes) -> None:
        kind, length = _HDR.unpack(data[:5])
        assert kind == b"C"
        assert length <= agent._JOB_MAX_FRAME
        self.sent.append(json.loads(data[5 : 5 + length]))

    def done(self) -> dict[str, Any]:
        msgs = [m for m in self.sent if m.get("ev") == "done"]
        assert msgs, f"no done frame; got {self.sent}"
        return msgs[-1]

    def progress(self) -> list[str]:
        return [m["text"] for m in self.sent if m.get("ev") == "progress"]

    def files(self) -> dict[str, bytes]:
        """The `file` frames put back together, as the hub would."""
        out: dict[str, bytearray] = {}
        for m in self.sent:
            if m.get("ev") != "file":
                continue
            buf = out.setdefault(m["name"], bytearray())
            assert m["offset"] == len(buf), "pieces in order"
            buf += base64.b64decode(m["b64"])
        return {k: bytes(v) for k, v in out.items()}


@dataclass
class _Bench:
    tmp: Path
    root: Path  # tool_root
    pkg: Path  # one package under it: root/board-a/v1
    exe: Path
    log: Path
    usb: list[list[dict[str, Any]]]
    monkeypatch: pytest.MonkeyPatch

    def ops(self, **cfg: str) -> dict[str, Any]:
        """The op table for one tool `vt` registered with these extra keys."""
        tools, problems = agent._resolve_tools({"vt": self.conf(**cfg)})
        assert problems == []
        table: dict[str, Any] = agent._build_ops("", {}, None, tools, tuple(problems))
        return table

    def conf(self, **cfg: str) -> dict[str, str]:
        return {"tool_exe": str(self.exe), "tool_root": str(self.root), **cfg}

    def calls(self) -> list[dict[str, Any]]:
        if not self.log.exists():
            return []
        return [json.loads(ln) for ln in self.log.read_text(encoding="utf-8").splitlines()]

    def prints(self, data: bytes, rc: int = 0) -> None:
        self.monkeypatch.setenv("FAKE_TOOL_OUT_HEX", data.hex())
        self.monkeypatch.setenv("FAKE_TOOL_RC", str(rc))


@pytest.fixture
def bench(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Bench:
    root = tmp_path / "packages"
    pkg = root / "board-a" / "v1"
    pkg.mkdir(parents=True)
    (pkg / "update.img").write_bytes(b"img")
    (pkg / "old.log").write_bytes(b"from an earlier run")
    exe = tmp_path / "tools" / "vendortool"
    exe.parent.mkdir()
    exe.write_text(f"#!{sys.executable}\n{_FAKE_TOOL}", encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_TOOL_LOG", str(log))
    for var in _FAKE_VARS:
        monkeypatch.delenv(var, raising=False)
    usb = [_usb(_CONSOLE_PNP, _DL_PNP)]

    async def fake_usb(_timeout_s: float) -> list[dict[str, Any]]:
        return usb[0]

    monkeypatch.setattr(agent, "_tool_usb_devices", fake_usb)
    return _Bench(tmp_path, root, pkg, exe, log, usb, monkeypatch)


async def _run(ops: dict[str, Any], **req: Any) -> _Ws:
    ws = _Ws({"op": "tool-run", **req})
    await agent._run_job(ws, ops, hint="tool-run")
    return ws


def _gone(pid: int, wait_s: float = 3.0) -> bool:
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if not agent._posix_alive(pid):
            return True
        time.sleep(0.05)
    return False


# ── registration: agent.conf ────────────────────────────────────────────


def _conf(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "agent.conf"
    path.write_text("hub_url=ws://h/agent/connect\n" + text, encoding="utf-8")
    return path


def test_tool_keys_are_collected_per_name_and_usage_lines_join(tmp_path: Path) -> None:
    cfg = agent._load_config(
        _conf(
            tmp_path,
            "tool_exe.rk=D:\\t\\rk.exe\n"
            "TOOL_ROOT.rk=D:\\flash\n"
            "tool_usage.rk=line one\n"
            "tool_usage.rk=line two = with an equals sign\n"
            "tool_exe.qc=D:\\t\\qc.exe\n",
        )
    )
    assert cfg["tool_config"] == {
        "rk": {
            "tool_exe": "D:\\t\\rk.exe",
            "tool_root": "D:\\flash",
            "tool_usage": "line one\nline two = with an equals sign",
        },
        "qc": {"tool_exe": "D:\\t\\qc.exe"},
    }


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ("tool_exe.RK=x", "bad tool name"),
        ("tool_exe.-x=x", "bad tool name"),
        ("tool_exe.rk=", "is empty"),
        ("tool_okay.rk=x", "unknown key"),
        ("tool_exe=x", "unknown key"),
    ],
)
def test_bad_tool_lines_refuse_the_start(tmp_path: Path, line: str, message: str) -> None:
    with pytest.raises(SystemExit) as exc_info:
        agent._load_config(_conf(tmp_path, line + "\n"))
    assert message in str(exc_info.value)


@pytest.mark.parametrize(
    ("cfg", "message"),
    [
        ({"tool_exe": "/x"}, "tool_root.vt not set"),
        ({"tool_root": "/x"}, "tool_exe.vt not set"),
        ({"tool_exe": "vendortool", "tool_root": "/x"}, "absolute path"),
        ({"tool_exe": "/x", "tool_root": "packages"}, "absolute path"),
        ({"tool_exe": "/x", "tool_root": "/x", "tool_cwd": "here"}, "package or job"),
        ({"tool_exe": "/x", "tool_root": "/x", "tool_timeout": "0"}, "1 to 1700"),
        ({"tool_exe": "/x", "tool_root": "/x", "tool_timeout": "1701"}, "1 to 1700"),
        ({"tool_exe": "/x", "tool_root": "/x", "tool_timeout": "10s"}, "1 to 1700"),
        ({"tool_exe": "/x", "tool_root": "/x", "tool_ok": "(unclosed"}, "tool_ok.vt"),
        ({"tool_exe": "/x", "tool_root": "/x", "tool_fail": "[z-a]"}, "tool_fail.vt"),
        ({"tool_exe": "/x", "tool_root": "/x", "tool_usb": "1a2b:3c4"}, "VID:PID"),
    ],
)
def test_settings_that_cannot_be_right_are_errors(cfg: dict[str, str], message: str) -> None:
    with pytest.raises(ValueError) as exc_info:
        agent._resolve_tools({"vt": cfg})
    assert message in str(exc_info.value)


def test_a_missing_exe_or_root_is_a_problem_not_an_error(bench: _Bench) -> None:
    tools, problems = agent._resolve_tools(
        {
            "a": {"tool_exe": str(bench.tmp / "nope.exe"), "tool_root": str(bench.root)},
            "b": {"tool_exe": str(bench.exe), "tool_root": str(bench.tmp / "nope")},
            "c": {"tool_exe": str(bench.exe), "tool_root": str(bench.root)},
        }
    )
    assert list(tools) == ["c"]
    assert len(problems) == 2
    assert "tool a: tool_exe" in problems[0] and "tool b: tool_root" in problems[1]


def test_windows_paths_count_as_absolute(bench: _Bench) -> None:
    """A bench agent.conf names D:\\ paths; checked here they are absent,
    not refused as relative."""
    tools, problems = agent._resolve_tools(
        {"w": {"tool_exe": "D:\\tools\\x.exe", "tool_root": "D:/flash"}}
    )
    assert tools == {} and "not a file" in problems[0]


def test_defaults_and_settings(bench: _Bench) -> None:
    tools, _ = agent._resolve_tools(
        {
            "vt": bench.conf(
                tool_cwd="JOB",
                tool_timeout="90",
                tool_ok="OKAY",
                tool_usb="1A2B:3C4D, 1a2b:3c4d,1a2b:5e6f",
            )
        }
    )
    tool = tools["vt"]
    assert (tool.cwd, tool.timeout_s, tool.usb) == ("job", 90.0, ("1a2b:3c4d", "1a2b:5e6f"))
    plain, _ = agent._resolve_tools({"vt": bench.conf()})
    assert (plain["vt"].cwd, plain["vt"].timeout_s) == ("package", 1500.0)
    assert plain["vt"].ok is None and plain["vt"].fail is None


def test_no_tool_rows_without_a_tool() -> None:
    assert not agent._TOOL_OP_NAMES & set(agent._build_ops("/opt/fastboot", {}))


def test_the_rows_and_what_they_tell_the_hub(bench: _Bench) -> None:
    ops = bench.ops()
    run, listing = ops["tool-run"], ops["tool-list"]
    d = run.describe()
    assert (d["group"], d["record"], d["mutates"], d["exclusive"]) == ("tool", "uart", True, True)
    assert [p["name"] for p in d["params"]] == ["tool", "pkg", "args", "port"]
    assert d["params"][0]["choices"] == ["vt"]
    assert d["params"][2] == {
        "name": "args",
        "kind": "argv",
        "required": False,
        "default": [],
        "max_items": 64,
        "max_len": 1024,
    }
    assert d["timeout_s"] < 30 * 60
    ld = listing.describe()
    assert (ld["mutates"], ld["exclusive"], ld["record"]) == (False, False, "none")
    json.dumps([d, ld])


def test_agent_conf_reaches_the_table_hello_and_self_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bench: _Bench, capsys: Any
) -> None:
    conf = _conf(
        tmp_path,
        f"tool_exe.vt={bench.exe}\ntool_root.vt={bench.root}\n"
        f"tool_exe.gone={tmp_path / 'gone.exe'}\ntool_root.gone={bench.root}\n",
    )
    for name in (
        "_OPS",
        "_FASTBOOT_PATH",
        "_FLASH_PARTITIONS",
        "_BOARD_PARTITIONS",
        "_BOARD_PRODUCT",
        "_EDL",
        "_EDL_PROBLEM",
        "_TOOLS",
        "_TOOL_PROBLEMS",
    ):
        monkeypatch.setattr(agent, name, getattr(agent, name))
    monkeypatch.setattr(agent, "_AGENT_LOG_PATH", None)
    args = agent._parse_args(["--config", str(conf)])
    assert agent._self_check_main(args) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["tools"] == ["vt"]
    assert "tool gone: tool_exe" in report["tool_problems"][0]
    hello = json.loads(agent._hello("a", "n", None))
    run = next(op for op in hello["ops"] if op["name"] == "tool-run")
    assert run["params"][0]["choices"] == ["vt"]


def test_a_bad_tool_setting_stops_the_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conf = _conf(tmp_path, "tool_exe.vt=/x\n")
    for name in ("_OPS", "_TOOLS", "_TOOL_PROBLEMS"):
        monkeypatch.setattr(agent, name, getattr(agent, name))
    with pytest.raises(SystemExit) as exc_info:
        agent._apply_flash_config(agent._parse_args(["--config", str(conf)]))
    assert "tool_root.vt not set" in str(exc_info.value)


# ── the path rules: checked before {pkg}/{com} are filled in ────────────


@pytest.mark.parametrize(
    "arg",
    [
        "getvar",
        "-p",
        "/s",
        "--memory=emmc:0",
        "-s",
        "13:prog_firehose.elf",
        "a.xml,b.xml",
        "update.img",
        "sub/dir/file.bin",
        "sub\\dir\\file.bin",
        "{pkg}",
        "{pkg}\\update.img",
        "--search_path={pkg}",
        "\\\\.\\COM41",
        "--port=\\\\.\\COM41",
        "\\\\.\\{com}",
        "...",
        "v1..2",
        "http://example.invalid/x",
        "name with spaces",
        "100%",
        "a&b|c>d",
        "",
    ],
)
def test_arguments_that_pass_the_path_rules(arg: str) -> None:
    assert agent._tool_arg_problem(arg) == ""


@pytest.mark.parametrize(
    ("arg", "rule"),
    [
        ("..", "'..'"),
        ("../x", "'..'"),
        ("a/../b", "'..'"),
        ("x\\..\\y", "'..'"),
        ("--out=..\\x", "'..'"),
        ("a.xml,../b.xml", "'..'"),
        ("{pkg}\\..\\other", "'..'"),
        ("C:\\Windows\\x", "drive path"),
        ("d:/x", "drive path"),
        ("--out=C:\\x", "drive path"),
        ("file:///C:/x", "drive path"),
        ("\\\\server\\share\\x", "network"),
        ("\\\\?\\C:\\x", "drive path"),
        ("\\\\?\\UNC\\srv\\x", "network"),
        ("\\\\.\\PhysicalDrive0", "network"),
        ("\\\\.\\COM41x", "network"),
        ("//server/share", "network"),
        ("--o=//srv/x", "network"),
        ("\\Windows\\x", "root of the drive"),
        ("--o=\\x", "root of the drive"),
    ],
)
def test_arguments_the_path_rules_refuse(arg: str, rule: str) -> None:
    assert rule in agent._tool_arg_problem(arg)


async def test_a_refused_argument_names_its_number_and_runs_nothing(bench: _Bench) -> None:
    ws = await _run(bench.ops(), tool="vt", pkg="board-a/v1", args=["-p", "ok", "C:\\x"])
    done = ws.done()
    assert (done["ok"], done["code"]) == (False, "BENCH_TOOL_ARG_REJECTED")
    assert "argument 3" in done["error"] and "drive path" in done["error"]
    assert bench.calls() == []


@pytest.mark.parametrize(
    ("args", "error"),
    [
        (["a\nb"], "control character"),
        (["\x00"], "control character"),
        (["x" * 1025], "longer than 1024"),
        (["a"] * 65, "at most 64"),
        ("getvar product", "list of strings"),
        ([1, 2], "list of strings"),
    ],
)
async def test_argument_shapes_the_agent_refuses(bench: _Bench, args: Any, error: str) -> None:
    ws = await _run(bench.ops(), tool="vt", args=args)
    done = ws.done()
    assert done["code"] == "BENCH_TOOL_ARG_REJECTED" and error in done["error"]
    assert bench.calls() == []


# ── placeholders ────────────────────────────────────────────────────────


async def test_arguments_reach_the_tool_unchanged_without_a_shell(bench: _Bench) -> None:
    args = ["write", "name with spaces", "a&b|c>d", "%PATH%", "$HOME", "*.img", "", "{{x}}"]
    ws = await _run(bench.ops(), tool="vt", pkg="board-a/v1", args=args)
    assert ws.done()["ok"], ws.done()
    (call,) = bench.calls()
    assert call["argv"] == [*args[:-1], "{x}"]
    assert Path(call["cwd"]) == bench.pkg.resolve()


async def test_pkg_is_the_package_path_inside_an_argument(bench: _Bench) -> None:
    ws = await _run(bench.ops(), tool="vt", pkg="board-a/v1", args=["{pkg}", "--search_path={pkg}"])
    assert ws.done()["ok"]
    pkg = str(bench.pkg.resolve())
    assert bench.calls()[0]["argv"] == [pkg, f"--search_path={pkg}"]


async def test_rules_are_checked_before_pkg_is_filled_in(
    bench: _Bench, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On the bench {pkg} becomes D:\\…: a drive path the rules refuse in
    an argument. Checked after filling in, every {pkg} would be refused."""
    windows_pkg = PureWindowsPath("D:\\flash\\board-a\\v1")
    monkeypatch.setattr(agent, "_tool_package", lambda _tool, _rel: windows_pkg)
    ws = await _run(bench.ops(tool_cwd="job"), tool="vt", pkg="board-a/v1", args=["{pkg}\\x.img"])
    assert ws.done()["ok"], ws.done()
    assert bench.calls()[0]["argv"] == ["D:\\flash\\board-a\\v1\\x.img"]


@pytest.mark.parametrize(("arg", "error"), [("{pakg}", "lone '{'"), ("x}", "lone '}'")])
async def test_an_unknown_placeholder_is_refused(bench: _Bench, arg: str, error: str) -> None:
    ws = await _run(bench.ops(), tool="vt", pkg="board-a/v1", args=["ok", arg])
    done = ws.done()
    assert done["code"] == "BENCH_TOOL_ARG_REJECTED"
    assert "argument 2" in done["error"] and error in done["error"]


async def test_pkg_without_a_package_is_refused(bench: _Bench) -> None:
    ws = await _run(bench.ops(), tool="vt", args=["-f", "{pkg}/x.img"])
    done = ws.done()
    assert done["code"] == "BENCH_TOOL_ARG_REJECTED" and "argument 2" in done["error"]
    assert bench.calls() == []


async def test_com_is_the_port_of_the_registered_usb_device(bench: _Bench) -> None:
    ops = bench.ops(tool_usb="1a2b:3c4d")
    ws = await _run(ops, tool="vt", pkg="board-a/v1", args=["-p", "\\\\.\\{com}", "{com}"])
    assert ws.done()["ok"], ws.done()
    assert bench.calls()[0]["argv"] == ["-p", "\\\\.\\COM41", "COM41"]
    assert ws.done()["data"]["port"] == "COM41"


async def test_com_without_tool_usb_is_refused(bench: _Bench) -> None:
    ws = await _run(bench.ops(), tool="vt", args=["{com}"])
    done = ws.done()
    assert done["code"] == "BENCH_TOOL_ARG_REJECTED" and "tool_usb.vt" in done["error"]


async def test_two_download_ports_need_a_choice(bench: _Bench) -> None:
    second = {
        **_DL_PNP,
        "Name": "Vendor USB Download Port (COM42)",
        "PNPDeviceID": "USB\\VID_1A2B&PID_3C4D\\7",
    }
    bench.usb[0] = _usb(_DL_PNP, second)
    ops = bench.ops(tool_usb="1a2b:3c4d")
    ws = await _run(ops, tool="vt", args=["{com}"])
    assert ws.done()["code"] == "BENCH_TOOL_ARG_REJECTED" and "COM41, COM42" in ws.done()["error"]
    ws = await _run(ops, tool="vt", args=["{com}"], port="COM42")
    assert ws.done()["ok"] and bench.calls()[-1]["argv"] == ["COM42"]


# ── the device and the package ──────────────────────────────────────────


async def test_absent_usb_device_is_reported_with_what_is_attached(bench: _Bench) -> None:
    bench.usb[0] = _usb(_CONSOLE_PNP)
    ws = await _run(bench.ops(tool_usb="1a2b:3c4d"), tool="vt", pkg="board-a/v1")
    done = ws.done()
    assert done["code"] == "BENCH_TOOL_NO_DEVICE"
    assert "1a2b:3c4d" in done["error"] and "USB Serial Port (COM27) (0403:6001)" in done["error"]
    assert bench.calls() == []


async def test_a_device_without_a_com_port_points_at_the_driver(bench: _Bench) -> None:
    bench.usb[0] = _usb({**_DL_PNP, "Name": "Unknown USB Device"})
    ws = await _run(bench.ops(tool_usb="1a2b:3c4d"), tool="vt", args=["{com}"])
    assert ws.done()["code"] == "BENCH_TOOL_NO_DEVICE" and "driver" in ws.done()["error"]


async def test_without_tool_usb_nothing_is_checked(bench: _Bench) -> None:
    bench.usb[0] = []
    ws = await _run(bench.ops(), tool="vt", pkg="board-a/v1")
    assert ws.done()["ok"]


@pytest.mark.parametrize("pkg", ["../x", "board-a/../../x", "/etc", "C:\\x", ".hidden"])
async def test_package_shapes_refused(bench: _Bench, pkg: str) -> None:
    ws = await _run(bench.ops(), tool="vt", pkg=pkg)
    assert ws.done()["code"] == "BENCH_TOOL_PACKAGE_REJECTED"
    assert bench.calls() == []


async def test_a_package_linking_outside_the_root_is_refused(bench: _Bench) -> None:
    outside = bench.tmp / "elsewhere"
    outside.mkdir()
    (bench.root / "board-a" / "link").symlink_to(outside, target_is_directory=True)
    ws = await _run(bench.ops(), tool="vt", pkg="board-a/link")
    done = ws.done()
    assert done["code"] == "BENCH_TOOL_PACKAGE_REJECTED" and "outside tool_root" in done["error"]
    assert bench.calls() == []


async def test_a_missing_package_is_refused(bench: _Bench) -> None:
    ws = await _run(bench.ops(), tool="vt", pkg="board-a/v9")
    assert ws.done()["code"] == "BENCH_TOOL_PACKAGE_REJECTED"


async def test_an_unregistered_tool_names_the_registered_ones(bench: _Bench) -> None:
    ws = await _run(bench.ops(), tool="other")
    done = ws.done()
    assert done["code"] == "BENCH_TOOL_UNKNOWN"
    assert "(vt)" in done["error"] and "tool_exe.<name>" in done["error"]


async def test_tool_ops_on_an_agent_without_tools_say_how_to_register(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent, "_TOOL_PROBLEMS", ("tool x: tool_exe D:\\x.exe is not a file",))
    ws = _Ws({"op": "tool-run", "tool": "x"})
    await agent._run_job(ws, agent._build_ops("", {}), hint="tool-run")
    done = ws.done()
    assert done["code"] == "BENCH_OP_UNKNOWN"
    assert "tool_exe.<name>" in done["error"] and "D:\\x.exe is not a file" in done["error"]


# ── working directory and stdin ─────────────────────────────────────────


async def test_the_tool_runs_in_the_package_and_finds_its_files_by_name(bench: _Bench) -> None:
    bench.monkeypatch.setenv("FAKE_TOOL_NEED", "update.img")
    ws = await _run(bench.ops(), tool="vt", pkg="board-a/v1", args=["-f", "update.img"])
    assert ws.done()["ok"], ws.done()


async def test_tool_cwd_job_runs_in_a_scratch_directory_removed_after(bench: _Bench) -> None:
    bench.monkeypatch.setenv("FAKE_TOOL_WRITE", json.dumps({"trace.txt": 5}))
    ws = await _run(bench.ops(tool_cwd="job"), tool="vt", pkg="board-a/v1", args=["{pkg}"])
    assert ws.done()["ok"]
    cwd = Path(bench.calls()[0]["cwd"])
    assert cwd != bench.pkg.resolve() and not _exists(cwd)
    assert ws.files() == {"trace.txt": b"LLLLL"}
    assert not _exists(bench.pkg / "trace.txt")


async def test_no_package_runs_in_a_scratch_directory(bench: _Bench) -> None:
    ws = await _run(bench.ops(), tool="vt", args=["--version"])
    assert ws.done()["ok"] and ws.done()["data"]["cwd"] == "job"
    assert not _exists(bench.calls()[0]["cwd"])


async def test_stdin_is_empty_so_a_prompt_does_not_hang(bench: _Bench) -> None:
    bench.monkeypatch.setenv("FAKE_TOOL_READ_STDIN", "1")
    ws = await _run(bench.ops(tool_timeout="5"), tool="vt")
    assert ws.done()["ok"] and bench.calls()[0]["stdin"] == ""


# ── output and the verdict ──────────────────────────────────────────────


async def test_lines_stream_as_progress_and_crlf_is_one_break(bench: _Bench) -> None:
    bench.prints(b"start\r\nwriting boot\r\n\r\ndone\n")
    ws = await _run(bench.ops(), tool="vt")
    assert ws.progress() == ["start", "writing boot", "done"]
    assert ws.done()["stdout"] == "start\nwriting boot\ndone"


async def test_gbk_output_is_decoded(bench: _Bench) -> None:
    bench.prints("下载完成\r\n".encode("gbk") + "成功 ok\n".encode())
    ws = await _run(bench.ops(), tool="vt")
    assert ws.progress() == ["下载完成", "成功 ok"]


async def test_redrawn_progress_is_checked_every_time_but_forwarded_once_a_second(
    bench: _Bench,
) -> None:
    redraws = b"".join(f"{n}%\r".encode() for n in range(100))
    bench.prints(redraws + b"\nall done\n")
    ws = await _run(bench.ops(tool_note=r"^99%"), tool="vt")
    shown = ws.progress()
    assert shown[0] == "0%" and shown[-1] == "all done"
    assert len(shown) < 10, "a hundred redraws in one burst are not a hundred frames"
    assert ws.done()["data"]["highlights"] == ["99%"], "the patterns saw every redraw"


async def test_judged_by_ok_pattern(bench: _Bench) -> None:
    bench.prints(b"Download finished\n")
    ws = await _run(bench.ops(tool_ok=r"finished"), tool="vt")
    done = ws.done()
    assert done["ok"] and done["data"]["judged_by"] == "exit_code+ok_pattern"
    assert "note" not in done["data"]


async def test_exit_code_only_is_said(bench: _Bench) -> None:
    ws = await _run(bench.ops(), tool="vt")
    done = ws.done()
    assert done["ok"] and done["data"]["judged_by"] == "exit_code_only"
    assert "exit code only" in done["data"]["note"]


async def test_ok_pattern_missing_fails_an_exit_code_0(bench: _Bench) -> None:
    bench.prints(b"Download image ...\n")
    ws = await _run(bench.ops(tool_ok=r"finished"), tool="vt")
    done = ws.done()
    assert (done["ok"], done["code"]) == (False, "BENCH_TOOL_FAILED")
    assert done["data"]["judged_by"] == "ok_pattern_missing"
    assert done["stdout"] == "Download image ..."


async def test_fail_pattern_fails_an_exit_code_0(bench: _Bench) -> None:
    bench.prints(b"Download finished\nwrite misc failed: -5\n")
    ws = await _run(bench.ops(tool_ok=r"finished", tool_fail=r"failed"), tool="vt")
    done = ws.done()
    assert (done["ok"], done["data"]["judged_by"]) == (False, "fail_pattern")
    assert "write misc failed: -5" in done["error"]


async def test_exit_code_fails_without_patterns(bench: _Bench) -> None:
    bench.prints(b"oops\n", rc=3)
    ws = await _run(bench.ops(tool_ok="never needed"), tool="vt")
    done = ws.done()
    assert (done["ok"], done["rc"], done["data"]["judged_by"]) == (False, 3, "exit_code")
    assert "exit code 3" in done["error"]


async def test_note_lines_become_highlights_capped(bench: _Bench) -> None:
    lines = b"".join(f"part p{n}: ok\n".encode() for n in range(205))
    bench.prints(lines + b"other\n")
    ws = await _run(bench.ops(tool_note=r"^part "), tool="vt")
    data = ws.done()["data"]
    assert data["highlights"][:2] == ["part p0: ok", "part p1: ok"]
    assert len(data["highlights"]) == 200 and data["highlights_dropped"] == 5
    assert len(ws.done()["stdout"].splitlines()) == 80


async def test_a_tool_that_will_not_start(bench: _Bench) -> None:
    ops = bench.ops()
    bench.exe.unlink()
    ws = await _run(ops, tool="vt")
    done = ws.done()
    assert done["code"] == "BENCH_TOOL_NOT_STARTED" and "vendortool" in done["error"]


# ── ending it: the whole process tree ───────────────────────────────────


async def test_timeout_ends_the_tool_and_what_it_started(bench: _Bench) -> None:
    child = bench.tmp / "child.pid"
    bench.monkeypatch.setenv("FAKE_TOOL_CHILD", str(child))
    bench.monkeypatch.setenv("FAKE_TOOL_SLEEP", "30")
    bench.prints(b"waiting for device\n")
    started = time.monotonic()
    ws = await _run(bench.ops(tool_timeout="1"), tool="vt")
    done = ws.done()
    assert time.monotonic() - started < 10
    assert (done["ok"], done["code"]) == (False, "BENCH_TOOL_TIMEOUT")
    assert done["data"]["judged_by"] == "timeout"
    assert "waiting for device" in done["stdout"] and "ended the tool" in done["stdout"]
    assert _gone(int(child.read_text())), "the child still runs (and would hold the USB device)"


async def test_a_child_left_holding_the_output_is_ended(
    bench: _Bench, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(agent, "_TOOL_DRAIN_S", 0.5)
    child = bench.tmp / "child.pid"
    bench.monkeypatch.setenv("FAKE_TOOL_CHILD", str(child))
    bench.prints(b"done\n")
    started = time.monotonic()
    ws = await _run(bench.ops(tool_timeout="30"), tool="vt")
    done = ws.done()
    assert time.monotonic() - started < 10, "did not wait for the child's 60 s"
    assert done["ok"] and "kept its output open" in done["stdout"]
    assert _gone(int(child.read_text()))


# ── files the tool wrote ────────────────────────────────────────────────


async def test_new_and_changed_files_come_back_unchanged_ones_do_not(bench: _Bench) -> None:
    bench.monkeypatch.setenv(
        "FAKE_TOOL_WRITE", json.dumps({"tool.log": 10, "logs/detail.txt": 3, "old.log": 4})
    )
    bench.prints(b"x\n", rc=1)
    ws = await _run(bench.ops(), tool="vt", pkg="board-a/v1")
    done = ws.done()
    assert done["code"] == "BENCH_TOOL_FAILED", "files come back on a failure too"
    assert ws.files() == {"logs/detail.txt": b"LLL", "old.log": b"LLLL", "tool.log": b"L" * 10}
    assert [f["name"] for f in done["data"]["files"]] == ["logs/detail.txt", "old.log", "tool.log"]
    assert all(f["sent"] for f in done["data"]["files"])
    file_frames = [i for i, m in enumerate(ws.sent) if m.get("ev") == "file"]
    assert max(file_frames) < ws.sent.index(done), "files go out before the result"


async def test_file_caps(bench: _Bench, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent, "_TOOL_FILE_MAX", 100)
    monkeypatch.setattr(agent, "_TOOL_FILES_MAX", 3)
    monkeypatch.setattr(agent, "_TOOL_FILES_TOTAL", 150)
    monkeypatch.setattr(agent, "_TOOL_FILE_CHUNK", 40)
    sizes = {"a.log": 60, "b.log": 101, "c.log": 100, "d.log": 10, "e.log": 10, "f.log": 10}
    bench.monkeypatch.setenv("FAKE_TOOL_WRITE", json.dumps(sizes))
    ws = await _run(bench.ops(), tool="vt", pkg="board-a/v1")
    files = {f["name"]: f for f in ws.done()["data"]["files"]}
    assert ws.files() == {"a.log": b"L" * 60, "d.log": b"L" * 10, "e.log": b"L" * 10}
    assert "over" in files["b.log"]["why"] and not files["b.log"]["sent"]  # one file too big
    assert "in all" in files["c.log"]["why"]  # would pass the total
    assert "files" in files["f.log"]["why"]  # the fourth to fit
    pieces = [m for m in ws.sent if m.get("ev") == "file" and m["name"] == "a.log"]
    assert [p["offset"] for p in pieces] == [0, 40] and [p["end"] for p in pieces] == [False, True]


async def test_the_real_caps_fit_the_job_frame() -> None:
    piece = agent._TOOL_FILE_CHUNK * 4 // 3 + 4096
    assert piece < agent._JOB_MAX_FRAME
    assert agent._TOOL_FILE_MAX <= agent._TOOL_FILES_TOTAL < agent._JOB_MAX_FRAME


# ── tool-list ───────────────────────────────────────────────────────────


async def test_tool_list_says_whether_each_device_is_attached(bench: _Bench) -> None:
    tools, _ = agent._resolve_tools(
        {
            "vt": bench.conf(tool_usb="1a2b:3c4d", tool_usage="hold the key\nthen plug in"),
            "other": bench.conf(tool_usb="1234:5678"),
            "plain": bench.conf(),
        }
    )
    ops = agent._build_ops("", {}, None, tools, ("tool gone: tool_exe x is not a file",))
    ws = _Ws({"op": "tool-list"})
    await agent._run_job(ws, ops, hint="tool-list")
    data = ws.done()["data"]
    rows = {t["name"]: t for t in data["tools"]}
    assert rows["vt"]["attached"] == ["Vendor USB Download Port (COM41) (1a2b:3c4d)"]
    assert rows["other"]["attached"] == []
    assert "attached" not in rows["plain"]
    assert rows["vt"]["usage"] == "hold the key\nthen plug in"
    assert rows["vt"]["exe"] == str(bench.exe) and rows["vt"]["cwd"] == "package"
    assert data["problems"] == ["tool gone: tool_exe x is not a file"]


async def test_tool_run_takes_the_job_lock_and_tool_list_does_not(bench: _Bench) -> None:
    ops = bench.ops()
    assert ops["tool-run"].exclusive and not ops["tool-list"].exclusive
