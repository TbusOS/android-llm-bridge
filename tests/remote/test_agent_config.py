"""Config file (agent.conf) loading + precedence of the standalone agent.

The parser is hand-rolled, so these pin the behaviours the example file
promises: Notepad's UTF-8 BOM, CRLF endings, '=' and '#' inside values,
surrounding-quote stripping, loud failure on unknown keys, and the
CLI > config > built-in-default precedence (including the argparse
set_defaults type pitfall — status_port must arrive as an int).
"""

from __future__ import annotations

import importlib.util
import sys
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
        # level in the test env (production sets basicConfig(level=INFO)).
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
