"""board_config capability — find and read the board's config partition.

## What this answers that flashing cannot

`fastboot flash` returns `Writing 'x' OKAY` when fastboot believes it wrote.
That is not a readback, and nothing in the flash path performs one — so
"succeeded" and "the partition now holds what I sent" are different claims.
This capability closes that gap from the other side: once the board is back in
Android, read the block device and show what is actually there.

`fastboot getvar` cannot do this. It answers metadata (does the partition
exist, how big, is it logical) and never content.

## Finding the partition BY SHAPE, not by name

The obvious design is "let the operator name the partition". It is also the
one that breaks on the second board: the by-name label differs per product
(this bench's config partition is not called what fastboot calls it, and a
different board in the same family uses another label again). A hard-coded or
hand-typed name is the same defect that made the web partition picker offer
four names the bench refuses.

So detection is by content: read the head of every `/dev/block/by-name/*` and
keep the ones that parse as `KEY="VALUE"` lines. Measured on real hardware
(2026-08-10, ~60 partitions): one match with 11 parsed lines, zero false
positives, ~2 s. That judgement survives a relabelled partition and a new
board; a name does not.

## What it deliberately does NOT do

* **No writing.** Editing config from a browser has the destructive power of a
  flash with none of its protections (two-step arm, digest verification,
  single-job lock). If a value must change, it goes through the flash path.
* **No pretending.** When the bytes do not parse as `KEY="VALUE"`, the caller
  is told so and handed the raw head — an empty table would read as "the
  config is empty" when the truth is usually "you read the wrong partition".
  Same failure mode as reading `partition size: 0` as a diagnosis.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from alb.infra.result import Result, fail, ok
from alb.transport.base import Transport

# One `KEY="VALUE"` line. Anchored on both ends so a stray quote inside binary
# padding cannot masquerade as a key.
_KV_RE = re.compile(r'^([A-Za-z_][A-Za-z0-9_.-]*)="([^"\r\n]*)"\s*$')

# A by-name entry. It is interpolated into a shell command on the device, so
# the shape check is a security control, not tidiness: a name with a space, a
# quote or a slash would otherwise become extra argv or escape the directory.
_DEV_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")

# How much of a partition head to sniff during a scan. Small on purpose —
# this runs against every partition on the device, and the question ("does
# this look like config?") is answered by the first few lines or not at all.
SCAN_HEAD_BYTES = 512

# Minimum `KEY="VALUE"` lines for a partition to count as a candidate.
# Two was enough for zero false positives across ~60 real partitions; the real
# config partition matched 11. Raising it would start missing small configs,
# which is the worse error — a missed candidate looks like "no config on this
# board", and the operator has no way to tell that from a bad threshold.
SCAN_MIN_LINES = 2

# Default read for the detail view. The config text on the bench occupies well
# under 1 KiB of a 2 MiB partition; the rest is padding.
DEFAULT_READ_BYTES = 4096
MAX_READ_BYTES = 256 * 1024


@dataclass(frozen=True)
class ConfigCandidate:
    name: str
    node: str
    lines: int

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "node": self.node, "lines": self.lines}


@dataclass(frozen=True)
class ConfigRead:
    device: str
    node: str
    size_bytes: int
    read_bytes: int
    parsed: bool
    entries: list[tuple[str, str]] = field(default_factory=list)
    raw: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "device": self.device,
            "node": self.node,
            "size_bytes": self.size_bytes,
            "read_bytes": self.read_bytes,
            # False means "these bytes are not KEY=\"VALUE\"" — render `raw`,
            # never an empty table. See the module docstring.
            "parsed": self.parsed,
            "entries": [{"key": k, "value": v} for k, v in self.entries],
            "raw": self.raw,
        }


def parse_kv(text: str) -> list[tuple[str, str]]:
    """`KEY="VALUE"` lines, in file order, tolerating CRLF and padding.

    Non-matching lines are skipped rather than treated as an error: the tail
    of a config partition is padding, and one unreadable line should not
    discard the keys that did parse.
    """
    out: list[tuple[str, str]] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        m = _KV_RE.match(line.strip("\x00 \t"))
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def _scan_script(head: int, min_lines: int) -> str:
    """One on-device loop rather than N round trips.

    Each `dd` is milliseconds, but a shell round trip through the tunnel is
    not — doing this per partition turned a 2-second scan into a minute in an
    early draft. The device is the right place to iterate.
    """
    return (
        "for p in /dev/block/by-name/*; do "
        "n=${p##*/}; "
        f"c=$(dd if=\"$p\" bs={head} count=1 2>/dev/null | "
        "grep -acE '^[A-Za-z_][A-Za-z0-9_.-]*=\"[^\"]*\"' 2>/dev/null); "
        f'[ -n "$c" ] && [ "$c" -ge {min_lines} ] 2>/dev/null && '
        'echo "$n $c $(readlink -f "$p")"; '
        "done; echo __ALB_SCAN_DONE__"
    )


async def scan(transport: Transport, *, head: int = SCAN_HEAD_BYTES) -> Result:
    """Partitions whose head parses as `KEY="VALUE"`.

    Needs root: block devices are not readable by the shell user. That is a
    precondition, not a fallback — without it the loop silently finds nothing,
    which is indistinguishable from "this board has no config partition".
    """
    r = await transport.shell(_scan_script(head, SCAN_MIN_LINES), timeout=120)
    if not r.ok:
        return fail("BOARD_CONFIG_SCAN_FAILED", r.stderr or "scan command failed")
    if "__ALB_SCAN_DONE__" not in r.stdout:
        # The marker is how we tell "nothing matched" from "the loop never
        # finished" (killed, timed out, shell died mid-way). Without it an
        # aborted scan would report a confident empty list.
        return fail("BOARD_CONFIG_SCAN_FAILED", "scan did not run to completion")

    found: list[ConfigCandidate] = []
    for line in r.stdout.split("__ALB_SCAN_DONE__")[0].splitlines():
        parts = line.split()
        if len(parts) >= 2 and _DEV_RE.match(parts[0]):
            node = parts[2] if len(parts) >= 3 else f"/dev/block/by-name/{parts[0]}"
            try:
                found.append(ConfigCandidate(parts[0], node, int(parts[1])))
            except ValueError:
                continue
    return ok(
        {
            "candidates": [c.to_dict() for c in found],
            "hint": (
                ""
                if found
                else "nothing matched — is the shell root? block devices are unreadable otherwise"
            ),
        }
    )


async def read(
    transport: Transport, device: str, *, limit: int = DEFAULT_READ_BYTES
) -> Result:
    """Read `limit` bytes off `/dev/block/by-name/<device>` and parse them."""
    if not _DEV_RE.match(device):
        return fail("BOARD_CONFIG_BAD_DEVICE", f"device name {device!r} rejected")
    limit = max(1, min(int(limit), MAX_READ_BYTES))

    node = f"/dev/block/by-name/{device}"
    size = 0
    rs = await transport.shell(f'cat /sys/class/block/$(basename $(readlink -f {node}))/size', timeout=30)
    if rs.ok and rs.stdout.strip().isdigit():
        size = int(rs.stdout.strip()) * 512  # sysfs `size` is in 512-byte sectors

    rr = await transport.shell(f"dd if={node} bs={limit} count=1 2>/dev/null | od -c", timeout=60)
    if not rr.ok:
        return fail("BOARD_CONFIG_READ_FAILED", rr.stderr or "dd failed")

    text = _decode_od(rr.stdout)
    entries = parse_kv(text)
    return ok(
        ConfigRead(
            device=device,
            node=node,
            size_bytes=size,
            read_bytes=limit,
            parsed=bool(entries),
            entries=entries,
            # Always carried, parsed or not. When parsing fails this is the
            # only thing that lets the operator see WHY — usually "this is a
            # different partition than I thought".
            raw=text[:8192],
        ).to_dict()
    )


_OD_ESCAPES = {
    "\\0": "\x00", "\\a": "\a", "\\b": "\b", "\\f": "\f",
    "\\n": "\n", "\\r": "\r", "\\t": "\t", "\\v": "\v",
}


def _decode_od(out: str) -> str:
    """Rebuild the byte string from `od -c`.

    Why `od` and not raw bytes: the shell transport returns text, and a raw
    `dd` of a partition would carry NULs and arbitrary bytes through a pipe
    that decodes as UTF-8 — losing or mangling exactly the region we need to
    show. `od -c` is pure ASCII on the wire and lossless for our purpose.
    """
    chars: list[str] = []
    for line in out.splitlines():
        parts = line.split()
        if not parts or not all(c in "0123456789" for c in parts[0]):
            continue
        for tok in parts[1:]:
            if tok in _OD_ESCAPES:
                chars.append(_OD_ESCAPES[tok])
            elif len(tok) == 3 and tok.isdigit():
                chars.append(chr(int(tok, 8)))  # octal escape for a non-printable
            elif len(tok) == 1:
                chars.append(tok)
    return "".join(chars)
