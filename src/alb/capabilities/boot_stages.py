"""Boot milestones found in a merged timeline (pure, no I/O).

A timeline capture that spans a reboot holds the whole boot on one clock:
the bootloader on the UART, the kernel in dmesg, Android's boot events in
logcat. This module reads the line records of such a capture and answers
"when did each stage start", so a reader sees where the time went instead of
scrolling tens of thousands of lines.

Input is any iterable of dicts shaped like `timeline.jsonl` line records:
``{"t": seconds, "src": "uart" | "dmesg" | "logcat", "line": text}``.
Records with ``"note": true`` (alb's own commentary) are skipped, because a
note such as "[alb] uart link up" is not something the board printed.

Milestone keys, in the order a normal boot reaches them:

    uart_handoff       UART line "Starting kernel" (bootloader -> kernel)
    dmesg_first        first kernel line seen in the capture
    init_start         dmesg "Run /init as init process"
    boot_progress_*    Android event-log tags from system_server / zygote
    boot_completed     logcat ``sys.boot_completed``

Event-log tags only reach logcat when the *events* buffer is read; without
it the Android milestones are simply absent -- absent is reported as absent,
never guessed.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Final


@dataclass(frozen=True)
class _Spec:
    key: str
    label: str
    src: str
    pattern: re.Pattern[str]


def _tag(key: str, label: str) -> _Spec:
    return _Spec(key, label, "logcat", re.compile(rf"\b{key}\b"))


# Order = the order a boot normally reaches them; also the report order.
_SPECS: Final[tuple[_Spec, ...]] = (
    _Spec("uart_handoff", "Bootloader hands off to kernel", "uart", re.compile(r"Starting kernel")),
    _Spec("init_start", "Kernel runs /init", "dmesg", re.compile(r"Run /init as init process")),
    _tag("boot_progress_start", "Zygote starts"),
    _tag("boot_progress_preload_start", "Class preload starts"),
    _tag("boot_progress_preload_end", "Class preload ends"),
    _tag("boot_progress_system_run", "system_server running"),
    _tag("boot_progress_pms_ready", "PackageManager ready"),
    _tag("boot_progress_ams_ready", "ActivityManager ready"),
    _tag("boot_progress_enable_screen", "Screen enabled"),
    _Spec("boot_completed", "sys.boot_completed", "logcat", re.compile(r"sys\.boot_completed")),
)

_LABELS: Final[dict[str, str]] = {s.key: s.label for s in _SPECS}
_LABELS["dmesg_first"] = "First kernel line"

# (key, src, start milestone, end milestone). A stage exists only when both
# ends were seen and the end is not before the start. ``uart_first`` is the
# first UART line of the capture -- internal, not a milestone: a capture that
# merely started listening says nothing about when the bootloader began.
_STAGES: Final[tuple[tuple[str, str, str, str], ...]] = (
    ("bootloader", "uart", "uart_first", "uart_handoff"),
    ("kernel", "dmesg", "dmesg_first", "init_start"),
    ("userspace_init", "logcat", "init_start", "boot_progress_start"),
    ("zygote_preload", "logcat", "boot_progress_preload_start", "boot_progress_preload_end"),
    ("system_server", "logcat", "boot_progress_system_run", "boot_progress_ams_ready"),
    ("ui_ready", "logcat", "boot_progress_ams_ready", "boot_progress_enable_screen"),
    ("boot_completed", "logcat", "boot_progress_enable_screen", "boot_completed"),
)

# What counts as "this capture saw a boot". dmesg_first is excluded on
# purpose: every capture with dmesg has a first line, so it proves nothing.
_BOOT_KEYS: Final[frozenset[str]] = frozenset(s.key for s in _SPECS)


def match_milestone(src: str, text: str) -> str | None:
    """The milestone key a single line names, or None."""
    for spec in _SPECS:
        if spec.src == src and spec.pattern.search(text):
            return spec.key
    return None


def _keep_min(seen: dict[str, float], key: str, t: float) -> None:
    if key not in seen or t < seen[key]:
        seen[key] = t


@dataclass(frozen=True)
class BootReport:
    found: bool
    total_s: float
    milestones: list[dict[str, Any]]
    stages: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "found": self.found,
            "total_s": self.total_s,
            "milestones": self.milestones,
            "stages": self.stages,
        }


def detect_boot(records: Iterable[dict[str, Any]]) -> BootReport:
    """Find the earliest occurrence of every milestone in ``records``.

    ``total_s`` is the span from the earliest to the latest milestone found
    (0.0 when fewer than two). It is the time between what was *seen*, not a
    claim about when the boot began.
    """
    first: dict[str, float] = {}
    for rec in records:
        if rec.get("note"):
            continue
        src = rec.get("src")
        text = rec.get("line")
        if not isinstance(src, str) or not isinstance(text, str):
            continue
        try:
            t = float(rec.get("t", 0.0))
        except (TypeError, ValueError):
            continue
        # Records are stamped at arrival and a file is only nearly sorted, so
        # "first" means the smallest t, not the first one read.
        if src == "uart":
            _keep_min(first, "uart_first", t)
        if src == "dmesg":
            _keep_min(first, "dmesg_first", t)
        key = match_milestone(src, text)
        if key is not None:
            _keep_min(first, key, t)

    milestones: list[dict[str, Any]] = []
    if "dmesg_first" in first:
        milestones.append(
            {
                "key": "dmesg_first",
                "label": _LABELS["dmesg_first"],
                "src": "dmesg",
                "t": first["dmesg_first"],
            }
        )
    for spec in _SPECS:
        if spec.key in first:
            milestones.append(
                {"key": spec.key, "label": spec.label, "src": spec.src, "t": first[spec.key]}
            )
    milestones.sort(key=lambda m: m["t"])

    stages = [
        {"key": key, "src": src, "t0": first[a], "t1": first[b]}
        for key, src, a, b in _STAGES
        if a in first and b in first and first[b] >= first[a]
    ]
    found = any(m["key"] in _BOOT_KEYS for m in milestones)
    times = [m["t"] for m in milestones]
    total = round(max(times) - min(times), 3) if len(times) >= 2 else 0.0
    return BootReport(found=found, total_s=total, milestones=milestones, stages=stages)
