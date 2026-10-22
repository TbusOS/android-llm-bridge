"""The agent's op table (ADR-059), loaded by path like the other agent tests.

What is pinned here is the contract the hub is written against: the shape
of each row in `hello`, the four parameter kinds and their exact rules,
argv built from whole-element placeholders only, the dispatcher's error
codes, which ops skip the single job lock, and the two host builtins
(usb-list, log-tail) that never take a path from the hub.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
import struct
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

_AGENT_PATH = Path(__file__).resolve().parents[2] / "clients" / "windows-agent" / "alb_agent.py"


def _load_agent():
    spec = importlib.util.spec_from_file_location("alb_agent_ops_under_test", _AGENT_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


agent = _load_agent()

_HDR = struct.Struct(">cI")
FB = "/opt/fastboot"


def _frame(kind: bytes, payload: bytes) -> bytes:
    return _HDR.pack(kind, len(payload)) + payload


def _req(**fields) -> bytes:
    """A job request as the hub sends it: one control frame, flat fields."""
    return _frame(b"C", json.dumps(fields).encode())


class _FakeWs:
    """Feeds canned frames to the agent and records what it sends back."""

    def __init__(self, incoming: bytes = b"", *, chunk: int = 4096) -> None:
        self._data = incoming
        self._chunk = chunk
        self._pos = 0
        self.sent: list[dict] = []

    async def recv(self) -> bytes:
        if self._pos >= len(self._data):
            return b""
        out = self._data[self._pos : self._pos + self._chunk]
        self._pos += len(out)
        return out

    async def send(self, data: bytes) -> None:
        kind, length = _HDR.unpack(data[:5])
        assert kind == b"C", "the agent must only send control frames"
        self.sent.append(json.loads(data[5 : 5 + length]))

    def events(self, ev: str) -> list[dict]:
        return [m for m in self.sent if m.get("ev") == ev]

    def done(self) -> dict:
        msgs = self.events("done")
        assert msgs, f"no done frame; got {self.sent}"
        return msgs[-1]


def _ops(fastboot: str = FB, log_sources: dict[str, Path] | None = None):
    return agent._build_ops(fastboot, log_sources)


async def _dispatch(
    monkeypatch,
    req: dict,
    *,
    ops=None,
    present: bool = True,
    rc: int = 0,
    out: str = "",
    err: str = "",
    hint: str | None = None,
) -> tuple[_FakeWs, list[list[str]]]:
    """One request through the dispatcher with the tool and the device check
    faked. Returns the fake ws and every argv the tool was run with."""
    ran: list[list[str]] = []

    async def fake_run(_ws, argv, timeout_s=0.0):
        ran.append(argv)
        return rc, out, err

    async def fake_present(_fastboot):
        return present

    monkeypatch.setattr(agent, "_run_fastboot", fake_run)
    monkeypatch.setattr(agent, "_device_present", fake_present)
    ws = _FakeWs(_req(**req))
    await agent._run_job(ws, ops if ops is not None else _ops(), hint=hint)
    return ws, ran


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _read(path: str) -> bytes:
    return Path(path).read_bytes()


def _exists(path: str) -> bool:
    return Path(path).exists()


# ── parameter kinds: the rule, per kind, exactly as the contract states ──


def _param(**kw):
    return agent.ParamSpec(**kw)


def _rejects(param, value) -> str:
    """The code a value is rejected with ("" = it was accepted)."""
    try:
        param.resolve({param.name: value})
    except agent._JobRejected as e:
        return e.code
    return ""


@pytest.mark.parametrize("value", ["boot", "vendor_cfg", "super.img", "a-b"])
def test_partition_kind_accepts_well_formed_names(value):
    assert _param(name="partition", kind="partition").resolve({"partition": value}) == value


@pytest.mark.parametrize("value", ["", None, "../x", "a b", "-w", 7, True, ["boot"]])
def test_partition_kind_rejects_with_the_partition_code(value):
    p = _param(name="partition", kind="partition", code="FLASH_PARTITION_REJECTED")
    assert _rejects(p, value) == "FLASH_PARTITION_REJECTED"


def test_partition_kind_honours_the_allowlist(monkeypatch):
    """The row says `partition`; the allowlist it means is this bench's."""
    monkeypatch.setattr(agent, "_FLASH_PARTITIONS", frozenset({"vendor_cfg"}))
    p = _param(name="partition", kind="partition")
    assert p.resolve({"partition": "vendor_cfg"}) == "vendor_cfg"
    assert _rejects(p, "boot") == "BENCH_PARAM_REJECTED"


@pytest.mark.parametrize("value", ["a", "b"])
def test_enum_kind_accepts_a_listed_choice(value):
    assert _param(name="slot", kind="enum", choices=("a", "b")).resolve({"slot": value}) == value


@pytest.mark.parametrize("value", ["A", " a", "a ", "c", "", None, 0, True, ["a"]])
def test_enum_kind_is_exact(value):
    """No case folding, no strip: the device would not fold it either, and a
    stray space would become a second argv element."""
    p = _param(name="slot", kind="enum", choices=("a", "b"))
    assert _rejects(p, value) == "BENCH_PARAM_REJECTED"


@pytest.mark.parametrize("value", [1, 200, 2000])
def test_int_kind_accepts_in_range(value):
    p = _param(name="lines", kind="int", required=False, default=200, min=1, max=2000)
    assert p.resolve({"lines": value}) == value


@pytest.mark.parametrize("value", [0, 2001, -1, "200", 1.5, [200], True, False])
def test_int_kind_rejects_out_of_range_and_non_integers(value):
    """`True` is an int to Python and a caller bug to this table."""
    p = _param(name="lines", kind="int", required=False, default=200, min=1, max=2000)
    assert _rejects(p, value) == "BENCH_PARAM_REJECTED"


@pytest.mark.parametrize("req", [{}, {"lines": None}, {"lines": ""}])
def test_optional_param_missing_null_or_empty_means_default(req):
    p = _param(name="lines", kind="int", required=False, default=200, min=1, max=2000)
    assert p.resolve(req) == 200


@pytest.mark.parametrize("value", ["OKAY", "x" * 64, "two words", "a:b/c"])
def test_text_kind_accepts_pattern_and_length(value):
    p = _param(
        name="grep",
        kind="text",
        required=False,
        default="",
        pattern=agent._GREP_PATTERN,
        max_len=64,
    )
    assert p.resolve({"grep": value}) == value


@pytest.mark.parametrize("value", ["x" * 65, "a\nb", "a\x00b", "\x7f", 5, ["x"]])
def test_text_kind_rejects_overlong_control_chars_and_non_strings(value):
    p = _param(
        name="grep",
        kind="text",
        required=False,
        default="",
        pattern=agent._GREP_PATTERN,
        max_len=64,
    )
    assert _rejects(p, value) == "BENCH_PARAM_REJECTED"


def test_getvar_name_rides_the_text_kind_with_its_own_code():
    name = _ops()["getvar"].params[0]
    assert name.resolve({"name": "partition-size:cfg"}) == "partition-size:cfg"
    assert name.resolve({}) == "all", "no name means `getvar all`"
    assert _rejects(name, "-w") == "FLASH_VAR_REJECTED"


@pytest.mark.parametrize(
    ("op", "code"),
    [
        ("set-active", "BENCH_PARAM_REJECTED"),
        ("erase", "FLASH_PARTITION_REJECTED"),
        ("oem", "FLASH_OEM_REJECTED"),
    ],
)
def test_missing_required_param_is_rejected_with_that_params_code(op, code):
    try:
        _ops()[op].resolve({"op": op})
    except agent._JobRejected as e:
        assert e.code == code
    else:
        pytest.fail("a required param that is absent must be rejected")


def test_unknown_request_keys_are_ignored():
    """Documented choice: a key the table does not name cannot reach argv,
    so there is nothing to refuse — and `op`/`size`/`sha256` ride the same
    frame. What is NOT ignored is the value of a named param."""
    values = _ops()["erase"].resolve({"op": "erase", "partition": "misc", "shell": "rm -rf /"})
    assert values == {"partition": "misc"}


def test_param_spec_refuses_a_malformed_row():
    """A table bug fails at build time, not at the first request."""
    with pytest.raises(ValueError):
        _param(name="x", kind="enum")  # no choices
    with pytest.raises(ValueError):
        _param(name="x", kind="int")  # no bounds
    with pytest.raises(ValueError):
        _param(name="x", kind="text")  # no pattern
    with pytest.raises(ValueError):
        _param(name="x", kind="path")  # not a kind


# ── argv assembly: a placeholder is one whole element, always ───────────


@pytest.mark.parametrize(
    ("req", "argv"),
    [
        ({"op": "erase", "partition": "misc"}, [FB, "erase", "misc"]),
        ({"op": "set-active", "slot": "b"}, [FB, "set_active", "b"]),
        ({"op": "oem", "command": "at-unlock-vboot"}, [FB, "oem", "at-unlock-vboot"]),
        ({"op": "getvar"}, [FB, "getvar", "all"]),
        ({"op": "getvar", "name": "has-slot:boot"}, [FB, "getvar", "has-slot:boot"]),
        ({"op": "reboot"}, [FB, "reboot"]),
        ({"op": "reboot", "target": ""}, [FB, "reboot"]),
        ({"op": "reboot", "target": "bootloader"}, [FB, "reboot", "bootloader"]),
        ({"op": "devices"}, [FB, "devices"]),
    ],
)
def test_argv_is_the_template_with_one_element_per_placeholder(req, argv):
    spec = _ops()[req["op"]]
    assert spec.assemble(spec.resolve(req)) == argv


def test_a_value_that_looks_like_a_placeholder_is_inserted_verbatim():
    """Substitution is one pass over the template, never over values."""
    spec = agent.OpSpec(
        "probe",
        "host",
        "probe",
        params=(agent.ParamSpec("v", "enum", choices=("{image}", "{v}")),),
        argv=("tool", "{v}"),
    )
    assert spec.assemble({"v": "{image}"}) == ["tool", "{image}"]
    assert spec.assemble({"v": "{v}"}) == ["tool", "{v}"]


def test_image_placeholder_takes_the_agents_path_as_one_element():
    spec = _ops()["flash"]
    argv = spec.assemble({"partition": "boot"}, image="/tmp/alb flash/image.bin")
    assert argv == [FB, "flash", "boot", "/tmp/alb flash/image.bin"]


@pytest.mark.parametrize(
    "argv",
    [
        (FB, "erase", "--part={partition}"),  # placeholder inside an element
        (FB, "erase", "{partition}{partition}"),  # two in one element
        (FB, "erase", "{nope}"),  # names no param
        (FB, "erase", "{image}"),  # image on a non-image op
    ],
)
def test_a_template_whose_placeholder_is_not_a_whole_element_is_refused_at_build(argv):
    with pytest.raises(ValueError):
        agent.OpSpec(
            "erase",
            "fastboot",
            "x",
            params=(agent.ParamSpec("partition", "partition"),),
            argv=argv,
        )


def test_an_image_op_must_have_an_image_element():
    with pytest.raises(ValueError):
        agent.OpSpec("boot", "fastboot", "x", image=True, argv=(FB, "boot"))


def test_a_row_has_exactly_one_of_argv_or_builtin():
    async def builtin(_spec, _values):
        return "", {}

    with pytest.raises(ValueError):
        agent.OpSpec("x", "host", "x")
    with pytest.raises(ValueError):
        agent.OpSpec("x", "host", "x", argv=("t",), builtin=builtin)


# ── the table as the hub sees it ────────────────────────────────────────

_OP_KEYS = {
    "name",
    "group",
    "summary",
    "params",
    "needs",
    "image",
    "max_image_bytes",
    "mutates",
    "exclusive",
    "record",
    "timeout_s",
}


def test_every_row_describes_itself_with_exactly_the_contract_fields():
    for spec in _ops(FB, {"agent": Path("/x/agent.log")}).values():
        d = spec.describe()
        assert set(d) == _OP_KEYS, spec.name
        assert d["group"] in ("fastboot", "host")
        assert d["needs"] in ("fastboot-device", "")
        assert d["record"] in ("none", "events", "uart")
        for p in d["params"]:
            base = {"name", "kind", "required", "default"}
            extra = {"enum": {"choices"}, "int": {"min", "max"}, "text": {"pattern", "max_len"}}
            assert set(p) == base | extra.get(p["kind"], set()), (spec.name, p["name"])
        json.dumps(d)  # must be plain JSON


def test_the_table_matches_the_contract_rows():
    ops = _ops(FB, {"agent": Path("/x/agent.log"), "setupapi": Path("/y/setupapi.dev.log")})
    assert list(ops) == [
        "devices",
        "getvar",
        "oem",
        "reboot",
        "flash",
        "erase",
        "set-active",
        "boot",
        "usb-list",
        "log-tail",
        "agent-update",
    ]
    assert {n for n, s in ops.items() if s.image} == {"flash", "boot", "agent-update"}
    assert {n for n, s in ops.items() if not s.mutates} == {
        "devices",
        "getvar",
        "usb-list",
        "log-tail",
    }
    assert {n for n, s in ops.items() if not s.exclusive} == {"usb-list", "log-tail"}
    assert {n for n, s in ops.items() if s.needs == ""} == {
        "devices",
        "usb-list",
        "log-tail",
        "agent-update",
    }
    assert {n for n, s in ops.items() if s.record == "uart"} == {"flash", "boot"}
    assert ops["set-active"].timeout_s == 30.0
    assert ops["boot"].timeout_s == 120.0
    assert ops["usb-list"].timeout_s == 30.0 and ops["log-tail"].timeout_s == 30.0
    assert [p.name for p in ops["log-tail"].params] == ["source", "lines", "grep"]
    assert ops["log-tail"].params[0].choices == ("agent", "setupapi")
    assert ops["set-active"].params[0].choices == ("a", "b")
    assert ops["reboot"].params[0].choices == ("bootloader", "fastboot", "recovery")


def test_fastboot_rows_exist_only_with_a_fastboot_executable():
    assert [s.group for s in _ops("").values()] == ["host", "host"]
    assert all(s.argv[0] == FB for s in _ops(FB).values() if s.argv)
    assert agent._FASTBOOT_OP_NAMES == {
        "devices",
        "getvar",
        "oem",
        "reboot",
        "flash",
        "erase",
        "set-active",
        "boot",
    }


def test_log_tail_row_exists_only_with_a_configured_source():
    """A dropdown with zero valid choices is a question nobody can answer."""
    assert "log-tail" not in _ops(FB, {})
    assert "log-tail" in _ops(FB, {"agent": Path("/x")})


def test_hello_carries_the_table_from_apply_config(tmp_path, monkeypatch):
    """`_apply_flash_config` is what turns config into the rows hello
    reports — and the rows must say what this process will then accept."""
    exe = tmp_path / "fastboot"
    exe.write_text("#!/bin/sh\n")
    monkeypatch.setattr(agent, "_AGENT_LOG_PATH", tmp_path / "agent.log")
    monkeypatch.setattr(agent, "_OPS", {})
    # Without --config, _parse_args reads the agent.conf next to the script —
    # a deployed checkout has a real one, and its lists would leak into every
    # later test through the globals _apply_flash_config rebinds.
    monkeypatch.setattr(agent, "_default_config_path", lambda: tmp_path / "absent.conf")
    for name in ("_FASTBOOT_PATH", "_FLASH_PARTITIONS", "_BOARD_PARTITIONS", "_BOARD_PRODUCT"):
        monkeypatch.setattr(agent, name, getattr(agent, name))
    agent._apply_flash_config(
        agent._parse_args(["--hub-url", "ws://h", "--fastboot-path", str(exe)])
    )
    hello = json.loads(agent._hello("a", "n", None))
    names = [op["name"] for op in hello["ops"]]
    assert "flash" in names and "log-tail" in names
    assert hello["caps"] == ["adb", "fastboot"]
    assert agent._OPS["flash"].argv[0] == str(exe)
    assert agent._STATUS.snapshot()["ops"] == names, "the status page lists the same rows"


# ── the dispatcher ──────────────────────────────────────────────────────


async def test_unknown_op_is_bench_op_unknown(monkeypatch):
    ws, ran = await _dispatch(monkeypatch, {"op": "frobnicate"})
    assert ran == []
    done = ws.done()
    assert done["code"] == "BENCH_OP_UNKNOWN"
    assert not ws.events("accepted")


async def test_fastboot_op_on_a_table_without_fastboot_is_unknown(monkeypatch):
    ws, ran = await _dispatch(monkeypatch, {"op": "erase", "partition": "misc"}, ops=_ops(""))
    assert ran == [] and ws.done()["code"] == "BENCH_OP_UNKNOWN"


@pytest.mark.parametrize(
    ("req", "code"),
    [
        ({"op": "erase", "partition": "../x"}, "FLASH_PARTITION_REJECTED"),
        (
            {"op": "flash", "partition": "a b", "size": 4, "sha256": "00" * 32},
            "FLASH_PARTITION_REJECTED",
        ),
        ({"op": "oem", "command": "unlock"}, "FLASH_OEM_REJECTED"),
        ({"op": "getvar", "name": "-w"}, "FLASH_VAR_REJECTED"),
        ({"op": "set-active", "slot": "c"}, "BENCH_PARAM_REJECTED"),
        ({"op": "set-active"}, "BENCH_PARAM_REJECTED"),
        ({"op": "reboot", "target": "edl"}, "BENCH_PARAM_REJECTED"),
    ],
)
async def test_rejection_codes_per_contract(monkeypatch, req, code):
    ws, ran = await _dispatch(monkeypatch, req)
    assert ran == [], "a rejected request must not run anything"
    assert ws.done()["code"] == code
    assert not ws.events("accepted"), "a rejected job must not be accepted first"


async def test_needs_device_is_checked_after_validation_and_before_running(monkeypatch):
    ws, ran = await _dispatch(monkeypatch, {"op": "set-active", "slot": "a"}, present=False)
    assert ran == []
    done = ws.done()
    assert done["code"] == "FASTBOOT_NO_DEVICE"
    assert done["error"] == "no device is in fastboot on this host"
    assert not ws.events("accepted")


async def test_devices_runs_without_a_device_check(monkeypatch):
    """It IS the device check — gating it on itself would never list anything."""
    ws, ran = await _dispatch(monkeypatch, {"op": "devices"}, present=False, out="123\tfastboot\n")
    assert ran == [[FB, "devices"]]
    assert ws.done()["ok"] is True


async def test_set_active_runs_with_agent_built_argv(monkeypatch):
    ws, ran = await _dispatch(monkeypatch, {"op": "set-active", "slot": "b"}, err="OKAY\n")
    assert ran == [[FB, "set_active", "b"]]
    done = ws.done()
    assert done["ok"] is True and done["code"] == ""
    assert ws.events("accepted")[0]["detail"] == "set_active b"
    assert "data" not in done, "tool ops keep today's done frame exactly"


async def test_tool_failure_is_flash_failed_with_the_tools_words(monkeypatch):
    ws, _ = await _dispatch(
        monkeypatch, {"op": "set-active", "slot": "a"}, rc=1, err="FAILED (remote: not supported)\n"
    )
    done = ws.done()
    assert done["ok"] is False
    assert done["code"] == "FLASH_FAILED"
    assert done["error"] == "FAILED (remote: not supported)"


async def test_a_request_that_contradicts_the_channel_hint_is_refused(monkeypatch):
    """The lock decision was taken from the hint before dialling back; a
    request naming another op would run under the wrong decision."""
    ws, ran = await _dispatch(monkeypatch, {"op": "erase", "partition": "misc"}, hint="usb-list")
    assert ran == []
    assert ws.done()["code"] == "BENCH_PARAM_REJECTED"


async def test_bad_opening_frame_is_reported_not_raised(monkeypatch):
    ws = _FakeWs(_frame(b"D", b"not a control frame"))
    await agent._run_job(ws, _ops())
    assert ws.done()["ok"] is False


# ── open_channel: the hint decides lock and tool BEFORE dialling back ───


class _FakeDial:
    """Stands in for ws_connect: an async context manager yielding the fake
    data websocket, counting how often the agent dialled."""

    def __init__(self, data_ws: _FakeWs) -> None:
        self.data_ws = data_ws
        self.calls = 0

    def __call__(self, url, **_kw):
        self.calls += 1
        return self

    async def __aenter__(self):
        return self.data_ws

    async def __aexit__(self, *_a):
        return False


class _FakeSignal:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    def errors(self) -> list[str]:
        return [f["reason"] for f in self.sent if f.get("verb") == "channel_error"]


def _open_frame(op: str | None) -> dict:
    frame = {
        "verb": "open_channel",
        "cid": "c1",
        "channel_type": "job",
        "csecret": "s",
        "params": {},
    }
    if op is not None:
        frame["params"]["op"] = op
    return frame


async def _open(
    monkeypatch, op: str | None, incoming: bytes = b"", *, fastboot: str = FB
) -> tuple[_FakeSignal, _FakeWs, _FakeDial]:
    sig = _FakeSignal()
    data_ws = _FakeWs(incoming)
    dial = _FakeDial(data_ws)
    monkeypatch.setattr(agent, "ws_connect", dial)
    monkeypatch.setattr(agent, "_FASTBOOT_PATH", fastboot)
    monkeypatch.setattr(agent, "_OPS", _ops(fastboot))
    # a 2 s ceiling turns "took the lock it must not take" into a failure
    # instead of a hung test run
    await asyncio.wait_for(
        agent._handle_job_channel("ws://h/agent/connect", None, _open_frame(op), sig), 2.0
    )
    return sig, data_ws, dial


def _sysfs_tree(root: Path) -> None:
    """A minimal /sys/bus/usb/devices: one root hub (ignored), one device in
    bootloader fastboot (interface class ff/42/03, no driver bound)."""
    (root / "usb1").mkdir(parents=True)
    dev = root / "1-3"
    for name, value in {
        "idVendor": "18D1",
        "idProduct": "D00D",
        "product": "Android",
        "manufacturer": "Example",
        "serial": "1000000003",
        "bDeviceClass": "00",
        "bDeviceSubClass": "00",
        "bDeviceProtocol": "00",
    }.items():
        _write(dev / name, f"{value}\n".encode())
    (root / "drivers" / "usb").mkdir(parents=True)
    os.symlink(root / "drivers" / "usb", dev / "driver")
    itf = dev / "1-3:1.0"
    for name, value in {
        "bInterfaceClass": "ff",
        "bInterfaceSubClass": "42",
        "bInterfaceProtocol": "03",
        "bInterfaceNumber": "00",
    }.items():
        _write(itf / name, f"{value}\n".encode())
    os.symlink(itf, root / "1-3:1.0")  # the root-level alias real sysfs has


async def test_usb_list_runs_while_the_lock_is_held_and_without_fastboot(monkeypatch, tmp_path):
    """The two reasons it exists: a board that `fastboot devices` cannot see
    is diagnosed DURING someone else's flash, on a bench that may have no
    fastboot at all."""
    _sysfs_tree(tmp_path)
    monkeypatch.setattr(agent, "_SYSFS_USB_ROOT", tmp_path)
    locked_during: list[bool] = []
    real = agent._enumerate_sysfs_usb

    def spy(root):
        locked_during.append(agent._job_lock.locked())
        return real(root)

    monkeypatch.setattr(agent, "_enumerate_sysfs_usb", spy)
    async with agent._job_lock:  # a flash is running
        sig, data_ws, dial = await _open(monkeypatch, "usb-list", _req(op="usb-list"), fastboot="")
    assert sig.errors() == []
    assert dial.calls == 1
    done = data_ws.done()
    assert done["ok"] is True
    assert done["stdout"] == ""
    assert [d["vid"] for d in done["data"]["devices"]] == ["18d1", "18d1"]
    assert locked_during == [True], "ran while the flash held the lock — without waiting for it"


async def test_exclusive_op_is_refused_while_the_lock_is_held(monkeypatch):
    async with agent._job_lock:
        sig, _, dial = await _open(monkeypatch, "erase")
    assert sig.errors() == ["another flash job is already running"]
    assert dial.calls == 0, "refused before dialling back"


async def test_exclusive_op_takes_the_lock(monkeypatch):
    locked_during: list[bool] = []

    async def fake_run(_ws, argv, timeout_s=0.0):
        locked_during.append(agent._job_lock.locked())
        return 0, "", ""

    async def yes(_fastboot):
        return True

    monkeypatch.setattr(agent, "_run_fastboot", fake_run)
    monkeypatch.setattr(agent, "_device_present", yes)
    sig, data_ws, _ = await _open(monkeypatch, "erase", _req(op="erase", partition="misc"))
    assert sig.errors() == []
    assert data_ws.done()["ok"] is True
    assert locked_during == [True]
    assert not agent._job_lock.locked(), "and releases it"


# ── back to back: run_plan opens the next row the moment it reads `done` ──


class _ReplyingWs(_FakeWs):
    """A data ws that, when it carries `done`, wakes the "hub" — which opens
    the next row at once, as run_plan does (src/alb/remote/flash.py)."""

    def __init__(self, incoming: bytes, got_done: asyncio.Event) -> None:
        super().__init__(incoming)
        self._got_done = got_done
        self.locked_at_done: list[bool] = []

    async def send(self, data: bytes) -> None:
        await super().send(data)
        if self.sent[-1].get("ev") == "done":
            self.locked_at_done.append(agent._job_lock.locked())
            self._got_done.set()


class _ClosingDial:
    """ws_connect stand-in: one fresh data ws per dial, picked by the cid in
    the URL, and an exit that yields the way a real close handshake waits on
    the network — the gap the next row used to land in."""

    def __init__(self, requests: dict[str, bytes], got_done: asyncio.Event) -> None:
        self._requests = requests
        self._got_done = got_done
        self.opened: dict[str, _ReplyingWs] = {}

    def __call__(self, url, **_kw):
        (cid,) = parse_qs(urlsplit(url).query)["cid"]
        return _ClosingConn(self, cid)


class _ClosingConn:
    def __init__(self, dial: _ClosingDial, cid: str) -> None:
        self._dial, self._cid = dial, cid

    async def __aenter__(self):
        ws = _ReplyingWs(self._dial._requests[self._cid], self._dial._got_done)
        self._dial.opened[self._cid] = ws
        return ws

    async def __aexit__(self, *_a):
        for _ in range(3):
            await asyncio.sleep(0)
        return False


def _open_frame_cid(op: str, cid: str) -> dict:
    frame = _open_frame(op)
    frame["cid"] = cid
    return frame


def _fake_fastboot(monkeypatch, run) -> None:
    async def yes(_fastboot):
        return True

    monkeypatch.setattr(agent, "_run_fastboot", run)
    monkeypatch.setattr(agent, "_device_present", yes)
    monkeypatch.setattr(agent, "_FASTBOOT_PATH", FB)
    monkeypatch.setattr(agent, "_OPS", _ops(FB))


async def test_the_next_row_opened_on_done_is_not_refused_as_a_running_job(monkeypatch):
    """The 2026-10-20 bench run: getvar answered, and flash uboot_a — opened
    5 ms later — was refused because the getvar channel was still closing."""

    async def fake_run(_ws, argv, timeout_s=0.0):
        return 0, "", ""

    _fake_fastboot(monkeypatch, fake_run)
    got_done = asyncio.Event()
    row = _req(op="erase", partition="misc")
    dial = _ClosingDial({"c1": row, "c2": row}, got_done)
    monkeypatch.setattr(agent, "ws_connect", dial)
    sig = _FakeSignal()
    url = "ws://h/agent/connect"

    async def hub_runs_the_next_row() -> None:
        await got_done.wait()
        await agent._handle_job_channel(url, None, _open_frame_cid("erase", "c2"), sig)

    await asyncio.wait_for(
        asyncio.gather(
            agent._handle_job_channel(url, None, _open_frame_cid("erase", "c1"), sig),
            hub_runs_the_next_row(),
        ),
        2.0,
    )
    assert sig.errors() == []
    assert [dial.opened[c].done()["ok"] for c in ("c1", "c2")] == [True, True]
    assert [dial.opened[c].locked_at_done for c in ("c1", "c2")] == [[True], [True]], (
        "the result goes out while the lock is still held — a stop request "
        "waiting for the lock cannot cut it off"
    )
    assert not agent._job_lock.locked()


async def test_a_read_only_job_ending_does_not_free_the_lock_a_running_flash_holds(
    monkeypatch, tmp_path
):
    """The hand-back is per job: usb-list finishing DURING a flash must not
    release the flash's hold and let a second write in."""
    _sysfs_tree(tmp_path)
    monkeypatch.setattr(agent, "_SYSFS_USB_ROOT", tmp_path)
    flashing, finish = asyncio.Event(), asyncio.Event()

    async def slow_run(_ws, argv, timeout_s=0.0):
        flashing.set()
        await finish.wait()
        return 0, "", ""

    _fake_fastboot(monkeypatch, slow_run)
    got_done = asyncio.Event()
    requests = {"c1": _req(op="erase", partition="misc"), "c2": _req(op="usb-list")}
    dial = _ClosingDial(requests, got_done)
    monkeypatch.setattr(agent, "ws_connect", dial)
    sig = _FakeSignal()
    url = "ws://h/agent/connect"

    async def run() -> None:
        flash = asyncio.create_task(
            agent._handle_job_channel(url, None, _open_frame_cid("erase", "c1"), sig)
        )
        await flashing.wait()
        await agent._handle_job_channel(url, None, _open_frame_cid("usb-list", "c2"), sig)
        assert dial.opened["c2"].done()["ok"] is True
        assert agent._job_lock.locked(), "usb-list's done released the flash's lock"
        finish.set()
        await flash

    await asyncio.wait_for(run(), 2.0)
    assert sig.errors() == []
    assert dial.opened["c1"].done()["ok"] is True
    assert not agent._job_lock.locked()


async def test_fastboot_op_without_fastboot_is_refused_before_dialling(monkeypatch):
    sig, _, dial = await _open(monkeypatch, "erase", fastboot="")
    assert sig.errors() == ["no fastboot executable on this agent host"]
    assert dial.calls == 0


async def test_no_hint_means_an_exclusive_fastboot_job_as_before(monkeypatch):
    sig, _, dial = await _open(monkeypatch, None, fastboot="")
    assert sig.errors() == ["no fastboot executable on this agent host"]
    async with agent._job_lock:
        sig, _, dial = await _open(monkeypatch, None)
    assert sig.errors() == ["another flash job is already running"]
    assert dial.calls == 0


async def test_unknown_hint_dials_back_and_answers_bench_op_unknown(monkeypatch):
    async with agent._job_lock:  # an unknown name cannot need the lock
        sig, data_ws, dial = await _open(monkeypatch, "frobnicate", _req(op="frobnicate"))
    assert sig.errors() == []
    assert dial.calls == 1
    assert data_ws.done()["code"] == "BENCH_OP_UNKNOWN"


# ── image ops share one receive path: boot gets what flash has ──────────


def _image_request(op: str, img: bytes, *, sha256: str | None = None, **extra) -> bytes:
    fields = {
        "op": op,
        "size": len(img),
        "sha256": sha256 or hashlib.sha256(img).hexdigest(),
        **extra,
    }
    return _req(**fields) + _frame(b"D", img)


async def test_boot_receives_verifies_then_runs_with_the_agents_temp_path(monkeypatch):
    img = b"kernel-bytes" * 16
    ran: list[list[str]] = []

    async def fake_run(_ws, argv, timeout_s=0.0):
        ran.append(argv)
        assert _read(argv[2]) == img, "the image is on disk when the tool runs"
        return 0, "", "Booting OKAY\n"

    async def yes(_fastboot):
        return True

    monkeypatch.setattr(agent, "_run_fastboot", fake_run)
    monkeypatch.setattr(agent, "_device_present", yes)
    ws = _FakeWs(_image_request("boot", img), chunk=5)
    await agent._run_job(ws, _ops())
    assert len(ran) == 1
    assert ran[0][:2] == [FB, "boot"] and ran[0][2].endswith("image.bin")
    assert not _exists(ran[0][2]), "temp image removed afterwards"
    done = ws.done()
    assert done["ok"] is True and done["code"] == ""
    assert ws.events("accepted")[0]["detail"] == f"receiving {len(img)} bytes"
    assert ws.events("progress"), "transfer progress must be reported"


async def test_boot_with_a_bad_digest_never_runs_fastboot(monkeypatch):
    ran: list[list[str]] = []

    async def fake_run(_ws, argv, timeout_s=0.0):
        ran.append(argv)
        return 0, "", ""

    async def yes(_fastboot):
        return True

    monkeypatch.setattr(agent, "_run_fastboot", fake_run)
    monkeypatch.setattr(agent, "_device_present", yes)
    ws = _FakeWs(_image_request("boot", b"A" * 64, sha256="00" * 32))
    await agent._run_job(ws, _ops())
    assert ran == [], "fastboot must not run on an unverified image"
    assert ws.done()["code"] == "FLASH_IMAGE_CORRUPT"


async def test_boot_refuses_before_receiving_when_no_device_or_no_space(monkeypatch):
    img = b"z" * 32
    ws, ran = await _dispatch(
        monkeypatch,
        {"op": "boot", "size": len(img), "sha256": hashlib.sha256(img).hexdigest()},
        present=False,
    )
    assert ran == []
    assert ws.done()["code"] == "FASTBOOT_NO_DEVICE"
    assert "nothing was transferred" in ws.done()["error"]

    async def none_free():
        return 0

    monkeypatch.setattr(agent, "_tmp_free_bytes", none_free)
    ws, ran = await _dispatch(
        monkeypatch, {"op": "boot", "size": len(img), "sha256": hashlib.sha256(img).hexdigest()}
    )
    assert ran == []
    assert ws.done()["code"] == "FLASH_AGENT_NO_SPACE"
    assert not ws.events("accepted")


@pytest.mark.parametrize("size", [0, -1, "many", None])
async def test_image_op_with_a_bad_size_is_corrupt_not_a_crash(monkeypatch, size):
    ws, ran = await _dispatch(monkeypatch, {"op": "boot", "size": size, "sha256": "00" * 32})
    assert ran == []
    assert ws.done()["code"] == "FLASH_IMAGE_CORRUPT"


# ── usb-list: the Windows JSON → UsbDev, as a pure function ─────────────

_FASTBOOT_PNP = {
    "Name": "Android Bootloader Interface",
    "PNPDeviceID": "USB\\VID_18D1&PID_D00D\\1000000003",
    "Status": "OK",
    "ConfigManagerErrorCode": 0,
    "PNPClass": "AndroidUsbDeviceClass",
    "Service": "WinUSB",
    "Manufacturer": "Example",
    "CompatibleID": [
        "USB\\Class_ff&SubClass_42&Prot_03",
        "USB\\Class_ff&SubClass_42",
        "USB\\Class_ff",
    ],
}
_ADB_INTERFACE_PNP = {
    "Name": "ADB Interface",
    "PNPDeviceID": "USB\\VID_1234&PID_5678&MI_01\\7&2B3C4D5E&0&0001",
    "Status": "OK",
    "ConfigManagerErrorCode": 0,
    "PNPClass": "USBDevice",
    "Service": "WinUSB",
    "Manufacturer": "Example",
    "CompatibleID": "USB\\Class_ff&SubClass_42&Prot_01",  # one entry → bare string
}
_NO_DRIVER_PNP = {
    "Name": "Android",
    "PNPDeviceID": "USB\\VID_1234&PID_D00D\\0123456789",
    "Status": "Error",
    "ConfigManagerErrorCode": 28,
    "PNPClass": None,
    "Service": None,
    "Manufacturer": None,
    "CompatibleID": [
        "USB\\Class_ff&SubClass_42&Prot_03",
        "USB\\Class_ff&SubClass_42",
        "USB\\Class_ff",
    ],
}

_USBDEV_KEYS = {
    "name",
    "instance_id",
    "vid",
    "pid",
    "serial",
    "interface",
    "status",
    "problem",
    "class",
    "service",
    "manufacturer",
    "compatible_ids",
}


def test_usb_json_fastboot_device():
    [dev] = agent._parse_usb_json(json.dumps([_FASTBOOT_PNP]))
    assert set(dev) == _USBDEV_KEYS
    assert dev["vid"] == "18d1" and dev["pid"] == "d00d"
    assert dev["serial"] == "1000000003"
    assert dev["interface"] == ""
    assert dev["problem"] == 0
    assert dev["service"] == "WinUSB" and dev["class"] == "AndroidUsbDeviceClass"
    assert dev["compatible_ids"] == _FASTBOOT_PNP["CompatibleID"]


def test_usb_json_composite_adb_interface_with_a_single_compatible_id():
    [dev] = agent._parse_usb_json(json.dumps(_ADB_INTERFACE_PNP))  # single object, not a list
    assert dev["vid"] == "1234" and dev["pid"] == "5678"
    assert dev["interface"] == "01"
    assert dev["serial"] == "7&2B3C4D5E&0&0001", "the last segment, raw — a bus token here"
    assert dev["compatible_ids"] == ["USB\\Class_ff&SubClass_42&Prot_01"]


def test_usb_json_device_with_no_driver_keeps_the_problem_code_and_the_class_triple():
    """Problem 28 (no driver) is the case usb-list was built for: the triple
    is still there for the hub to say "that is a fastboot interface"."""
    [dev] = agent._parse_usb_json(json.dumps([_NO_DRIVER_PNP]))
    assert dev["problem"] == 28
    assert dev["status"] == "Error"
    assert dev["service"] == "" and dev["class"] == "" and dev["manufacturer"] == ""
    assert dev["compatible_ids"][0] == "USB\\Class_ff&SubClass_42&Prot_03"


def test_usb_json_several_devices_keep_their_order():
    devs = agent._parse_usb_json(json.dumps([_FASTBOOT_PNP, _ADB_INTERFACE_PNP, _NO_DRIVER_PNP]))
    assert [d["serial"] for d in devs] == ["1000000003", "7&2B3C4D5E&0&0001", "0123456789"]


@pytest.mark.parametrize("text", ["", "   \r\n"])
def test_usb_json_no_match_prints_nothing_and_that_is_an_empty_list(text):
    assert agent._parse_usb_json(text) == []


def test_usb_json_tolerates_missing_and_odd_fields():
    [dev] = agent._parse_usb_json(
        json.dumps(
            [
                {
                    "PNPDeviceID": "USB\\ROOT_HUB30\\4&1A2B&0",
                    "CompatibleID": None,
                    "ConfigManagerErrorCode": None,
                }
            ]
        )
    )
    assert dev["vid"] == "" and dev["pid"] == "" and dev["interface"] == ""
    assert dev["serial"] == "4&1A2B&0"
    assert dev["problem"] is None
    assert dev["compatible_ids"] == []
    assert dev["name"] == ""


def test_usb_json_garbage_is_an_error_not_a_listing():
    with pytest.raises(ValueError):
        agent._parse_usb_json("not json")
    with pytest.raises(ValueError):
        agent._parse_usb_json("42")


def test_usb_powershell_command_is_fixed_and_shell_free():
    """The one fixed command line: nothing from the hub can reach it, and it
    is passed as argv, not through a shell."""
    assert agent._USB_PS_ARGV[0] == "powershell"
    assert "-NoProfile" in agent._USB_PS_ARGV and "-NonInteractive" in agent._USB_PS_ARGV
    assert "{" not in agent._USB_PS_QUERY.replace("{ $_.PNPDeviceID", "")  # no format placeholders
    assert "CompatibleID" in agent._USB_PS_QUERY and "ConvertTo-Json" in agent._USB_PS_QUERY


# ── usb-list on a non-Windows host: sysfs, with an injectable root ──────


def test_sysfs_enumeration_lists_device_then_interfaces(tmp_path):
    _sysfs_tree(tmp_path)
    devs = agent._enumerate_sysfs_usb(tmp_path)
    assert [d["instance_id"] for d in devs] == ["1-3", "1-3:1.0"], "root hub and alias excluded"
    dev, itf = devs
    assert set(dev) == _USBDEV_KEYS
    assert dev["vid"] == "18d1" and dev["pid"] == "d00d" and dev["serial"] == "1000000003"
    assert dev["name"] == "Android" and dev["manufacturer"] == "Example"
    assert dev["service"] == "usb" and dev["interface"] == ""
    assert dev["compatible_ids"] == [
        "USB\\Class_00&SubClass_00&Prot_00",
        "USB\\Class_ff&SubClass_42&Prot_03",
    ], "device triple first, then interfaces by dir name"
    assert itf["interface"] == "00"
    assert itf["service"] == "", "no driver bound to the fastboot interface"
    assert itf["compatible_ids"] == ["USB\\Class_ff&SubClass_42&Prot_03"]
    assert itf["serial"] == "1000000003"


def test_sysfs_enumeration_without_the_tree_is_empty_not_a_crash(tmp_path):
    assert agent._enumerate_sysfs_usb(tmp_path / "absent") == []
    (tmp_path / "2-1").mkdir()  # a device dir with nothing readable in it
    [dev] = agent._enumerate_sysfs_usb(tmp_path)
    assert dev["vid"] == "" and dev["compatible_ids"] == [] and dev["service"] == ""


# ── log-tail: a name, never a path ──────────────────────────────────────


def test_tail_decodes_utf16_le_with_bom(tmp_path):
    p = tmp_path / "setupapi.dev.log"
    _write(p, "﻿alpha\r\nbeta\r\ngamma\r\n".encode("utf-16-le"))
    rows, truncated = agent._tail_log(p, 2, "")
    assert rows == ["beta", "gamma"]
    assert truncated is False


def test_tail_decodes_utf16_be_and_utf8_sig(tmp_path):
    be = tmp_path / "be.log"
    _write(be, "﻿one\ntwo\n".encode("utf-16-be"))
    assert agent._tail_log(be, 5, "")[0] == ["one", "two"]
    sig = tmp_path / "sig.log"
    _write(sig, b"\xef\xbb\xbffirst\nsecond\n")
    assert agent._tail_log(sig, 5, "")[0] == ["first", "second"], "BOM not glued to line 1"


def test_tail_plain_utf8_with_a_bad_byte_is_replaced_not_fatal(tmp_path):
    p = tmp_path / "agent.log"
    _write(p, b"ok line\nbad \xff byte\n")
    rows, _ = agent._tail_log(p, 5, "")
    assert rows[0] == "ok line" and "bad" in rows[1]


def test_tail_grep_is_a_case_insensitive_literal(tmp_path):
    p = tmp_path / "agent.log"
    _write(p, b"ERROR one\ninfo\nerror two\na.b\n")
    assert agent._tail_log(p, 50, "error")[0] == ["ERROR one", "error two"]
    assert agent._tail_log(p, 50, "a.b")[0] == ["a.b"], "a dot is a dot, not a regex"
    assert agent._tail_log(p, 50, "nothing")[0] == []


def test_tail_returns_the_last_n_matching_lines(tmp_path):
    p = tmp_path / "agent.log"
    _write(p, "".join(f"line {i}\n" for i in range(500)).encode())
    rows, truncated = agent._tail_log(p, 3, "")
    assert rows == ["line 497", "line 498", "line 499"]
    assert truncated is False


def test_tail_truncated_when_the_window_does_not_reach_the_start(tmp_path):
    p = tmp_path / "agent.log"
    _write(p, "".join(f"row {i:04d}\n" for i in range(100)).encode())  # 900 bytes
    rows, truncated = agent._tail_log(p, 1000, "", window=100)
    assert truncated is True
    assert rows[0] != "" and rows[0].startswith("row "), "the cut fragment is dropped"
    assert len(rows) < 100 and rows[-1] == "row 0099"
    assert agent._tail_log(p, 1000, "", window=10_000)[1] is False


def test_tail_utf16_window_stays_on_a_code_unit_boundary(tmp_path):
    p = tmp_path / "w.log"
    _write(p, ("﻿" + "".join(f"r{i}\n" for i in range(200))).encode("utf-16-le"))
    rows, truncated = agent._tail_log(p, 3, "", window=101)  # an odd window
    assert truncated is True
    assert rows == ["r197", "r198", "r199"]


async def test_log_tail_through_the_table(monkeypatch, tmp_path):
    log = tmp_path / "agent.log"
    _write(log, b"boot\nerror: x\nfine\nerror: y\n")
    ops = _ops("", {"agent": log})
    assert ops["log-tail"].params[0].choices == ("agent",)
    ws, ran = await _dispatch(
        monkeypatch, {"op": "log-tail", "source": "agent", "grep": "ERROR", "lines": 1}, ops=ops
    )
    assert ran == []
    done = ws.done()
    assert done["ok"] is True
    assert done["stdout"] == "error: y"
    assert done["data"] == {"source": "agent", "lines": 1, "truncated": False}
    ws, _ = await _dispatch(monkeypatch, {"op": "log-tail", "source": "agent"}, ops=ops)
    assert ws.done()["stdout"] == "boot\nerror: x\nfine\nerror: y"
    assert ws.done()["data"]["lines"] == 4


@pytest.mark.parametrize(
    "req",
    [
        {"op": "log-tail", "source": "setupapi"},  # not configured here
        {"op": "log-tail", "source": "/etc/passwd"},  # a path is never a source
        {"op": "log-tail"},  # source is required
        {"op": "log-tail", "source": "agent", "lines": True},  # bool is not an int
        {"op": "log-tail", "source": "agent", "lines": 0},
        {"op": "log-tail", "source": "agent", "grep": "a\nb"},
    ],
)
async def test_log_tail_rejects_unknown_source_and_bad_params(monkeypatch, tmp_path, req):
    ops = _ops("", {"agent": tmp_path / "agent.log"})
    ws, _ = await _dispatch(monkeypatch, req, ops=ops)
    assert ws.done()["code"] == "BENCH_PARAM_REJECTED"
    assert not ws.events("accepted")


async def test_log_tail_missing_file_is_log_unavailable(monkeypatch, tmp_path):
    ops = _ops("", {"agent": tmp_path / "not-written-yet.log"})
    ws, _ = await _dispatch(monkeypatch, {"op": "log-tail", "source": "agent"}, ops=ops)
    done = ws.done()
    assert done["ok"] is False
    assert done["code"] == "BENCH_LOG_UNAVAILABLE"


def test_log_sources_come_from_config_not_from_the_hub(monkeypatch, tmp_path):
    monkeypatch.setattr(agent, "_AGENT_LOG_PATH", None)
    monkeypatch.setattr(agent.sys, "platform", "linux")
    assert agent._log_sources() == {}
    monkeypatch.setattr(agent, "_AGENT_LOG_PATH", tmp_path / "agent.log")
    assert agent._log_sources() == {"agent": tmp_path / "agent.log"}
    monkeypatch.setattr(agent.sys, "platform", "win32")
    monkeypatch.setenv("WINDIR", str(tmp_path / "Windows"))
    assert agent._log_sources()["setupapi"] == tmp_path / "Windows" / "INF" / "setupapi.dev.log"


def test_file_logging_records_the_path_log_tail_reads(tmp_path, monkeypatch):
    import logging

    monkeypatch.setattr(agent, "_AGENT_LOG_PATH", None)
    target = tmp_path / "logs" / "a.log"
    agent._setup_file_logging(str(target))
    try:
        assert agent._AGENT_LOG_PATH == target
    finally:
        for h in list(logging.getLogger().handlers):
            if getattr(h, "baseFilename", "") == str(target):
                logging.getLogger().removeHandler(h)
                h.close()


# ── review fixes (2026-10-15) ────────────────────────────────────────────


async def test_the_lock_is_taken_before_dialling_back(monkeypatch):
    """Dialling yields; holding the lock across it is what makes a second
    request in that gap a refusal instead of a job queued behind the first."""
    held_at_dial: list[bool] = []

    class SpyDial(_FakeDial):
        def __call__(self, url, **kw):
            held_at_dial.append(agent._job_lock.locked())
            return super().__call__(url, **kw)

    async def fake_run(_ws, argv, timeout_s=0.0):
        return 0, "", ""

    async def yes(_fastboot):
        return True

    data_ws = _FakeWs(_req(op="erase", partition="misc"))
    monkeypatch.setattr(agent, "ws_connect", SpyDial(data_ws))
    monkeypatch.setattr(agent, "_FASTBOOT_PATH", FB)
    monkeypatch.setattr(agent, "_OPS", _ops(FB))
    monkeypatch.setattr(agent, "_run_fastboot", fake_run)
    monkeypatch.setattr(agent, "_device_present", yes)
    sig = _FakeSignal()
    await asyncio.wait_for(
        agent._handle_job_channel("ws://h/agent/connect", None, _open_frame("erase"), sig), 2.0
    )
    assert held_at_dial == [True]
    assert data_ws.done()["ok"] is True
    assert not agent._job_lock.locked()


async def test_a_second_request_while_the_first_is_dialling_is_refused_not_queued(monkeypatch):
    gate = asyncio.Event()
    dialled = asyncio.Event()

    class SlowDial(_FakeDial):
        def __call__(self, url, **kw):
            dialled.set()
            return super().__call__(url, **kw)

        async def __aenter__(self):
            await gate.wait()
            return self.data_ws

    async def fake_run(_ws, argv, timeout_s=0.0):
        return 0, "", ""

    async def yes(_fastboot):
        return True

    dial = SlowDial(_FakeWs(_req(op="erase", partition="misc")))
    monkeypatch.setattr(agent, "ws_connect", dial)
    monkeypatch.setattr(agent, "_FASTBOOT_PATH", FB)
    monkeypatch.setattr(agent, "_OPS", _ops(FB))
    monkeypatch.setattr(agent, "_run_fastboot", fake_run)
    monkeypatch.setattr(agent, "_device_present", yes)
    sig = _FakeSignal()
    first = asyncio.create_task(
        agent._handle_job_channel("ws://h/agent/connect", None, _open_frame("erase"), sig)
    )
    await asyncio.wait_for(dialled.wait(), 2.0)
    second = dict(_open_frame("erase"), cid="c2")
    await asyncio.wait_for(
        agent._handle_job_channel("ws://h/agent/connect", None, second, sig), 2.0
    )
    assert sig.errors() == ["another flash job is already running"]
    assert dial.calls == 1, "the second request never dialled"
    gate.set()
    await asyncio.wait_for(first, 2.0)
    assert not agent._job_lock.locked()


class _Spawned(list):
    """Every child the agent starts, plus an event set at the first one."""

    def __init__(self) -> None:
        super().__init__()
        self.first = asyncio.Event()


async def _spawned(monkeypatch) -> _Spawned:
    procs = _Spawned()
    real = asyncio.create_subprocess_exec

    async def spy(*a, **kw):
        proc = await real(*a, **kw)
        procs.append(proc)
        procs.first.set()
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)
    return procs


_SLEEPER = [sys.executable, "-c", "import time; time.sleep(30)"]


async def test_a_cancelled_fastboot_is_killed_and_reaped(monkeypatch):
    """Agent stopping or session dropped mid-flash: the child must not keep
    writing the partition (or hold the temp image open) after the job ends."""
    procs = await _spawned(monkeypatch)
    task = asyncio.create_task(agent._run_fastboot(_FakeWs(), _SLEEPER, 60.0))
    await asyncio.wait_for(procs.first.wait(), 5.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert procs[0].returncode is not None, "killed and waited for"


async def _hanging_fastboot(tmp_path: Path) -> str:
    exe = tmp_path / "fastboot"
    exe.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(30)\n")
    exe.chmod(0o755)
    return str(exe)


@pytest.mark.skipif(sys.platform == "win32", reason="shebang script stands in for fastboot")
async def test_a_hung_device_query_is_not_reported_as_no_device(monkeypatch, tmp_path):
    """A stuck USB link or driver must not read as "the board is not in
    fastboot" — that sends the operator to the wrong end."""
    procs = await _spawned(monkeypatch)
    monkeypatch.setattr(agent, "_DEVICE_QUERY_TIMEOUT_S", 0.3)
    with pytest.raises(agent._JobRejected) as e:
        await agent._device_present(await _hanging_fastboot(tmp_path))
    assert e.value.code == "FLASH_FAILED"
    assert "did not answer" in e.value.message
    assert procs[0].returncode is not None, "the hung query is reaped"


async def test_a_refusal_from_the_device_check_still_ends_the_job(monkeypatch):
    async def stuck(_fastboot):
        raise agent._JobRejected("FLASH_FAILED", "`fastboot devices` did not answer")

    monkeypatch.setattr(agent, "_device_present", stuck)
    ws = _FakeWs(_req(op="set-active", slot="a"))
    await agent._run_job(ws, _ops())
    assert ws.done()["code"] == "FLASH_FAILED"


async def test_an_unexpected_error_still_ends_the_job_with_a_done_frame(monkeypatch):
    """e.g. fastboot deleted after the agent started: create_subprocess_exec
    raises. Without a done frame the hub only sees a dropped channel."""

    async def gone(_ws, argv, timeout_s=0.0):
        raise FileNotFoundError(2, "No such file or directory", argv[0])

    async def yes(_fastboot):
        return True

    monkeypatch.setattr(agent, "_run_fastboot", gone)
    monkeypatch.setattr(agent, "_device_present", yes)
    ws = _FakeWs(_req(op="set-active", slot="a"))
    await agent._run_job(ws, _ops())
    done = ws.done()
    assert done["ok"] is False and done["code"] == "FLASH_FAILED"
    assert "FileNotFoundError" in done["error"]


async def test_log_tail_sends_whole_lines_that_fit_and_counts_them(tmp_path):
    """The done frame keeps only the last _JOB_OUTPUT_CAP characters. Asking
    for 2000 lines of ~150 characters must not deliver a half line at the
    top while `lines` claims 2000 and `truncated` says nothing is missing."""
    rows = [f"{i:05d} " + "x" * 144 for i in range(2000)]
    log = tmp_path / "agent.log"
    log.write_text("\n".join(rows) + "\n", encoding="utf-8")
    out, data = await agent._op_log_tail(
        None, {"source": "agent", "lines": 2000, "grep": ""}, sources={"agent": log}
    )
    assert len(out) <= agent._JOB_OUTPUT_CAP
    got = out.split("\n")
    assert got == rows[-len(got) :], "every line whole, newest last"
    assert data["lines"] == len(got) < 2000
    assert data["truncated"] is True


def test_fit_rows_keeps_the_end_of_a_single_overlong_line():
    rows, clipped = agent._fit_rows(["a" * 10], 4)
    assert rows == ["aaaa"] and clipped is True
    assert agent._fit_rows(["ab", "cd"], 5) == (["ab", "cd"], False)


async def test_usb_list_on_a_host_without_sysfs_is_refused_not_empty(monkeypatch, tmp_path):
    """An empty list must mean "nothing plugged in", never "nowhere to look"."""
    if sys.platform == "win32":
        pytest.skip("the sysfs branch is the non-Windows one")
    monkeypatch.setattr(agent, "_SYSFS_USB_ROOT", tmp_path / "no-such-dir")
    spec = _ops(FB)["usb-list"]
    with pytest.raises(agent._JobRejected) as e:
        await agent._op_usb_list(spec, {})
    assert e.value.code == "FLASH_FAILED"


@pytest.mark.parametrize("name", ["misc\n", "boot_a\n"])
def test_agent_shape_rules_do_not_let_a_trailing_newline_through(name):
    assert agent._partition_allowed(name) is False
    assert agent._GETVAR_RE.match(name) is None
