"""TerminalGuard — line-buffered HITL gatekeeper for an InteractiveShell.

Sits between the WebSocket client and a PTY shell. Intercepts user
keystrokes, builds the current input line server-side, and runs a
deny-list match before letting the line reach the shell.

Decisions land via async callbacks the WS layer wires up — HITL prompt
to the client, approval response back from the client, audit JSONL
append. The guard knows nothing about WebSockets; it just speaks bytes.

Threading model: single-task ownership. The `feed()` coroutine is
called sequentially from one coroutine (the WS recv loop). No locks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Iterable, Pattern

from alb.transport.interactive import InteractiveShell


# ─── Deny-list ───────────────────────────────────────────────────


@dataclass(frozen=True)
class DangerRule:
    name: str
    pattern: Pattern[str]
    reason: str


def _r(name: str, regex: str, reason: str) -> DangerRule:
    return DangerRule(name=name, pattern=re.compile(regex), reason=reason)


# Conservative defaults. Add per-deployment via TerminalGuard(extra_rules=...).
DEFAULT_DANGER_RULES: tuple[DangerRule, ...] = (
    _r("rm-rf-root",
       r"^\s*rm\s+(-[^/\s]*r[^/\s]*\s+)*/?(system|vendor|boot|data|root|sdcard|product|odm|dev|sys|proc)(/|$)",
       "rm targeting a system or critical-data path"),
    _r("rm-rf-bare-slash",
       r"^\s*rm\s+(-[^/\s]*r[^/\s]*\s+)+/(\s|$)",
       "rm -r at the filesystem root"),
    _r("dd",
       r"^\s*dd\s+",
       "dd writes raw bytes to a device — extremely destructive"),
    _r("mkfs",
       r"^\s*mkfs[\.\w]*\s+",
       "mkfs reformats a partition"),
    _r("reboot",
       r"^\s*reboot(\s|$)",
       "reboot ends the session and may interrupt a long-running task"),
    _r("setprop-persist",
       r"^\s*setprop\s+persist\.\S+",
       "persistent setprop survives reboot — needs explicit confirm"),
    _r("setenforce",
       r"^\s*setenforce\s+",
       "changing SELinux mode is rarely intended"),
    _r("mount-rw",
       r"^\s*mount\s+(-o\s+\S*rw|--remount,?rw)",
       "remounting a partition rw can let following commands brick the device"),
    _r("dropbear-ssh-keygen",
       r"^\s*ssh-keygen\s+-f\s+/(?:system|vendor)",
       "writing keys into system partitions"),
    _r("partition-tools",
       r"^\s*(parted|fdisk|sgdisk|sfdisk)\s+",
       "partition table editor"),
    _r("fastboot-flash",
       r"^\s*fastboot\s+(flash|erase)\s+",
       "fastboot flash / erase requires a deliberate intent"),
    _r("avbctl-disable",
       r"^\s*avbctl\s+(disable-verification|disable-verity)",
       "disabling Android Verified Boot"),
)


# Read-only allowlist — small set of obviously-safe commands. Anything
# else gets HITL'd in read-only mode.
READ_ONLY_ALLOWLIST: tuple[Pattern[str], ...] = tuple(
    re.compile(p) for p in (
        r"^\s*$",                                 # empty line
        r"^\s*(ls|cat|head|tail|file|stat|wc)\b",
        r"^\s*(grep|awk|sed|sort|uniq|cut|tr)\b",  # text munging on existing files
        r"^\s*(ps|top|free|df|du|uptime)\b",
        r"^\s*(uname|whoami|id|env|date)\b",
        r"^\s*(getprop|dumpsys|service|pm|cmd)\s",
        r"^\s*(ip|netstat|ss|ifconfig|ping)\b",
        r"^\s*(logcat|dmesg)\b",
        r"^\s*(echo|printf|true|false|exit|clear)\b",
        r"^\s*(history|alias|which|type|help)\b",
        r"^\s*(cd|pwd)\b",
    )
)


# ─── Verdicts and side-channel events ────────────────────────────


@dataclass(frozen=True)
class GuardVerdict:
    allow: bool
    rule: DangerRule | None = None    # which rule fired (if any)
    notice: str = ""                  # text to surface in the terminal


def _check_line(
    line: str,
    *,
    rules: Iterable[DangerRule],
    read_only: bool,
    session_allowed: set[str],
) -> GuardVerdict:
    stripped = line.strip()
    if stripped in session_allowed:
        return GuardVerdict(allow=True)

    if read_only:
        for pat in READ_ONLY_ALLOWLIST:
            if pat.search(line):
                return GuardVerdict(allow=True)
        return GuardVerdict(
            allow=False,
            rule=DangerRule(
                name="read-only-mode",
                pattern=re.compile(""),
                reason="read-only terminal blocks any non-listed command",
            ),
            notice="(read-only mode is on)",
        )

    for rule in rules:
        if rule.pattern.search(line):
            return GuardVerdict(allow=False, rule=rule)
    return GuardVerdict(allow=True)


# ─── Line buffer ─────────────────────────────────────────────────


# Bytes the guard recognizes as line-completion / editing.
_NL = ord("\n")
_CR = ord("\r")
_BS = 0x08      # ^H
_DEL = 0x7F     # backspace key on most terminals
_ESC = 0x1B     # start of an escape sequence


@dataclass
class _LineBuffer:
    """Server-side echo of what the user has typed since the last Enter.

    Tracks an in-progress line so we can examine it before letting it
    reach the shell. Falls back to passthrough on the first escape
    sequence so interactive apps (vim/less) keep working — at the cost
    of skipping HITL for the current line.
    """

    chars: bytearray = field(default_factory=bytearray)
    in_escape: bool = False  # set when we see ESC; passthrough until next Enter

    def reset(self) -> None:
        self.chars.clear()
        self.in_escape = False

    def text(self) -> str:
        return self.chars.decode("utf-8", errors="replace")


# ─── The guard ───────────────────────────────────────────────────


class TerminalGuard:
    def __init__(
        self,
        shell: InteractiveShell,
        *,
        rules: Iterable[DangerRule] | None = None,
        read_only: bool = False,
        on_hitl: Callable[[str, DangerRule], Awaitable[None]] | None = None,
        on_audit: Callable[[dict], Awaitable[None]] | None = None,
        on_echo: Callable[[bytes], Awaitable[None]] | None = None,
    ) -> None:
        self.shell = shell
        self.rules: tuple[DangerRule, ...] = tuple(rules or DEFAULT_DANGER_RULES)
        self.read_only = read_only
        self._on_hitl = on_hitl
        self._on_audit = on_audit
        self._on_echo = on_echo

        self._buf = _LineBuffer()
        self._session_allowed: set[str] = set()
        self._pending_line: str | None = None  # waiting for HITL response
        self._closed = False

    # ── External API ─────────────────────────────────────────────

    async def feed(self, data: bytes) -> None:
        """Process a chunk of user input.

        Splits on newline; passes each newline-terminated line through
        the deny-list. Editing keys (backspace) update the in-progress
        buffer. Escape sequences flip the buffer into passthrough mode
        for the rest of the line so vim/less aren't broken.
        """
        if self._closed or not data:
            return
        if self._pending_line is not None:
            # While waiting for HITL response, drop further input on
            # the floor. Echo a bell so the user knows.
            await self._echo(b"\a")
            return

        for byte in data:
            await self._handle_byte(byte)

    async def _handle_byte(self, byte: int) -> None:
        if byte == _CR or byte == _NL:
            await self._handle_enter()
            return
        if byte == _ESC:
            self._buf.in_escape = True
            await self.shell.write(bytes([byte]))
            return
        if self._buf.in_escape:
            # Forward escape-sequence bytes verbatim — don't try to
            # parse them. They terminate at the next non-CSI letter.
            await self.shell.write(bytes([byte]))
            return
        if byte in (_BS, _DEL):
            if self._buf.chars:
                self._buf.chars.pop()
                await self._echo(b"\b \b")
            return
        # Printable: buffer + echo
        self._buf.chars.append(byte)
        await self._echo(bytes([byte]))

    async def _handle_enter(self) -> None:
        line = self._buf.text()
        # If we were in passthrough (escape seq) just forward the CR
        # without HITL — that command is a control sequence anyway.
        if self._buf.in_escape:
            await self.shell.write(b"\r")
            self._buf.reset()
            return

        verdict = _check_line(
            line,
            rules=self.rules,
            read_only=self.read_only,
            session_allowed=self._session_allowed,
        )
        if verdict.allow:
            await self._audit("command", {"line": line})
            # Forward the fully-buffered line + Enter to the shell.
            payload = self._buf.chars + b"\n"
            self._buf.reset()
            await self.shell.write(payload)
            return

        # Hold the line + ask the client for a decision.
        self._pending_line = line
        await self._echo(b"\r\n")  # visually park the cursor
        await self._audit(
            "hitl_request",
            {"line": line, "rule": verdict.rule.name if verdict.rule else "?",
             "reason": verdict.rule.reason if verdict.rule else verdict.notice},
        )
        if self._on_hitl and verdict.rule:
            await self._on_hitl(line, verdict.rule)

    # ── HITL response handling ──────────────────────────────────

    async def respond_hitl(self, *, approve: bool, allow_session: bool = False) -> None:
        """Called by the WS layer when the client sends a HITL response."""
        line = self._pending_line
        self._pending_line = None
        self._buf.reset()
        if line is None:
            return
        if approve:
            if allow_session:
                self._session_allowed.add(line.strip())
            await self._audit("hitl_approve", {"line": line, "session": allow_session})
            await self.shell.write(line.encode("utf-8", errors="replace") + b"\n")
        else:
            await self._audit("hitl_deny", {"line": line})
            await self._echo(b"[denied]\r\n")
            # Re-prompt by sending an empty Enter so the shell shows the
            # next prompt; harmless if shell is in a sub-mode.
            await self.shell.write(b"\n")

    # ── Lifecycle ───────────────────────────────────────────────

    def close(self) -> None:
        self._closed = True

    @property
    def has_pending(self) -> bool:
        return self._pending_line is not None

    @property
    def pending_line(self) -> str | None:
        return self._pending_line

    # ── Internal sinks ──────────────────────────────────────────

    async def _echo(self, data: bytes) -> None:
        if self._on_echo:
            await self._on_echo(data)

    async def _audit(self, kind: str, payload: dict) -> None:
        if self._on_audit:
            await self._on_audit({"event": kind, **payload})
