"""Flash plans: a line-based flashing manifest checked as one unit.

What these lock is the property a plan exists for: every reason the run
would stop halfway is found BEFORE the first row, and the run is the plan
that was confirmed. A plan that stops at row 17 for a reason knowable at
row 0 leaves a half-written board.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from alb.remote.flash_plan import (
    AgentView,
    PlanRow,
    apply_skips,
    build_plan,
    check_agent,
    fingerprint,
    parse_manifest,
)
from alb.remote.protocol import LEGACY_FLASH_MAX_BYTES

MANIFEST = """\
tool setvar model model-x
tool flash loader_a                 loader.elf
tool flash loader_b                 loader.elf
tool reboot-bootloader
tool flash boot_a                   boot.img
tool flash boot_b                   boot.img
tool flash super                    super.img
tool erase misc
tool reboot
"""

FULL_AGENT = AgentView(
    caps=["adb", "fastboot", "fastboot-erase", "fastboot-large-image"], partitions=[]
)


def _package(root: Path, text: str = MANIFEST) -> Path:
    pkg = root / "images" / "board-x"
    pkg.mkdir(parents=True)
    (pkg / "loader.elf").write_bytes(b"L" * 100)
    (pkg / "boot.img").write_bytes(b"B" * 300)
    (pkg / "super.img").write_bytes(b"S" * 500)
    manifest = pkg / "flash.txt"
    manifest.write_text(text, encoding="utf-8")
    return manifest


def _where(root: Path, manifest: Path) -> tuple[str, Path]:
    return str(manifest.relative_to(root)), root.resolve()


async def _plan(root: Path, manifest: Path, *, skip=(), skip_actions=("setvar",), agent=FULL_AGENT):
    name, resolved = _where(root, manifest)
    return await build_plan(
        manifest,
        name=name,
        root=resolved,
        skip=list(skip),
        skip_actions=list(skip_actions),
        agent=agent,
    )


# ── parsing ─────────────────────────────────────────────────────────────


def test_parses_every_action_and_ignores_the_tool_column():
    rows, problems = parse_manifest(MANIFEST)
    assert problems == []
    assert [(r.line, r.action) for r in rows] == [
        (1, "setvar"),
        (2, "flash"),
        (3, "flash"),
        (4, "reboot-bootloader"),
        (5, "flash"),
        (6, "flash"),
        (7, "flash"),
        (8, "erase"),
        (9, "reboot"),
    ]
    assert (rows[0].key, rows[0].value) == ("model", "model-x")
    assert (rows[1].partition, rows[1].file) == ("loader_a", "loader.elf")
    assert rows[7].partition == "misc"


def test_padding_crlf_bom_and_blank_lines_are_tolerated():
    text = "\ufefftool  flash\tboot_a   boot.img\r\n\r\n   \r\ntool reboot\r\n"
    rows, problems = parse_manifest(text)
    assert problems == []
    assert [(r.line, r.action) for r in rows] == [(1, "flash"), (4, "reboot")]


def test_every_problem_is_reported_with_its_line_not_just_the_first():
    text = "\n".join(
        [
            "tool flash boot_a boot.img",
            "tool format userdata",  # unknown action
            "tool flash boot_b",  # missing the file
            "tool erase ../misc",  # malformed partition
            "lonely",  # no action column at all
            "tool setvar model has space",  # wrong arity
            "tool setvar -model x",  # malformed key
        ]
    )
    rows, problems = parse_manifest(text)
    assert [r.line for r in rows] == [1, 4, 7], "well-formed rows still parse"
    joined = "\n".join(problems)
    for line in (2, 3, 4, 5, 6, 7):
        assert f"line {line}:" in joined, f"line {line} problem missing:\n{joined}"


def test_an_empty_manifest_is_a_problem():
    assert parse_manifest("\n \n")[1] == ["the manifest has no rows"]


# ── skips ───────────────────────────────────────────────────────────────


def test_skip_by_partition_and_by_action_is_marked_with_its_reason():
    rows, _ = parse_manifest(MANIFEST)
    problems = apply_skips(rows, ["loader_a,loader_b"], ["setvar", "reboot-bootloader"])
    assert problems == []
    skipped = {r.line: r.skip for r in rows if not r.runs}
    assert skipped == {
        1: "--skip-action setvar",
        2: "--skip loader_a",
        3: "--skip loader_b",
        4: "--skip-action reboot-bootloader",
    }


def test_a_skip_that_matches_nothing_refuses_the_plan():
    """The skip is what protects the partition. A typo must not quietly
    flash the very thing it was meant to protect."""
    rows, _ = parse_manifest(MANIFEST)
    problems = apply_skips(rows, ["loader"], ["setvar"])
    assert len(problems) == 1
    assert "--skip loader matches no row" in problems[0]
    assert "loader_a" in problems[0], "say what the real names are"
    assert all(r.runs for r in rows if r.partition.startswith("loader"))


def test_skip_action_cannot_reach_flash_or_erase_rows():
    rows, _ = parse_manifest(MANIFEST)
    problems = apply_skips(rows, [], ["setvar", "flash"])
    assert any("--skip-action 'flash'" in p and "--skip" in p for p in problems)
    assert all(r.runs for r in rows if r.action == "flash")


def test_setvar_must_be_skipped_explicitly():
    rows, _ = parse_manifest(MANIFEST)
    problems = apply_skips(rows, [], [])
    assert len(problems) == 1
    assert problems[0].startswith("line 1: setvar cannot run")
    assert "--skip-action setvar" in problems[0]


def test_a_skip_action_that_matches_nothing_refuses_the_plan():
    rows, _ = parse_manifest("tool flash boot_a boot.img\n")
    assert apply_skips(rows, [], ["reboot"]) == ["--skip-action reboot matches no row"]


# ── what the agent would refuse halfway, refused up front ──────────────


def _running(text: str) -> list[PlanRow]:
    rows, problems = parse_manifest(text)
    assert problems == []
    return rows


def test_no_agent_is_a_problem():
    assert check_agent(_running("tool reboot\n"), None)


def test_partitions_off_the_agent_allowlist_are_named_with_their_lines():
    rows = _running(MANIFEST)
    apply_skips(rows, [], ["setvar"])
    agent = AgentView(caps=FULL_AGENT.caps, partitions=["boot_a", "boot_b", "loader_a", "loader_b"])
    problems = check_agent(rows, agent)
    assert len(problems) == 1
    assert "super (line 7)" in problems[0] and "misc (line 8)" in problems[0]
    assert "misc,super" in problems[0], "hand over the exact names to add"


def test_skipped_rows_do_not_need_to_be_allowed():
    rows = _running("tool flash loader_a loader.elf\ntool flash boot_a boot.img\n")
    apply_skips(rows, ["loader_a"], [])
    assert check_agent(rows, AgentView(caps=FULL_AGENT.caps, partitions=["boot_a"])) == []


def test_an_agent_too_old_to_erase_is_refused_before_the_first_row():
    rows = _running("tool flash boot_a boot.img\ntool erase misc\n")
    problems = check_agent(rows, AgentView(caps=["adb", "fastboot"], partitions=[]))
    assert len(problems) == 1 and "too old to erase (line 2)" in problems[0]


def test_an_agent_with_the_old_size_ceiling_is_refused_for_a_large_image():
    rows = _running("tool flash super super.img\n")
    rows[0].size = LEGACY_FLASH_MAX_BYTES + 1
    old = AgentView(caps=["adb", "fastboot", "fastboot-erase"], partitions=[])
    assert any("refuses images over" in p for p in check_agent(rows, old))
    assert check_agent(rows, FULL_AGENT) == []


# ── the whole plan, against real files ─────────────────────────────────


async def test_a_valid_plan_carries_sizes_and_both_digests(tmp_path):
    manifest = _package(tmp_path)
    plan = await _plan(tmp_path, manifest, skip=["loader_a", "loader_b"])
    assert plan.ok, plan.problems
    boot = next(r for r in plan.rows if r.partition == "boot_a")
    assert boot.size == 300
    assert boot.md5 == hashlib.md5(b"B" * 300).hexdigest()
    assert boot.sha256 == hashlib.sha256(b"B" * 300).hexdigest()
    assert boot.path == (manifest.parent / "boot.img").resolve()
    summary = plan.as_dict()
    assert (summary["total"], summary["run"], summary["skipped"]) == (9, 6, 3)


async def test_a_missing_image_refuses_the_plan(tmp_path):
    manifest = _package(tmp_path, MANIFEST + "tool flash vendor_a vendor.img\n")
    plan = await _plan(tmp_path, manifest)
    assert not plan.ok
    assert any("line 10" in p and "not found" in p for p in plan.problems)


async def test_a_skipped_row_does_not_need_its_image(tmp_path):
    """A package stripped of the very image being protected is still a valid
    package to run with that skip."""
    manifest = _package(tmp_path)
    (manifest.parent / "loader.elf").unlink()
    plan = await _plan(tmp_path, manifest, skip=["loader_a", "loader_b"])
    assert plan.ok, plan.problems


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        # manifest sits at ws/images/board-x/, so three levels up is tmp_path,
        # where the file really exists — refused for WHERE it is, not absence
        ("../../../outside.img", "outside the workspace"),
        ("/etc/hostname", "must be relative"),
        ("C:\\images\\x.img", "must be relative"),
    ],
)
async def test_an_image_outside_the_workspace_is_refused(tmp_path, name, reason):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (tmp_path / "outside.img").write_bytes(b"x")
    manifest = _package(workspace, f"tool flash boot_a {name}\n")
    plan = await _plan(workspace, manifest, skip_actions=())
    assert plan.rows[0].path is None
    assert len(plan.problems) == 1 and reason in plan.problems[0], plan.problems


async def test_backslash_paths_from_a_windows_manifest_resolve(tmp_path):
    manifest = _package(tmp_path, "tool flash boot_a sub\\boot.img\n")
    (manifest.parent / "sub").mkdir()
    (manifest.parent / "sub" / "boot.img").write_bytes(b"b")
    plan = await _plan(tmp_path, manifest, skip_actions=())
    assert plan.ok, plan.problems


async def test_an_empty_image_is_refused(tmp_path):
    manifest = _package(tmp_path, "tool flash boot_a empty.img\n")
    (manifest.parent / "empty.img").write_bytes(b"")
    plan = await _plan(tmp_path, manifest, skip_actions=())
    assert any("is empty" in p for p in plan.problems)


async def test_a_binary_file_is_not_a_manifest(tmp_path):
    manifest = _package(tmp_path)
    manifest.write_bytes(b"\xff\xfe\x00garbage")
    plan = await _plan(tmp_path, manifest)
    assert plan.problems == ["the manifest is not UTF-8 text"]


# ── the fingerprint: what was confirmed is what runs ───────────────────


async def test_fingerprint_is_stable_for_the_same_plan(tmp_path):
    manifest = _package(tmp_path)
    a = await _plan(tmp_path, manifest)
    b = await _plan(tmp_path, manifest)
    assert a.fingerprint == b.fingerprint and len(a.fingerprint) == 64


async def test_fingerprint_changes_when_an_image_changes(tmp_path):
    manifest = _package(tmp_path)
    before = await _plan(tmp_path, manifest)
    (manifest.parent / "boot.img").write_bytes(b"C" * 300)  # same size, new bytes
    after = await _plan(tmp_path, manifest)
    assert before.fingerprint != after.fingerprint


async def test_fingerprint_changes_when_the_skips_change(tmp_path):
    manifest = _package(tmp_path)
    a = await _plan(tmp_path, manifest)
    b = await _plan(tmp_path, manifest, skip=["loader_a"])
    assert a.fingerprint != b.fingerprint


def test_fingerprint_covers_the_decision_not_just_the_rows():
    rows, _ = parse_manifest("tool flash boot_a boot.img\n")
    run = fingerprint("m", rows)
    rows[0].skip = "--skip boot_a"
    assert fingerprint("m", rows) != run
