"""boot_stages.detect_boot / timeline_view -- pure functions on sample lines."""

from __future__ import annotations

import pytest

from alb.capabilities.boot_stages import detect_boot, match_milestone
from alb.capabilities.timeline_view import classify_marker, density, line_view, parse_logcat


def _rec(t: float, src: str, line: str, **extra) -> dict:
    return {"t": t, "wall": "2026-01-01T00:00:00.000+00:00", "src": src, "line": line, **extra}


def _event(tag: str) -> str:
    return f"10-23 12:00:01.000  1000  1000 I {tag}: 4242"


FULL_BOOT = [
    _rec(0.1, "uart", "U-Boot SPL 2020.07"),
    _rec(2.5, "uart", "Starting kernel ..."),
    _rec(2.9, "dmesg", "[    0.000000] Booting Linux on physical CPU 0x0"),
    _rec(4.0, "dmesg", "[    1.300000] Run /init as init process"),
    _rec(9.0, "logcat", _event("boot_progress_start")),
    _rec(9.5, "logcat", _event("boot_progress_preload_start")),
    _rec(12.0, "logcat", _event("boot_progress_preload_end")),
    _rec(13.0, "logcat", _event("boot_progress_system_run")),
    _rec(15.0, "logcat", _event("boot_progress_pms_ready")),
    _rec(18.0, "logcat", _event("boot_progress_ams_ready")),
    _rec(19.0, "logcat", _event("boot_progress_enable_screen")),
    _rec(21.0, "logcat", "10-23 12:00:12.000   500   500 I init: Setting property 'sys.boot_completed'='1'"),
]


def test_full_boot_has_every_milestone_in_time_order() -> None:
    rep = detect_boot(FULL_BOOT)
    assert rep.found is True
    keys = [m["key"] for m in rep.milestones]
    assert keys == [
        "uart_handoff",
        "dmesg_first",
        "init_start",
        "boot_progress_start",
        "boot_progress_preload_start",
        "boot_progress_preload_end",
        "boot_progress_system_run",
        "boot_progress_pms_ready",
        "boot_progress_ams_ready",
        "boot_progress_enable_screen",
        "boot_completed",
    ]
    assert [m["t"] for m in rep.milestones] == sorted(m["t"] for m in rep.milestones)
    assert rep.total_s == pytest.approx(21.0 - 2.5)
    handoff = rep.milestones[0]
    assert handoff == {
        "key": "uart_handoff",
        "label": "Bootloader hands off to kernel",
        "src": "uart",
        "t": 2.5,
    }


def test_stages_span_consecutive_milestones() -> None:
    stages = {s["key"]: s for s in detect_boot(FULL_BOOT).stages}
    assert stages["bootloader"] == {"key": "bootloader", "src": "uart", "t0": 0.1, "t1": 2.5}
    assert stages["kernel"]["t0"] == 2.9 and stages["kernel"]["t1"] == 4.0
    assert stages["zygote_preload"]["t1"] - stages["zygote_preload"]["t0"] == pytest.approx(2.5)
    assert stages["boot_completed"]["t0"] == 19.0 and stages["boot_completed"]["t1"] == 21.0


def test_preload_start_is_not_taken_for_boot_progress_start() -> None:
    assert match_milestone("logcat", _event("boot_progress_preload_start")) == (
        "boot_progress_preload_start"
    )
    assert match_milestone("logcat", _event("boot_progress_start")) == "boot_progress_start"


def test_a_milestone_only_counts_on_its_own_source() -> None:
    # The kernel string on the UART is not the dmesg milestone, and an event
    # tag echoed on the console is not a logcat event.
    assert match_milestone("uart", "[ 1.3] Run /init as init process") is None
    assert match_milestone("uart", "boot_progress_start: 1") is None
    assert match_milestone("dmesg", "Starting kernel ...") is None


def test_no_boot_when_only_dmesg_is_quiet_running_system() -> None:
    rep = detect_boot(
        [_rec(1.0, "dmesg", "[ 9000.1] some driver"), _rec(2.0, "logcat", "10-23 12:00:00.000 1 1 I x: y")]
    )
    assert rep.found is False  # a first dmesg line alone is not a boot
    assert [m["key"] for m in rep.milestones] == ["dmesg_first"]
    assert rep.stages == []


def test_missing_events_buffer_leaves_android_milestones_absent() -> None:
    rep = detect_boot(FULL_BOOT[:4])
    assert rep.found is True
    assert not any(m["key"].startswith("boot_progress") for m in rep.milestones)


def test_alb_notes_are_not_board_output() -> None:
    rep = detect_boot([_rec(1.0, "uart", "[alb] Starting kernel", note=True)])
    assert rep.found is False and rep.milestones == []


def test_first_means_smallest_t_even_when_read_late() -> None:
    rep = detect_boot(
        [_rec(5.0, "logcat", _event("boot_progress_start")), _rec(4.9, "logcat", _event("boot_progress_start"))]
    )
    assert rep.milestones[0]["t"] == 4.9


def test_stage_needs_both_ends_in_order() -> None:
    rep = detect_boot(
        [_rec(3.0, "dmesg", "[ 1.0] Run /init as init process"), _rec(1.0, "dmesg", "[ 0.0] boot")]
    )
    stages = {s["key"] for s in rep.stages}
    assert "kernel" in stages  # dmesg_first (1.0) -> init_start (3.0)
    assert "bootloader" not in stages


# ─── timeline_view ─────────────────────────────────────────────────
def test_parse_logcat_threadtime() -> None:
    line = "10-23 12:00:01.123  1234  5678 W ActivityManager: Slow operation: 120ms"
    assert parse_logcat(line) == ("W", "ActivityManager", 1234)


def test_parse_logcat_tag_with_spaces_and_event_tag() -> None:
    assert parse_logcat("10-23 12:00:01.000  1000  1000 I boot_progress_start: 4242") == (
        "I",
        "boot_progress_start",
        1000,
    )
    assert parse_logcat("10-23 12:00:01.000   10   10 E Some Tag : msg")[1] == "Some Tag"


@pytest.mark.parametrize("text", ["--------- beginning of main", "[    1.0] kernel line", ""])
def test_parse_logcat_rejects_non_threadtime(text: str) -> None:
    assert parse_logcat(text) == (None, None, None)


def test_line_view_only_parses_logcat() -> None:
    kernel = line_view(_rec(1.0, "dmesg", "10-23 12:00:01.123  1  1 W X: y"))
    assert kernel["level"] is None and kernel["tag"] is None
    lc = line_view(_rec(1.5, "logcat", "10-23 12:00:01.123  7  7 E Foo: boom"))
    assert (lc["level"], lc["tag"], lc["pid"], lc["text"], lc["t"]) == ("E", "Foo", 7, lc["text"], 1.5)
    assert lc["ts"] == "2026-01-01T00:00:00.000+00:00"


@pytest.mark.parametrize(
    ("src", "text", "kind"),
    [
        ("dmesg", "Kernel panic - not syncing: Fatal exception", "panic"),
        ("uart", "Unable to handle kernel NULL pointer dereference", "panic"),
        ("logcat", "10-23 12:00:00.000 1 1 E ActivityManager: ANR in com.example.app", "anr"),
        ("logcat", "10-23 12:00:00.000 1 1 E AndroidRuntime: FATAL EXCEPTION: main", "crash"),
        ("dmesg", "Fatal signal 11 (SIGSEGV)", "crash"),
        ("dmesg", "[ 5.0] audit: avc:  denied  { read } for pid=1", "avc"),
        ("uart", "Starting kernel ...", "boot"),
        ("uart", "[alb] uart link up", "alb"),
        ("uart", "just a console line", None),
    ],
)
def test_classify_marker(src: str, text: str, kind: str | None) -> None:
    assert classify_marker(src, text) == kind


def test_density_buckets_and_markers() -> None:
    recs = [
        _rec(0.2, "uart", "hello"),
        _rec(0.8, "uart", "world"),
        _rec(1.5, "dmesg", "[ 1.0] x"),
        _rec(2.1, "logcat", "10-23 12:00:00.000 1 1 E Foo: boom"),
        _rec(2.2, "logcat", "10-23 12:00:00.000 1 1 W Foo: hmm"),
        _rec(2.3, "logcat", "no level here"),
        _rec(2.4, "uart", "[alb] uart unavailable: gone", note=True),
        _rec(2.9, "dmesg", "Kernel panic - not syncing"),
    ]
    d = density(recs, 1.0)
    assert d["t0"] == 0.0 and d["t1"] == 2.9 and d["bucket_s"] == 1.0
    assert d["tracks"]["uart"] == [2, 0, 0]  # the alb note is not board output
    assert d["tracks"]["dmesg"] == [0, 1, 1]
    assert d["tracks"]["logcat"]["E"] == [0, 0, 1]
    assert d["tracks"]["logcat"]["W"] == [0, 0, 1]
    assert sum(sum(v) for v in d["tracks"]["logcat"].values()) == 2  # unparsed line skipped
    assert [(m["kind"], m["t"]) for m in d["markers"]] == [("alb", 2.4), ("panic", 2.9)]


def test_density_of_nothing_is_one_empty_bucket() -> None:
    d = density([], 1.0)
    assert d["tracks"]["uart"] == [0] and d["markers"] == []


def test_density_widens_the_bucket_instead_of_returning_a_giant_array() -> None:
    d = density([_rec(100000.0, "uart", "x")], 0.1)
    assert len(d["tracks"]["uart"]) <= 20000
    assert d["bucket_s"] > 0.1
