"""Tests for TerminalGuard — pattern matching, line buffering, HITL flow,
audit hook. Uses a fake shell that captures whatever the guard forwards."""

from __future__ import annotations

import sys
from typing import Any

import pytest

from alb.transport.terminal_guard import (
    DEFAULT_DANGER_RULES,
    DangerRule,
    TerminalGuard,
    _check_line,
)


pytestmark = pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="Terminal guard targets Unix PTYs",
)


class _FakeShell:
    """Captures bytes the guard forwards. read/resize/close are no-ops."""

    def __init__(self) -> None:
        self.written: bytearray = bytearray()
        self.closed = False

    async def write(self, data: bytes) -> None:
        self.written.extend(data)

    async def resize(self, rows: int, cols: int) -> None:
        pass

    async def close(self) -> None:
        self.closed = True

    @property
    def returncode(self) -> int | None:
        return None


# ─── Synchronous helper: line classification ───────────────────────


def test_check_line_safe_command_passes() -> None:
    v = _check_line("ls -la /sdcard", rules=DEFAULT_DANGER_RULES,
                    read_only=False, session_allowed=set())
    assert v.allow is True


def test_check_line_blocks_rm_rf_system() -> None:
    v = _check_line("rm -rf /system/priv-app",
                    rules=DEFAULT_DANGER_RULES,
                    read_only=False, session_allowed=set())
    assert v.allow is False
    assert v.rule and "rm" in v.rule.name


def test_check_line_blocks_dd() -> None:
    v = _check_line("dd if=/dev/zero of=/dev/block/sda1 bs=1M",
                    rules=DEFAULT_DANGER_RULES,
                    read_only=False, session_allowed=set())
    assert v.allow is False
    assert v.rule.name == "dd"


def test_check_line_blocks_reboot() -> None:
    v = _check_line("reboot recovery", rules=DEFAULT_DANGER_RULES,
                    read_only=False, session_allowed=set())
    assert v.allow is False
    assert v.rule.name == "reboot"


def test_check_line_session_allow_overrides_rule() -> None:
    v = _check_line("rm -rf /system/x",
                    rules=DEFAULT_DANGER_RULES,
                    read_only=False,
                    session_allowed={"rm -rf /system/x"})
    assert v.allow is True


def test_check_line_read_only_allows_listed() -> None:
    v = _check_line("ls /sdcard", rules=(), read_only=True, session_allowed=set())
    assert v.allow is True


def test_check_line_read_only_blocks_unknown() -> None:
    v = _check_line("vim /etc/hosts", rules=(), read_only=True, session_allowed=set())
    assert v.allow is False
    assert v.rule.name == "read-only-mode"


def test_check_line_read_only_blocks_dangerous_too() -> None:
    # Read-only mode blocks even if the deny-list also matched.
    v = _check_line("rm -rf /system", rules=DEFAULT_DANGER_RULES,
                    read_only=True, session_allowed=set())
    assert v.allow is False


# ─── Guard line buffering + HITL flow ─────────────────────────────


@pytest.mark.asyncio
async def test_guard_passes_safe_command_through() -> None:
    shell = _FakeShell()
    audits: list[dict] = []

    async def on_audit(ev: dict) -> None:
        audits.append(ev)

    g = TerminalGuard(shell, on_audit=on_audit)  # type: ignore[arg-type]
    await g.feed(b"ls -la\n")
    assert shell.written.endswith(b"ls -la\n")
    assert any(a["event"] == "command" for a in audits)


@pytest.mark.asyncio
async def test_guard_blocks_dangerous_and_calls_hitl() -> None:
    shell = _FakeShell()
    hitl_calls: list[tuple[str, str]] = []
    audits: list[dict] = []

    async def on_hitl(line: str, rule: DangerRule) -> None:
        hitl_calls.append((line, rule.name))

    async def on_audit(ev: dict) -> None:
        audits.append(ev)

    g = TerminalGuard(  # type: ignore[arg-type]
        shell, on_hitl=on_hitl, on_audit=on_audit,
    )
    await g.feed(b"rm -rf /system/x\n")

    # Shell must not have received the command.
    assert b"rm -rf" not in bytes(shell.written)
    # HITL was invoked.
    assert hitl_calls and "rm" in hitl_calls[0][1]
    # Guard now waiting for response.
    assert g.has_pending
    assert g.pending_line and "rm -rf" in g.pending_line
    # hitl_request audit event recorded.
    assert any(a["event"] == "hitl_request" for a in audits)


@pytest.mark.asyncio
async def test_guard_approve_forwards_held_command() -> None:
    shell = _FakeShell()
    g = TerminalGuard(shell)  # type: ignore[arg-type]
    await g.feed(b"reboot\n")
    assert g.has_pending

    await g.respond_hitl(approve=True)
    assert b"reboot\n" in bytes(shell.written)
    assert not g.has_pending


@pytest.mark.asyncio
async def test_guard_deny_drops_held_command() -> None:
    shell = _FakeShell()
    echos: list[bytes] = []

    async def on_echo(b: bytes) -> None:
        echos.append(b)

    g = TerminalGuard(shell, on_echo=on_echo)  # type: ignore[arg-type]
    await g.feed(b"reboot\n")
    await g.respond_hitl(approve=False)
    # The command bytes never reached the shell stdin.
    assert b"reboot" not in bytes(shell.written)
    # User saw the [denied] notice.
    joined = b"".join(echos)
    assert b"denied" in joined


@pytest.mark.asyncio
async def test_guard_allow_session_persists_for_same_command() -> None:
    shell = _FakeShell()
    g = TerminalGuard(shell)  # type: ignore[arg-type]

    # First time the command is dangerous → HITL → approve session
    await g.feed(b"reboot\n")
    await g.respond_hitl(approve=True, allow_session=True)

    # Second time same command → goes straight through (no pending hitl)
    await g.feed(b"reboot\n")
    assert not g.has_pending
    # Both invocations reached the shell.
    assert bytes(shell.written).count(b"reboot\n") == 2


@pytest.mark.asyncio
async def test_guard_allow_session_refuses_metachar_commands() -> None:
    """Security audit 2026-05-02 finding HIGH 1: approving `eval $X`
    for the session would let the user later mutate $X to bypass the
    deny list. respond_hitl must REFUSE to add metachar lines to
    `_session_allowed` (still approve once, just not for session)."""
    from alb.transport.terminal_guard import (
        DangerRule,
        TerminalGuard,
        _has_shell_metachars,
    )
    import re

    # Custom rule that catches `eval` since default DANGEROUS_PATTERNS
    # doesn't have one, but the metachar refusal is rule-independent.
    custom = (
        DangerRule(
            name="eval-block",
            pattern=re.compile(r"^\s*eval\b"),
            reason="eval expansion bypasses pattern matching",
        ),
    )
    shell = _FakeShell()
    g = TerminalGuard(shell, rules=custom)  # type: ignore[arg-type]

    await g.feed(b"eval $X\n")
    assert g.has_pending
    await g.respond_hitl(approve=True, allow_session=True)

    # Even though caller asked for allow_session, the metachar guard
    # refuses to promote — second instance must re-trigger HITL.
    await g.feed(b"eval $X\n")
    assert g.has_pending
    # Sanity: the metachar detector itself.
    assert _has_shell_metachars("eval $X")
    assert _has_shell_metachars("rm -rf `pwd`")
    assert _has_shell_metachars("foo; bar")
    assert _has_shell_metachars("foo | bar")
    assert _has_shell_metachars("rm -rf /(...)")
    assert not _has_shell_metachars("rm -rf /data/foo")
    assert not _has_shell_metachars("setprop debug.x 1")


@pytest.mark.asyncio
async def test_guard_backspace_edits_buffer() -> None:
    shell = _FakeShell()
    echos: list[bytes] = []

    async def on_echo(b: bytes) -> None:
        echos.append(b)

    g = TerminalGuard(shell, on_echo=on_echo)  # type: ignore[arg-type]
    await g.feed(b"lls")          # typo
    await g.feed(b"\x7f")         # backspace removes "s"
    await g.feed(b"\x7f")         # remove second "l"
    await g.feed(b"s -la\n")      # finishes "ls -la"
    assert bytes(shell.written).endswith(b"ls -la\n")
    # Echo includes the backspace-erase sequence.
    joined = b"".join(echos)
    assert b"\b \b" in joined


@pytest.mark.asyncio
async def test_guard_pending_input_drops_with_bell() -> None:
    shell = _FakeShell()
    echos: list[bytes] = []

    async def on_echo(b: bytes) -> None:
        echos.append(b)

    g = TerminalGuard(shell, on_echo=on_echo)  # type: ignore[arg-type]
    await g.feed(b"reboot\n")
    assert g.has_pending
    # Anything typed while waiting is dropped + a bell echoed.
    await g.feed(b"y\n")
    joined = b"".join(echos)
    assert b"\a" in joined


@pytest.mark.asyncio
async def test_guard_escape_sequences_passthrough() -> None:
    shell = _FakeShell()
    g = TerminalGuard(shell)  # type: ignore[arg-type]
    # ESC + [A is "arrow up" — should bypass HITL line buffer
    await g.feed(b"\x1b[A")
    await g.feed(b"\n")
    assert b"\x1b" in bytes(shell.written)


@pytest.mark.asyncio
async def test_guard_read_only_blocks_unsafe() -> None:
    shell = _FakeShell()
    hitl: list[Any] = []

    async def on_hitl(line: str, rule: DangerRule) -> None:
        hitl.append((line, rule.name))

    g = TerminalGuard(shell, read_only=True, on_hitl=on_hitl)  # type: ignore[arg-type]
    await g.feed(b"vim /etc/hosts\n")
    assert g.has_pending
    assert hitl[0][1] == "read-only-mode"
    assert b"vim" not in bytes(shell.written)


@pytest.mark.asyncio
async def test_guard_read_only_lets_safe_commands_through() -> None:
    shell = _FakeShell()
    g = TerminalGuard(shell, read_only=True)  # type: ignore[arg-type]
    await g.feed(b"ls /sdcard\n")
    assert not g.has_pending
    assert bytes(shell.written).endswith(b"ls /sdcard\n")
