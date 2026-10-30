"""Per-command exit codes for an interactive shell, read from its prompt.

An interactive shell over a PTY is only a byte stream: the hub sees what
was typed and what came back, never where one command ended or what it
returned. So at the start of a session the hub prepends a marker to the
shell's prompt that carries `$?` — the same idea as the OSC 133 "command
finished" mark terminal emulators use for shell integration:

    ESC ] 133 ; D ; <exit code> BEL

Every time the prompt comes back, the marker reports the status of the
command before it. The marker is cut out of what the terminal shows.

    hub writes SETUP ──> shell: PS1 = <marker>$PS1
    shell prints ESC]133;D;1 BEL + prompt  ──>  ExitMarks.feed
                                               ├─ shown: the prompt only
                                               └─ codes: [1]

Shells (checked on real ones through a pty, 2026-10-30):
  * mksh — Android's /system/bin/sh. The marker is wrapped in mksh's
    non-printing delimiter (first PS1 character followed by CR) so line
    editing does not count it as printed width; a prompt that already uses
    a delimiter keeps its own. Android's mkshrc prompt (`${|…}` showing
    `1|` after a failure) is unaffected: `$?` is read, not changed.
  * bash — `\\[ … \\]` around the marker.
  * anything else (dash, toybox sh) — SETUP changes nothing, no marker
    ever comes back; after ARM_TIMEOUT_S the held output is shown as is
    and exit codes are simply unavailable for that session.

Not covered: a nested shell (`su`, `sh`) has its own prompt without the
marker, so commands typed inside it get no exit code until it is left.
"""

from __future__ import annotations

import re

# One line, leading space (bash leaves it out of history). Works as a no-op
# in shells that are neither mksh nor bash.
SETUP: bytes = (
    b" if [ -n \"$KSH_VERSION\" ]; then"
    b" __d=$(printf '\\001'); __r=$(printf '\\r');"
    b" case $PS1 in ?\"$__r\"*) __d=${PS1%\"${PS1#?}\"}; PS1=${PS1#??};; esac;"
    b" PS1=\"$__d$__r$__d$(printf '\\033]133;D;')\"'$?'\"$(printf '\\007')$__d$PS1\";"
    b" unset __d __r;"
    b" elif [ -n \"$BASH_VERSION\" ]; then"
    b" PS1='\\[\\e]133;D;$?\\a\\]'\"$PS1\";"
    b" fi\n"
)

_MARK = re.compile(rb"\x1b\]133;D;(\d{1,3})\x07")
# A chunk may end inside a marker; hold back at most this much of its tail.
_PREFIX = b"\x1b]133;D;"
_MAX_PARTIAL = len(_PREFIX) + 3

# How long output is held back waiting for the first marker (the echo of
# SETUP and the prompt before it are never shown), and how much of it.
ARM_TIMEOUT_S = 2.0
ARM_MAX_BYTES = 64 * 1024


class ExitMarks:
    """Feed it the shell's output; it returns what to show and the exit
    codes the prompt reported. The first marker answers SETUP itself and
    is not reported."""

    def __init__(self, started_at: float) -> None:
        self._deadline = started_at + ARM_TIMEOUT_S
        self._held = b""  # output before the first marker (not shown yet)
        self._partial = b""  # tail that may be the start of a marker
        self.armed = False  # the first marker has been seen
        self.unavailable = False  # gave up waiting: this shell has no marker

    @property
    def waiting(self) -> bool:
        return not self.armed and not self.unavailable

    def seconds_left(self, now: float) -> float:
        return max(0.0, self._deadline - now)

    def give_up(self) -> bytes:
        """No marker came: show what was held and stop holding."""
        self.unavailable = True
        out, self._held = self._held + self._partial, b""
        self._partial = b""
        return out

    def feed(self, chunk: bytes, now: float) -> tuple[bytes, list[int]]:
        data = self._partial + chunk
        self._partial = b""
        # Keep a trailing fragment that could still become a marker.
        cut = data.rfind(b"\x1b")
        if cut != -1 and len(data) - cut <= _MAX_PARTIAL and _could_be_mark(data[cut:]):
            data, self._partial = data[:cut], data[cut:]

        if self.waiting:
            self._held += data
            m = _MARK.search(self._held)
            if m is None:
                if now >= self._deadline or len(self._held) > ARM_MAX_BYTES:
                    return self.give_up(), []
                return b"", []
            # Everything up to the first marker is the echo of SETUP.
            self.armed = True
            data, self._held = self._held[m.end() :], b""

        codes = [int(c) for c in _MARK.findall(data)]
        return _MARK.sub(b"", data), codes


def _could_be_mark(tail: bytes) -> bool:
    """True when `tail` is a prefix of a marker that has not ended yet."""
    if len(tail) <= len(_PREFIX):
        return _PREFIX.startswith(tail)
    return tail.startswith(_PREFIX) and tail[len(_PREFIX) :].isdigit()
