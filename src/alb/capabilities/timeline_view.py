"""Reading a finished timeline capture for display (pure, no I/O).

`capabilities/timeline.py` writes `timeline.jsonl`; this module turns its
line records into what a chart or a log viewer needs: parsed logcat fields,
per-bucket line counts, and a short list of events worth jumping to.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from typing import Any, Final

from alb.capabilities.boot_stages import match_milestone

LOGCAT_LEVELS: Final[tuple[str, ...]] = ("V", "D", "I", "W", "E", "F")

# `logcat -v threadtime`:  MM-DD HH:MM:SS.mmm  PID  TID L TAG: message
_THREADTIME: Final = re.compile(
    r"^\d\d-\d\d \d\d:\d\d:\d\d\.\d{3}\s+(\d+)\s+\d+\s+([VDIWEF])\s+(.*?)\s*:(?:\s|$)"
)

_MARKER_TEXT_MAX: Final[int] = 200
# One noisy kind (avc denials come by the hundred) must not drown the rest.
_MARKER_PER_KIND: Final[int] = 200
# Density buckets in one response: a 10 min capture at 0.1 s is 6000.
MAX_BUCKETS: Final[int] = 20000

_MARKER_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("panic", re.compile(r"Kernel panic|Unable to handle kernel|Internal error: Oops")),
    ("anr", re.compile(r"\bANR in ")),
    ("crash", re.compile(r"FATAL EXCEPTION|Fatal signal")),
    ("avc", re.compile(r"avc:\s+denied")),
)


def parse_logcat(text: str) -> tuple[str | None, str | None, int | None]:
    """``(level, tag, pid)`` of a threadtime line, ``(None, None, None)`` otherwise."""
    m = _THREADTIME.match(text)
    if m is None:
        return None, None, None
    return m.group(2), m.group(3), int(m.group(1))


def line_view(rec: dict[str, Any]) -> dict[str, Any]:
    """A raw timeline record as the lines endpoint returns it."""
    text = str(rec.get("line", ""))
    src = str(rec.get("src", ""))
    level = tag = pid = None
    if src == "logcat":
        level, tag, pid = parse_logcat(text)
    return {
        "t": float(rec.get("t", 0.0)),
        "ts": rec.get("wall"),
        "src": src,
        "text": text,
        "level": level,
        "tag": tag,
        "pid": pid,
    }


def classify_marker(src: str, text: str, *, note: bool = False) -> str | None:
    """The marker kind a line deserves, or None. alb's own lines first: a
    note quoting a panic string is still alb talking."""
    if text.startswith("[alb]"):
        return "alb"
    if note:
        return None
    for kind, pat in _MARKER_PATTERNS:
        if pat.search(text):
            return kind
    if match_milestone(src, text) is not None:
        return "boot"
    return None


def density(records: Iterable[dict[str, Any]], bucket_s: float) -> dict[str, Any]:
    """Line counts per time bucket for each source, plus jump-to markers.

    Buckets start at t=0 (the capture start), so ``tracks[...][i]`` covers
    ``[i * bucket_s, (i + 1) * bucket_s)``. The bucket size is raised when
    the span would need more than ``MAX_BUCKETS``; the returned ``bucket_s``
    is the one actually used.
    """
    recs = [r for r in records if "line" in r]
    t1 = max((float(r.get("t", 0.0)) for r in recs), default=0.0)
    bucket = bucket_s
    if t1 / bucket + 1 > MAX_BUCKETS:
        bucket = math.ceil(t1 / (MAX_BUCKETS - 1) * 1000) / 1000
    n = int(t1 / bucket) + 1
    uart = [0] * n
    dmesg = [0] * n
    logcat = {lv: [0] * n for lv in LOGCAT_LEVELS}
    markers: list[dict[str, Any]] = []
    per_kind: dict[str, int] = {}
    for r in recs:
        t = float(r.get("t", 0.0))
        i = min(max(int(t / bucket), 0), n - 1)
        src = str(r.get("src", ""))
        text = str(r.get("line", ""))
        note = bool(r.get("note"))
        if not note:
            if src == "uart":
                uart[i] += 1
            elif src == "dmesg":
                dmesg[i] += 1
            elif src == "logcat":
                level = parse_logcat(text)[0]
                if level is not None:
                    logcat[level][i] += 1
        kind = classify_marker(src, text, note=note)
        if kind is not None and per_kind.get(kind, 0) < _MARKER_PER_KIND:
            per_kind[kind] = per_kind.get(kind, 0) + 1
            markers.append({"t": t, "src": src, "kind": kind, "text": text[:_MARKER_TEXT_MAX]})
    markers.sort(key=lambda m: m["t"])
    return {
        "t0": 0.0,
        "t1": t1,
        "bucket_s": bucket,
        "tracks": {"uart": uart, "dmesg": dmesg, "logcat": logcat},
        "markers": markers,
    }
