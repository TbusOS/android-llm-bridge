"""Config file (agent.conf) loading + precedence of the standalone agent.

The parser is hand-rolled, so these pin the behaviours the example file
promises: Notepad's UTF-8 BOM, CRLF endings, '=' and '#' inside values,
surrounding-quote stripping, loud failure on unknown keys, and the
CLI > config > built-in-default precedence (including the argparse
set_defaults type pitfall — status_port must arrive as an int).
"""

from __future__ import annotations

import importlib.util
import logging
import re
import sys
import threading
import time
from pathlib import Path

import pytest

_AGENT_PATH = Path(__file__).resolve().parents[2] / "clients" / "windows-agent" / "alb_agent.py"


def _load_agent():
    spec = importlib.util.spec_from_file_location("alb_agent_config_under_test", _AGENT_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


agent = _load_agent()


def _write(tmp_path: Path, text: str, *, encoding: str = "utf-8") -> Path:
    p = tmp_path / "agent.conf"
    p.write_bytes(text.encode(encoding))
    return p


# ── _load_config ─────────────────────────────────────────────────────


def test_basic_key_values(tmp_path):
    p = _write(tmp_path, "hub_url=ws://hub:8765/agent/connect\ntoken=abc\nname=bench-01\n")
    assert agent._load_config(p) == {
        "hub_url": "ws://hub:8765/agent/connect",
        "token": "abc",
        "name": "bench-01",
    }


def test_notepad_bom_and_crlf(tmp_path):
    # Notepad saves UTF-8 with BOM and CRLF — the first key must not become
    # '<BOM>hub_url'.
    p = _write(tmp_path, "\ufeffhub_url=ws://h/agent/connect\r\ntoken=t\r\n")
    assert agent._load_config(p) == {"hub_url": "ws://h/agent/connect", "token": "t"}


def test_comments_and_blank_lines_skipped(tmp_path):
    p = _write(tmp_path, "# comment\n\n  \nhub_url=ws://h\n# token=ignored\n")
    assert agent._load_config(p) == {"hub_url": "ws://h"}


def test_value_may_contain_equals_and_hash(tmp_path):
    # Tokens may contain '=' (base64) and '#' — only the FIRST '=' splits,
    # and there are no inline comments.
    p = _write(tmp_path, "token=abc=def#ghi\n")
    assert agent._load_config(p) == {"token": "abc=def#ghi"}


def test_surrounding_quotes_stripped(tmp_path):
    p = _write(tmp_path, "token=\"abc\"\nname='bench'\n")
    assert agent._load_config(p) == {"token": "abc", "name": "bench"}


def test_whitespace_around_key_and_value(tmp_path):
    p = _write(tmp_path, "  token =  abc  \n")
    assert agent._load_config(p) == {"token": "abc"}


def test_empty_value_means_unset(tmp_path):
    p = _write(tmp_path, "token=\nhub_url=ws://h\n")
    assert agent._load_config(p) == {"hub_url": "ws://h"}


def test_status_port_converted_to_int(tmp_path):
    p = _write(tmp_path, "status_port=9000\n")
    assert agent._load_config(p) == {"status_port": 9000}


def test_bad_int_is_fatal(tmp_path):
    p = _write(tmp_path, "status_port=lots\n")
    with pytest.raises(SystemExit) as ei:
        agent._load_config(p)
    assert "status_port" in str(ei.value)


def test_status_port_out_of_range_is_fatal(tmp_path):
    # ThreadingHTTPServer raises OverflowError (not OSError) past 65535 —
    # reject it at parse time with a file:line message instead.
    p = _write(tmp_path, "status_port=70000\n")
    with pytest.raises(SystemExit) as ei:
        agent._load_config(p)
    assert "status_port" in str(ei.value)


def test_duplicate_key_last_wins(tmp_path):
    p = _write(tmp_path, "name=first\nname=second\n")
    assert agent._load_config(p) == {"name": "second"}


def test_shipped_example_parses_with_real_loader():
    # The example is the first step of the double-click path — it must always
    # parse, and must not demonstrate keys the loader doesn't accept.
    cfg = agent._load_config(_AGENT_PATH.parent / "agent.conf.example")
    assert set(cfg) <= set(agent._CONFIG_KEYS)
    assert cfg["hub_url"]


def test_unknown_key_is_fatal_and_lists_valid_keys(tmp_path):
    # A typo ('hub-url') must not be silently swallowed by set_defaults.
    p = _write(tmp_path, "hub-url=ws://h\n")
    with pytest.raises(SystemExit) as ei:
        agent._load_config(p)
    assert "unknown key" in str(ei.value)
    assert "hub_url" in str(ei.value)


def test_line_without_equals_is_fatal(tmp_path):
    p = _write(tmp_path, "just some text\n")
    with pytest.raises(SystemExit) as ei:
        agent._load_config(p)
    assert "KEY=VALUE" in str(ei.value)


# ── _parse_args precedence ───────────────────────────────────────────


def test_cli_beats_config_beats_default(tmp_path):
    p = _write(
        tmp_path,
        "hub_url=ws://from-config/agent/connect\nname=config-name\nstatus_port=9000\n",
    )
    args = agent._parse_args(["--config", str(p), "--name", "cli-name"])
    assert args.hub_url == "ws://from-config/agent/connect"  # config > built-in
    assert args.name == "cli-name"  # CLI > config
    assert args.status_port == 9000  # config, converted
    assert isinstance(args.status_port, int)
    assert args.status_host == "127.0.0.1"  # built-in default


def test_status_port_zero_via_config_disables(tmp_path):
    # 0 must arrive as int 0 (falsy → status page disabled), not "0".
    p = _write(tmp_path, "hub_url=ws://h\nstatus_port=0\n")
    args = agent._parse_args(["--config", str(p)])
    assert args.status_port == 0
    assert isinstance(args.status_port, int)


def test_missing_hub_url_everywhere_is_fatal(tmp_path):
    p = _write(tmp_path, "name=bench\n")
    with pytest.raises(SystemExit):
        agent._parse_args(["--config", str(p)])


def test_explicit_config_must_exist(tmp_path):
    with pytest.raises(SystemExit):
        agent._parse_args(["--config", str(tmp_path / "nope.conf"), "--hub-url", "ws://h"])


def test_default_config_next_to_script_is_picked_up(tmp_path, monkeypatch):
    p = _write(tmp_path, "hub_url=ws://default-conf/agent/connect\nagent_id=bench-fixed\n")
    monkeypatch.setattr(agent, "_default_config_path", lambda: p)
    args = agent._parse_args([])
    assert args.hub_url == "ws://default-conf/agent/connect"
    assert args.agent_id == "bench-fixed"  # stable identity from the file


def test_no_config_no_flags_is_fatal(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "_default_config_path", lambda: tmp_path / "absent.conf")
    with pytest.raises(SystemExit):
        agent._parse_args([])


def test_agent_id_random_when_unset(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "_default_config_path", lambda: tmp_path / "absent.conf")
    a1 = agent._parse_args(["--hub-url", "ws://h"])
    a2 = agent._parse_args(["--hub-url", "ws://h"])
    assert a1.agent_id and a2.agent_id and a1.agent_id != a2.agent_id


# ── file logging ─────────────────────────────────────────────────────


def test_log_file_key_parses(tmp_path):
    p = _write(tmp_path, "log_file=logs/agent.log\n")
    assert agent._load_config(p) == {"log_file": "logs/agent.log"}


def test_setup_file_logging_creates_rotating_handler(tmp_path):
    import logging

    root = logging.getLogger()
    before = list(root.handlers)
    agent._setup_file_logging(str(tmp_path / "logs" / "a.log"))
    added = [h for h in root.handlers if h not in before]
    try:
        assert len(added) == 1  # parent dir auto-created, handler attached
        # warning: INFO would be gated by the root logger's default WARNING
        # level in the test env (production main() sets the root to INFO).
        logging.getLogger("alb-agent").warning("post-mortem line")
        added[0].flush()
        text = (tmp_path / "logs" / "a.log").read_text(encoding="utf-8")
        assert "post-mortem line" in text
    finally:
        for h in added:
            root.removeHandler(h)
            h.close()


def test_sigint_fallback_first_graceful_second_hard_exit(monkeypatch, capsys):
    # Windows fallback: 1st Ctrl-C → KeyboardInterrupt (asyncio.run teardown);
    # 2nd → os._exit, so a wedged worker thread can never trap the console.
    codes: list[int] = []
    monkeypatch.setattr(agent.os, "_exit", lambda code: codes.append(code))
    handler = agent._make_sigint_handler()
    with pytest.raises(KeyboardInterrupt):
        handler(2, None)
    assert codes == []
    assert "Ctrl-C again" in capsys.readouterr().err
    handler(2, None)  # no raise this time — straight to hard exit
    assert codes == [130]


def test_setup_file_logging_none_disables():
    import logging

    root = logging.getLogger()
    before = list(root.handlers)
    agent._setup_file_logging("none")
    assert root.handlers == before


# ── console window output must never hold the agent up ──────────────────
#
# Hit for real 2026-10-16: a text selection in the agent's console window
# blocks every write to it. Logging went straight to the window from the
# event loop, so the whole agent stopped — an update's answer never left
# and the helper had to kill it.


class _SelectedConsole:
    """A console window with text selected: writes wait for the selection
    to be cleared."""

    def __init__(self) -> None:
        self.cleared = threading.Event()
        self.text: list[str] = []

    def write(self, s: str) -> None:
        self.cleared.wait()
        self.text.append(s)

    def flush(self) -> None:
        pass


def _console_log(stream: _SelectedConsole) -> logging.Handler:
    h = agent._ConsoleLog(stream)
    h.setFormatter(logging.Formatter("%(message)s"))
    return h


def _record(msg: str) -> logging.LogRecord:
    return logging.LogRecord("alb-agent", logging.INFO, __file__, 0, msg, None, None)


def _wait_for(cond, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not cond() and time.monotonic() < deadline:
        time.sleep(0.01)


def test_a_selected_console_window_does_not_block_logging():
    console = _SelectedConsole()
    h = _console_log(console)
    try:
        # In a thread so a regression fails here instead of hanging the run.
        caller = threading.Thread(
            target=lambda: [h.handle(_record(f"line {i}")) for i in range(50)], daemon=True
        )
        caller.start()
        caller.join(timeout=2.0)
        assert not caller.is_alive(), "logging waited on the console window"
        assert console.text == []

        console.cleared.set()
        _wait_for(lambda: "".join(console.text).count("\n") >= 50)
        assert "".join(console.text).splitlines() == [f"line {i}" for i in range(50)]
    finally:
        console.cleared.set()
        h.close()


def test_lines_past_the_backlog_are_counted_and_reported(monkeypatch):
    monkeypatch.setattr(agent._ConsoleLog, "_BACKLOG", 3)
    console = _SelectedConsole()
    h = _console_log(console)
    try:
        for i in range(10):
            h.handle(_record(f"line {i}"))
        console.cleared.set()

        def accounted() -> int:
            out = "".join(console.text)
            skipped = sum(int(n) for n in re.findall(r"\((\d+) log lines were skipped", out))
            return len(re.findall(r"^line \d+$", out, re.M)) + skipped

        _wait_for(lambda: accounted() >= 10)
        out = "".join(console.text)
        assert "log lines were skipped" in out, out
        assert accounted() == 10, out  # every line either shown or counted
    finally:
        console.cleared.set()
        h.close()


def test_exit_does_not_wait_for_a_window_that_stays_selected():
    console = _SelectedConsole()
    h = _console_log(console)
    h.handle(_record("last words"))
    started = time.monotonic()
    h.close()
    assert time.monotonic() - started < 4.0
    console.cleared.set()


# ── config keys vs command-line flags must stay in lockstep ─────────────
#
# The bug this locks (hit for real 2026-08-07): `--fastboot-path` was added
# to argparse but not to `_CONFIG_KEYS`. An unknown key is a HARD ERROR by
# design, so the agent refused to start and told the operator the key did not
# exist — which reads as "this build cannot do fastboot", not "use the flag
# instead". A flag nobody can put in agent.conf is a flag nobody uses.

# dests that are deliberately command-line only.
_CLI_ONLY_DESTS = {
    "config",  # chicken-and-egg: it names the file being read
    "config_used",  # set after parsing, not an input at all
    "verbose",  # a per-run debugging choice, not bench configuration
    "self_check",  # a mode the update path runs, not bench configuration
    "config_explicit",  # set after parsing, not an input at all
    "agent_id_explicit",  # set after parsing, not an input at all
}


def test_every_flag_is_settable_from_the_config_file(tmp_path):
    cfg = tmp_path / "agent.conf"
    cfg.write_text("hub_url=ws://h/agent/connect\n", encoding="utf-8")
    args = agent._parse_args(["--config", str(cfg)])
    dests = {k for k in vars(args) if not k.startswith("_")}
    missing = dests - _CLI_ONLY_DESTS - set(agent._CONFIG_KEYS)
    assert not missing, (
        f"these flags cannot be set from agent.conf: {sorted(missing)} — "
        "add them to _CONFIG_KEYS or to _CLI_ONLY_DESTS"
    )


def test_every_config_key_reaches_a_real_flag(tmp_path):
    """The other direction: a key in _CONFIG_KEYS with no matching argparse
    dest would be silently accepted and then ignored."""
    cfg = tmp_path / "agent.conf"
    cfg.write_text("hub_url=ws://h/agent/connect\n", encoding="utf-8")
    args = agent._parse_args(["--config", str(cfg)])
    orphans = set(agent._CONFIG_KEYS) - set(vars(args))
    assert not orphans, f"config keys that go nowhere: {sorted(orphans)}"


def test_flash_keys_load_from_the_config_file(tmp_path):
    cfg = tmp_path / "agent.conf"
    cfg.write_text(
        "hub_url=ws://h/agent/connect\n"
        "fastboot_path=D:\\platform-tools\\fastboot.exe\n"
        "flash_partitions=vendor_cfg,boot\n",
        encoding="utf-8",
    )
    args = agent._parse_args(["--config", str(cfg)])
    assert args.fastboot_path == "D:\\platform-tools\\fastboot.exe"
    assert args.flash_partitions == "vendor_cfg,boot"


def test_flash_config_reaches_the_module_globals(tmp_path, monkeypatch):
    """_apply_flash_config is what turns the parsed values into the state the
    hello frame reads — a value that stops at the Namespace changes nothing."""
    exe = tmp_path / "fastboot"
    exe.write_text("#!/bin/sh\n")
    cfg = tmp_path / "agent.conf"
    cfg.write_text(
        f"hub_url=ws://h/agent/connect\nfastboot_path={exe}\nflash_partitions=boot, dtbo\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(agent, "_FASTBOOT_PATH", "")
    monkeypatch.setattr(agent, "_FLASH_PARTITIONS", frozenset())
    agent._apply_flash_config(agent._parse_args(["--config", str(cfg)]))
    assert agent._FASTBOOT_PATH == str(exe)
    assert agent._FLASH_PARTITIONS == frozenset({"boot", "dtbo"})  # whitespace trimmed
