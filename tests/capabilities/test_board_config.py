"""board_config — find the config partition by SHAPE, read it, parse it.

The capability exists because `Writing 'x' OKAY` is fastboot saying it
believes it wrote, not a readback. What is pinned here is mostly about
refusing to overclaim:

* detection must be by content, so a relabelled partition on the next board
  still works — a name would not;
* bytes that do not parse must be reported as "not KEY=\"VALUE\"" with the raw
  head, never as an empty table. "no keys" and "you read the wrong partition"
  look identical in an empty table, and the second is far more likely.
"""

from __future__ import annotations

import pytest

from alb.capabilities import board_config as bc
from alb.transport.base import ShellResult

# What `od -c` prints for the head of this bench's config partition.
_OD_REAL = """0000000   M   A   I   N   _   B   O   A   R   D   =   "   V   0   3   "
0000020  \\r  \\n   P   O   R   T   _   B   O   A   R   D   =   "   V   0
0000040   3   "  \\r  \\n   B   A   S   E   B   A   N   D   =   "   8   1
0000060   "  \\r  \\n   W   I   F   I   =   "   8   1   "  \\r  \\n  \\0  \\0
"""


class _T:
    """Transport double. Answers by substring so the tests stay about
    behaviour rather than about exact command strings."""

    name = "adb"

    def __init__(self, replies: dict[str, ShellResult], default: ShellResult | None = None):
        self.replies = replies
        self.default = default or ShellResult(ok=True, exit_code=0, stdout="", stderr="")
        self.seen: list[str] = []

    async def shell(self, cmd: str, *, timeout: int = 30) -> ShellResult:  # noqa: ASYNC109
        self.seen.append(cmd)
        for key, r in self.replies.items():
            if key in cmd:
                return r
        return self.default


def _ok(out: str) -> ShellResult:
    return ShellResult(ok=True, exit_code=0, stdout=out, stderr="")


# ── parsing ────────────────────────────────────────────────────────


def test_parse_handles_crlf_and_trailing_padding():
    text = 'A="1"\r\nB="2"\r\n\x00\x00\x00'
    assert bc.parse_kv(text) == [("A", "1"), ("B", "2")]


def test_parse_skips_junk_lines_without_discarding_good_ones():
    """The tail of a config partition is padding. One unreadable line must
    not throw away the keys that did parse."""
    assert bc.parse_kv('A="1"\n\x01\x02garbage\nB="2"') == [("A", "1"), ("B", "2")]


@pytest.mark.parametrize("line", ['A=1', 'A="unterminated', '="v"', '  # A="1"', 'A = "1"'])
def test_parse_rejects_near_misses(line):
    assert bc.parse_kv(line) == []


def test_od_decode_round_trip():
    text = bc._decode_od(_OD_REAL)
    assert 'MAIN_BOARD="V03"' in text
    assert "\r\n" in text, "CRLF must survive — it is how the lines are separated"
    assert "\x00" in text, "padding must survive so a caller can see where content ends"


# ── scan: by content, and honest when it finds nothing ─────────────


async def test_scan_finds_by_content_not_by_name():
    t = _T({"by-name": _ok("cfgpart 11 /dev/block/mmcblk0p56\n__ALB_SCAN_DONE__\n")})
    r = await bc.scan(t)
    assert r.ok
    c = r.data["candidates"]
    assert c == [{"name": "cfgpart", "node": "/dev/block/mmcblk0p56", "lines": 11}]
    # The probe must look at content. A scan that only listed names could not
    # tell a config partition from any other.
    assert 'grep -acE' in t.seen[0]


async def test_scan_without_the_completion_marker_is_a_failure_not_an_empty_list():
    """A killed or timed-out loop produces partial output. Reporting that as
    "no config partition on this board" is a confident wrong answer."""
    t = _T({"by-name": _ok("cfgpart 11 /dev/x\n")})  # no marker
    r = await bc.scan(t)
    assert not r.ok
    assert r.error is not None and r.error.code == "BOARD_CONFIG_SCAN_FAILED"


async def test_scan_empty_result_names_the_usual_cause():
    """Block devices are unreadable without root, and the loop then finds
    nothing — indistinguishable from a board that has no config partition
    unless we say so."""
    t = _T({"by-name": _ok("__ALB_SCAN_DONE__\n")})
    r = await bc.scan(t)
    assert r.ok
    assert r.data["candidates"] == []
    assert "root" in r.data["hint"]


async def test_scan_ignores_malformed_lines():
    t = _T({"by-name": _ok("bad line\n../evil 9 /dev/x\ncfg 3 /dev/y\n__ALB_SCAN_DONE__\n")})
    r = await bc.scan(t)
    assert [c["name"] for c in r.data["candidates"]] == ["cfg"]


# ── read ───────────────────────────────────────────────────────────


async def test_read_parses_and_reports_size():
    t = _T({"/sys/class/block": _ok("4096\n"), "od -c": _ok(_OD_REAL)})
    r = await bc.read(t, "cfgpart")
    assert r.ok
    d = r.data
    assert d["parsed"] is True
    assert d["size_bytes"] == 4096 * 512, "sysfs `size` is in 512-byte sectors, not bytes"
    assert [e["key"] for e in d["entries"]][:2] == ["MAIN_BOARD", "PORT_BOARD"]


async def test_read_that_does_not_parse_returns_raw_and_says_so():
    """The important one. An empty table would read as "the config is empty";
    the truth is almost always "that is not the config partition"."""
    t = _T({"/sys/class/block": _ok("8\n"), "od -c": _ok("0000000  \\0  \\0  \\0  \\0\n")})
    r = await bc.read(t, "userdata")
    assert r.ok
    assert r.data["parsed"] is False
    assert r.data["entries"] == []
    assert r.data["raw"] != "", "raw head must be carried so the operator can see why"


@pytest.mark.parametrize("name", ["../etc/passwd", "a b", "-w", "a/b", "", "x" * 65])
async def test_read_rejects_unsafe_device_names(name):
    """The name is interpolated into an on-device shell command."""
    t = _T({})
    r = await bc.read(t, name)
    assert not r.ok
    assert r.error is not None and r.error.code == "BOARD_CONFIG_BAD_DEVICE"
    assert t.seen == [], "a rejected name must not reach the device"


async def test_read_clamps_the_byte_count():
    """A one-click read of a multi-gigabyte partition would take out the
    browser and the tunnel together."""
    t = _T({"/sys/class/block": _ok("8\n"), "od -c": _ok(_OD_REAL)})
    r = await bc.read(t, "cfgpart", limit=10**9)
    assert r.data["read_bytes"] == bc.MAX_READ_BYTES
    r2 = await bc.read(t, "cfgpart", limit=0)
    assert r2.data["read_bytes"] == 1


async def test_read_failure_is_reported_not_swallowed():
    t = _T({"od -c": ShellResult(ok=False, exit_code=1, stdout="", stderr="Permission denied")})
    r = await bc.read(t, "cfgpart")
    assert not r.ok
    assert "Permission denied" in (r.error.message if r.error else "")
