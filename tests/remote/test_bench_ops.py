"""The hub's view of the agent's op table (ADR-059).

What is pinned here: values are checked by kind the same way the agent does
(so a typo fails before a channel opens), an agent from before the table gets
a table that errs on the side of "changes the device", and a malformed row
from a newer agent is dropped instead of taking the bench offline.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from alb.remote.bench_ops import (
    OpSpec,
    ParamSpec,
    check_params,
    legacy_ops,
    ops_of,
    parse_ops,
)
from alb.remote.protocol import LEGACY_FLASH_MAX_BYTES

# ── values, by kind ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "allowlist", "ok"),
    [
        ("boot_a", [], True),
        ("boot_a", ["boot_a", "misc"], True),
        ("super", ["boot_a", "misc"], False),  # well formed, not allowed
        ("-w", [], False),  # would become a fastboot flag
        ("boot a", [], False),  # would become two argv elements
        ("../x", [], False),
        (7, [], False),
    ],
)
def test_partition_kind(value, allowlist, ok):
    _, problem = ParamSpec("partition", "partition").check(value, allowlist)
    assert (problem == "") is ok


def test_enum_is_an_exact_match():
    p = ParamSpec("slot", "enum", choices=("a", "b"))
    assert p.check("a", []) == ("a", "")
    for bad in ("A", " a", "c", "a\n"):
        assert p.check(bad, [])[1], f"{bad!r} must be refused — no folding, no strip"


def test_int_accepts_decimal_strings_and_refuses_bools():
    p = ParamSpec("lines", "int", False, 200, min=1, max=2000)
    assert p.check("50", []) == (50, "")
    assert p.check(50, []) == (50, "")
    assert p.check(True, [])[1], "True is an int in Python — and never what was meant"
    assert p.check("0", [])[1] and p.check(2001, [])[1]
    assert p.check("5.5", [])[1]


def test_text_is_shape_and_length_checked():
    p = ParamSpec("grep", "text", False, "", pattern=r"[^\x00-\x1f\x7f]*", max_len=8)
    assert p.check("fastboot", []) == ("fastboot", "")
    assert p.check("too long here", [])[1]
    assert p.check("a\nb", [])[1]


def test_a_pattern_this_side_cannot_compile_is_left_to_the_agent():
    p = ParamSpec("x", "text", pattern="(?P<", max_len=10)
    assert p.check("abc", []) == ("abc", "")


def test_an_unknown_kind_from_a_newer_agent_passes_through():
    assert ParamSpec("x", "comport").check("COM3", []) == ("COM3", "")


def test_missing_optional_takes_the_default_and_missing_required_is_refused():
    assert ParamSpec("t", "enum", False, "", ("bootloader",)).check(None, []) == ("", "")
    assert ParamSpec("t", "enum", choices=("a",)).check("", [])[1] == "t is required"


# ── whole-op checks ──────────────────────────────────────────────────────


def _erase() -> OpSpec:
    return OpSpec("erase", params=(ParamSpec("partition", "partition"),))


def test_an_unknown_parameter_is_an_error_not_ignored():
    _, code, problem = check_params(_erase(), {"partiton": "misc"}, [])
    assert code == "BENCH_PARAM_REJECTED"
    assert "partiton" in problem and "partition" in problem


@pytest.mark.parametrize(
    ("spec", "params", "code"),
    [
        (_erase(), {"partition": "-x"}, "FLASH_PARTITION_REJECTED"),
        (
            OpSpec("oem", params=(ParamSpec("command", "enum", choices=("at-lock-vboot",)),)),
            {"command": "unlock"},
            "FLASH_OEM_REJECTED",
        ),
        (
            OpSpec("getvar", params=(ParamSpec("name", "text", pattern=r"[a-z]+", max_len=8),)),
            {"name": "A B"},
            "FLASH_VAR_REJECTED",
        ),
        (
            OpSpec("set-active", params=(ParamSpec("slot", "enum", choices=("a", "b")),)),
            {"slot": "c"},
            "BENCH_PARAM_REJECTED",
        ),
    ],
)
def test_rejections_keep_the_codes_callers_already_branch_on(spec, params, code):
    assert check_params(spec, params, [])[1] == code


def test_values_are_normalised_for_the_agent():
    spec = OpSpec(
        "log-tail",
        params=(
            ParamSpec("source", "enum", choices=("agent",)),
            ParamSpec("lines", "int", False, 200, min=1, max=2000),
            ParamSpec("grep", "text", False, "", max_len=64),
        ),
    )
    values, code, _ = check_params(spec, {"source": "agent", "lines": "30"}, [])
    assert code == "" and values == {"source": "agent", "lines": 30, "grep": ""}


# ── tables from hellos ───────────────────────────────────────────────────


def test_no_ops_field_is_not_the_same_as_an_empty_table():
    assert parse_ops(None) is None
    assert parse_ops([]) == {}


def test_malformed_rows_are_dropped_and_the_rest_kept():
    table = parse_ops(
        [
            {"name": "erase", "params": [{"name": "partition", "kind": "partition"}]},
            {"name": "bad name with spaces"},
            "not a dict",
            {"params": []},
        ]
    )
    assert list(table or {}) == ["erase"]


def test_a_row_that_does_not_say_is_treated_as_changing_the_device():
    spec = OpSpec.from_wire({"name": "mystery"})
    assert spec is not None and spec.mutates is True and spec.exclusive is True


def test_an_unknown_record_tier_falls_back_to_events():
    spec = OpSpec.from_wire({"name": "x", "record": "everything"})
    assert spec is not None and spec.record == "events"


def test_round_trip_through_the_wire_form():
    spec = OpSpec(
        "set-active",
        params=(ParamSpec("slot", "enum", choices=("a", "b")),),
        needs="fastboot-device",
        timeout_s=30.0,
    )
    assert OpSpec.from_wire(spec.as_dict()) == spec


def test_an_agent_without_fastboot_has_no_legacy_ops():
    assert legacy_ops(["adb"]) == {}


def test_legacy_table_is_the_five_old_ops_all_treated_as_mutating():
    table = legacy_ops(["adb", "fastboot"])
    assert sorted(table) == ["devices", "flash", "getvar", "oem", "reboot"]
    assert all(spec.mutates for spec in table.values())
    assert table["flash"].max_image_bytes == LEGACY_FLASH_MAX_BYTES
    assert "erase" not in table, "an old agent cannot erase — a plan must be refused up front"


def test_ops_of_prefers_the_reported_table():
    reported = SimpleNamespace(
        caps=["adb", "fastboot"], op_table={"usb-list": OpSpec("usb-list", group="host")}
    )
    old = SimpleNamespace(caps=["adb", "fastboot"])

    assert list(ops_of(reported)) == ["usb-list"]
    assert "flash" in ops_of(old)
    assert ops_of(None) == {}


def test_a_row_with_an_unreadable_timeout_is_dropped_not_fatal() -> None:
    """parse_ops runs before the hub answers hello; an exception there drops
    the agent's session and it reconnects forever. One bad row is skipped."""
    from alb.remote.bench_ops import parse_ops

    table = parse_ops(
        [
            {"name": "good", "timeout_s": 30},
            {"name": "bad", "timeout_s": "soon"},
            {"name": "worse", "timeout_s": float("nan")},
            {"name": "negative", "timeout_s": -1},
        ]
    )
    assert table is not None
    assert list(table) == ["good"]
    assert table["good"].timeout_s == 30.0


@pytest.mark.parametrize("value", ["misc\n", "boot_a\n"])
def test_shape_rules_do_not_let_a_trailing_newline_through(value: str) -> None:
    """`$` also matches before a final newline; `\\Z` does not. What the
    check passed must be exactly what reaches fastboot's argv."""
    from alb.remote.bench_ops import OP_NAME_RE, PARTITION_RE
    from alb.remote.flash_plan import VAR_RE

    assert PARTITION_RE.match(value) is None
    assert OP_NAME_RE.match(value) is None
    assert VAR_RE.match(value) is None
