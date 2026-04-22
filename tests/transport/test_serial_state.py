"""Tests for the SerialState classifier + state machine.

These tests are deliberately thorough because the state machine decides
what the whole capability layer does on UART. A wrong classification
leads to cryptic "board is still booting" errors on a healthy board, or
the opposite: trying to run shell commands into a kernel panic.
"""

from __future__ import annotations

import re

import pytest

from alb.transport.serial_state import (
    DEFAULT_PATTERNS,
    PatternSet,
    SerialState,
    SerialStateMachine,
    StateTransition,
    classify,
)


# ─── classify() — priority ordering ───────────────────────────────


def test_empty_buffer_is_idle() -> None:
    assert classify(b"") == SerialState.IDLE


def test_plain_user_shell_prompt() -> None:
    assert classify(b"user@host:/ $ ") == SerialState.SHELL_USER


def test_plain_root_shell_prompt() -> None:
    assert classify(b"root@host:/ # ") == SerialState.SHELL_ROOT


def test_uboot_prompt() -> None:
    assert classify(b"U-Boot 2024.07\nHit any key\n=> ") == SerialState.UBOOT
    assert classify(b"something\nU-Boot > ") == SerialState.UBOOT
    assert classify(b"boot> ") == SerialState.UBOOT


def test_kernel_boot_markers() -> None:
    assert classify(b"[    0.000000] Booting Linux on physical CPU 0x0\n") \
        == SerialState.KERNEL_BOOT
    assert classify(b"Starting kernel ...\n") == SerialState.KERNEL_BOOT


def test_linux_init_markers() -> None:
    assert classify(b"systemd[1]: Starting ...\n") == SerialState.LINUX_INIT
    assert classify(b"init: Service 'zygote' is being killed\n") == SerialState.LINUX_INIT


def test_spl_markers() -> None:
    assert classify(b"U-Boot SPL 2024.07\n") == SerialState.SPL
    assert classify(b"BL31: Built : 10:12:34\n") == SerialState.SPL


def test_login_prompt() -> None:
    assert classify(b"Debian GNU/Linux 12 host ttyS0\nlogin: ") == SerialState.LOGIN_PROMPT
    assert classify(b"Username: ") == SerialState.LOGIN_PROMPT


def test_recovery_beats_shell_root() -> None:
    """Recovery prompt looks like root shell but should win — it's more
    specific and leads to different capabilities.

    Real Android recovery prompts seen in the wild:
      - ``recovery:/ # `` (with a path)
      - ``~ # `` (plain home-dir marker)
    A normal ``root@host:/ # `` must NOT collapse into RECOVERY.
    """
    assert classify(b"recovery:/sdcard # ") == SerialState.RECOVERY
    assert classify(b"\n~ # ") == SerialState.RECOVERY


def test_fastboot_prompt() -> None:
    assert classify(b"fastboot> ") == SerialState.FASTBOOT


def test_panic_wins_over_crash() -> None:
    """Panic output typically contains BUG/WARNING lines; the classifier
    must label the whole thing PANIC, not CRASH.
    """
    buf = (
        b"[   42.112345] BUG: unable to handle page fault\n"
        b"[   42.113456] Kernel panic - not syncing: Fatal exception\n"
        b"[   42.114567] CPU: 0 PID: 1 Comm: init\n"
    )
    assert classify(buf) == SerialState.PANIC


def test_soft_crash_without_panic_is_crash() -> None:
    buf = b"BUG: using smp_processor_id() in preemptible\nCall Trace:\n  dump_stack+0x5c/0x88\n"
    assert classify(buf) == SerialState.CRASH


def test_unknown_when_no_pattern_matches() -> None:
    assert classify(b"hello world\nrandom text\n") == SerialState.UNKNOWN


def test_shell_prompt_must_anchor_to_tail() -> None:
    """A ``#`` somewhere in the middle must not trigger SHELL_ROOT."""
    buf = b"echo 'this # is not a prompt'\nsome more output\n"
    assert classify(buf) != SerialState.SHELL_ROOT
    assert classify(buf) != SerialState.SHELL_USER


def test_kernel_boot_loses_to_shell_prompt_at_tail() -> None:
    """Late-arriving printk shouldn't demote a reachable shell."""
    buf = (
        b"[   12.345678] wlan0: link up\n"
        b"root@localhost:/ # "
    )
    assert classify(buf) == SerialState.SHELL_ROOT


def test_u_boot_banner_alone_does_not_match_uboot_state() -> None:
    """Banner text without the prompt => shouldn't be classified as UBOOT."""
    buf = b"U-Boot 2024.07 (Jan 01 2025 - 00:00:00 +0000)\nBoard: Evaluation board\n"
    # No `=>`, no tail prompt — just boot banner; should NOT be UBOOT
    assert classify(buf) != SerialState.UBOOT


# ─── Corruption detection (wrong baud) ─────────────────────────────


def test_mostly_non_printable_is_corrupted() -> None:
    buf = bytes(range(128, 200)) * 10  # high bytes everywhere
    assert classify(buf) == SerialState.CORRUPTED


def test_mixed_but_mostly_ascii_is_not_corrupted() -> None:
    buf = b"some printable text with [    1.123456] a few timestamps\n" * 5
    assert classify(buf) != SerialState.CORRUPTED


def test_very_short_buffer_never_corrupted() -> None:
    """16-byte buffers aren't judged — too little signal to blame baud."""
    assert classify(b"\xff\xfe\xfd\xfc") != SerialState.CORRUPTED


def test_ansi_escapes_are_not_corruption() -> None:
    """ANSI color codes contain ESC (\\x1b) but are expected on modern shells."""
    buf = b"\x1b[32mgreen text\x1b[0m\nroot@h:/ # "
    assert classify(buf) == SerialState.SHELL_ROOT


# ─── Window / tail size ────────────────────────────────────────────


def test_classifier_only_looks_at_trailing_window() -> None:
    """Something matching far back in the buffer doesn't classify the tail."""
    # First: big chunk of noise that happens to contain "fastboot>"
    head = b"fastboot> \n" + b"x" * 10000
    # Then a clean shell prompt at the tail
    tail = b"root@host:/ # "
    buf = head + tail
    # Using window=256 (smaller than noise), only tail is inspected
    assert classify(buf, window=256) == SerialState.SHELL_ROOT


# ─── PatternSet override ──────────────────────────────────────────


def test_pattern_override_from_mapping() -> None:
    """User can supply a board-specific prompt via TOML config."""
    custom = PatternSet.from_mapping(
        {"shell_root": r"myboard:/\s*#\s*$"}
    )
    buf = b"myboard:/ # "
    assert classify(buf, patterns=custom) == SerialState.SHELL_ROOT
    # Default shell_root prompt should still classify too (we didn't
    # remove it — regex is user's full replacement for that state)
    # Actually no: since we REPLACED shell_root, the generic `root@x:/ #`
    # no longer matches. That's the expected trade-off and is documented.
    buf2 = b"root@host:/ # "
    assert classify(buf2, patterns=custom) != SerialState.SHELL_ROOT


def test_unknown_pattern_key_raises() -> None:
    with pytest.raises(ValueError, match="unknown serial state pattern key"):
        PatternSet.from_mapping({"nonexistent_state": r".*"})


def test_invalid_regex_raises() -> None:
    with pytest.raises(ValueError, match="invalid serial state regex"):
        PatternSet.from_mapping({"uboot": r"(unclosed"})


def test_default_pattern_dict_is_complete() -> None:
    """Every state that participates in classify()'s priority ladder
    must have a default pattern — otherwise PatternSet.default() would
    crash.
    """
    required_keys = {
        "uboot", "spl", "kernel_boot", "linux_init", "login_prompt",
        "shell_user", "shell_root", "recovery", "fastboot", "panic", "crash",
    }
    assert set(DEFAULT_PATTERNS.keys()) == required_keys


# ─── State machine — feeding + transitions ────────────────────────


def test_feed_empty_no_change() -> None:
    sm = SerialStateMachine()
    assert sm.feed(b"") == SerialState.UNKNOWN
    assert sm.state == SerialState.UNKNOWN
    assert sm.history == []


def test_feed_records_single_transition() -> None:
    sm = SerialStateMachine()
    sm.feed(b"some boot garbage\nroot@host:/ # ")
    assert sm.state == SerialState.SHELL_ROOT
    assert len(sm.history) == 1
    t = sm.history[0]
    assert t.from_state == SerialState.UNKNOWN
    assert t.to_state == SerialState.SHELL_ROOT


def test_feed_multiple_transitions_recorded_in_order() -> None:
    sm = SerialStateMachine()
    sm.feed(b"U-Boot SPL 2024.07\n")
    sm.feed(b"\nsome boot output\nU-Boot 2024.07\n=> ")
    sm.feed(b"\n[    0.000000] Booting Linux on CPU 0\n")
    sm.feed(b"\n[    5.234567] init: boot done\nroot@host:/ # ")
    states = [t.to_state for t in sm.history]
    assert SerialState.SPL in states
    assert SerialState.UBOOT in states
    assert SerialState.KERNEL_BOOT in states
    assert SerialState.SHELL_ROOT in states
    # Final
    assert sm.state == SerialState.SHELL_ROOT


def test_feed_same_state_no_new_transition() -> None:
    sm = SerialStateMachine()
    sm.feed(b"root@host:/ # ")
    sm.feed(b"")  # no-op
    sm.feed(b"\n[   12.123456] wlan up\nroot@host:/ # ")  # still shell_root
    assert len(sm.history) == 1   # single transition recorded


def test_listener_fires_on_transition() -> None:
    sm = SerialStateMachine()
    fired: list[StateTransition] = []
    sm.on_transition(fired.append)

    sm.feed(b"root@host:/ # ")
    assert len(fired) == 1
    assert fired[0].to_state == SerialState.SHELL_ROOT

    # No transition = no listener call
    sm.feed(b"\nroot@host:/ # ")
    assert len(fired) == 1

    # Another transition
    sm.feed(b"\nreboot\n[    0.000000] Booting Linux\n")
    assert len(fired) == 2
    assert fired[1].to_state == SerialState.KERNEL_BOOT


def test_reset_clears_buffer_and_history_but_keeps_listeners() -> None:
    sm = SerialStateMachine()
    fired: list[StateTransition] = []
    sm.on_transition(fired.append)
    sm.feed(b"root@host:/ # ")
    sm.reset()
    assert sm.state == SerialState.UNKNOWN
    assert sm.history == []
    # Listener still there: a fresh transition fires it
    sm.feed(b"root@host:/ # ")
    assert len(fired) == 2


def test_rolling_buffer_trims() -> None:
    sm = SerialStateMachine(buffer_size=100)
    sm.feed(b"x" * 500)
    assert len(sm.buffer_tail) == 100


def test_snapshot_shape() -> None:
    sm = SerialStateMachine()
    sm.feed(b"[    0.000000] Booting Linux\n")
    sm.feed(b"root@host:/ # ")
    snap = sm.snapshot()
    assert snap["state"] == SerialState.SHELL_ROOT.value
    assert snap["buffer_bytes"] > 0
    assert isinstance(snap["tail"], str)
    assert "#" in snap["tail"]
    assert len(snap["history"]) == 2
    assert snap["history"][-1]["to"] == SerialState.SHELL_ROOT.value


# ─── StateTransition dataclass ────────────────────────────────────


def test_state_transition_as_dict() -> None:
    t = StateTransition(
        from_state=SerialState.UNKNOWN,
        to_state=SerialState.SHELL_ROOT,
        at=1234.5,
        wall_time="2026-04-22T00:00:00+00:00",
    )
    d = t.as_dict()
    assert d == {
        "from": "unknown",
        "to": "shell_root",
        "at": 1234.5,
        "wall_time": "2026-04-22T00:00:00+00:00",
    }
