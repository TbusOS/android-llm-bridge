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
from tests.remote.bench_tables import new_agent_ops

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

# A current agent: reports its op table, erase included, no image ceiling.
FULL_AGENT = AgentView(caps=["adb", "fastboot"], partitions=[], ops=new_agent_ops())


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


def test_comment_lines_are_ignored():
    """Vendor manifests open with a `#` title line; it used to be read as
    tool `#` + action `<first word>` and refuse the whole package. A
    commented-out row is not run either — that is what commenting it out
    means."""
    text = "# board-x flashing commands\ntool flash boot_a boot.img\n  # note\n#tool erase misc\n"
    rows, problems = parse_manifest(text)
    assert problems == []
    assert [(r.line, r.action) for r in rows] == [(2, "flash")]


def test_getvar_and_oem_rows_parse():
    rows, problems = parse_manifest("tool getvar check-firmware-board-x\ntool oem at-lock-vboot\n")
    assert problems == []
    assert [(r.action, r.key, r.command) for r in rows] == [
        ("getvar", "check-firmware-board-x", ""),
        ("oem", "", "at-lock-vboot"),
    ]
    assert [r.describe() for r in rows] == ["getvar check-firmware-board-x", "oem at-lock-vboot"]
    assert rows[1].as_dict()["command"] == "at-lock-vboot"


def test_malformed_getvar_and_oem_rows_are_problems():
    text = "tool getvar -x\ntool oem a;b\ntool oem at-lock-vboot now\ntool getvar\n"
    _, problems = parse_manifest(text)
    joined = "\n".join(problems)
    assert "line 1: getvar name" in joined
    assert "line 2: oem command" in joined
    assert "line 3: oem takes 1 argument(s), got 2" in joined
    assert "line 4: getvar takes 1 argument(s), got 0" in joined


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


RESUME_MANIFEST = """\
tool getvar product
tool flash loader_a                 loader.elf
tool reboot-bootloader
tool flash boot_a                   boot.img
tool flash super                    super.img
tool reboot
"""


def test_from_line_skips_the_rows_before_it_but_keeps_getvar_checks() -> None:
    """Resuming at the row that failed: everything before it is skipped,
    except the getvar check, which writes nothing and is what makes sure
    the board is still the one the package is for."""
    rows, _ = parse_manifest(RESUME_MANIFEST)
    assert apply_skips(rows, [], [], from_line=4) == []
    assert {r.line: r.skip for r in rows if not r.runs} == {2: "--from-line 4", 3: "--from-line 4"}
    assert [r.line for r in rows if r.runs] == [1, 4, 5, 6]


def test_from_line_keeps_an_explicit_skip_reason_and_must_name_a_row() -> None:
    rows, _ = parse_manifest(RESUME_MANIFEST)
    assert apply_skips(rows, ["loader_a"], [], from_line=4) == []
    assert rows[1].skip == "--skip loader_a"
    rows, _ = parse_manifest(RESUME_MANIFEST)
    problems = apply_skips(rows, [], [], from_line=99)
    assert len(problems) == 1 and "--from-line 99 is not a row" in problems[0]
    assert all(r.runs for r in rows), "a bad line resumes nothing"


def test_from_line_changes_the_fingerprint() -> None:
    """The confirmation covers where the run starts."""
    whole, _ = parse_manifest(RESUME_MANIFEST)
    apply_skips(whole, [], [])
    resumed, _ = parse_manifest(RESUME_MANIFEST)
    apply_skips(resumed, [], [], from_line=4)
    assert fingerprint("m", whole) != fingerprint("m", resumed)


def test_a_skip_action_that_matches_nothing_refuses_the_plan():
    rows, _ = parse_manifest("tool flash boot_a boot.img\n")
    assert apply_skips(rows, [], ["reboot"]) == ["--skip-action reboot matches no row"]


def test_getvar_and_oem_run_unless_skipped_by_action():
    """Both run by default — the vendor listed them — and either can be left
    out by name, like any row without a partition."""
    text = "tool getvar check-firmware-board-x\ntool oem at-lock-vboot\ntool reboot\n"
    rows, _ = parse_manifest(text)
    assert apply_skips(rows, [], []) == []
    assert all(r.runs for r in rows)
    rows, _ = parse_manifest(text)
    assert apply_skips(rows, [], ["getvar,oem"]) == []
    assert {r.line: r.skip for r in rows if not r.runs} == {
        1: "--skip-action getvar",
        2: "--skip-action oem",
    }


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
    agent = AgentView(
        caps=FULL_AGENT.caps,
        partitions=["boot_a", "boot_b", "loader_a", "loader_b"],
        ops=FULL_AGENT.ops,
    )
    problems = check_agent(rows, agent)
    assert len(problems) == 1
    assert "super (line 7)" in problems[0] and "misc (line 8)" in problems[0]
    assert "misc,super" in problems[0], "hand over the exact names to add"


def test_skipped_rows_do_not_need_to_be_allowed():
    rows = _running("tool flash loader_a loader.elf\ntool flash boot_a boot.img\n")
    apply_skips(rows, ["loader_a"], [])
    agent = AgentView(caps=FULL_AGENT.caps, partitions=["boot_a"], ops=FULL_AGENT.ops)
    assert check_agent(rows, agent) == []


def test_an_oem_command_off_the_agent_list_is_refused_with_its_line():
    """The agent runs only the oem commands on its own list and reports that
    list in its table. A row it would refuse after the flashes is refused
    before row 1."""
    rows = _running("tool flash boot_a boot.img\ntool oem at-disable-unlock-vboot\n")
    problems = check_agent(rows, FULL_AGENT)
    assert len(problems) == 1
    assert problems[0].startswith("line 2: the agent would refuse oem at-disable-unlock-vboot")
    assert check_agent(_running("tool oem at-lock-vboot\n"), FULL_AGENT) == []


def test_an_agent_without_getvar_or_oem_is_refused_before_the_first_row():
    ops = {k: v for k, v in (FULL_AGENT.ops or {}).items() if k not in ("getvar", "oem")}
    agent = AgentView(caps=FULL_AGENT.caps, partitions=[], ops=ops)
    rows = _running("tool getvar check-firmware-board-x\ntool oem at-lock-vboot\n")
    joined = "\n".join(check_agent(rows, agent))
    assert "too old to getvar (line 1)" in joined
    assert "too old to oem (line 2)" in joined


def test_an_agent_too_old_to_erase_is_refused_before_the_first_row():
    rows = _running("tool flash boot_a boot.img\ntool erase misc\n")
    # An agent from before the op table: its table is inferred from caps and
    # has no erase.
    problems = check_agent(rows, AgentView(caps=["adb", "fastboot"], partitions=[]))
    assert len(problems) == 1 and "too old to erase (line 2)" in problems[0]


def test_an_agent_with_the_old_size_ceiling_is_refused_for_a_large_image():
    rows = _running("tool flash super super.img\n")
    rows[0].size = LEGACY_FLASH_MAX_BYTES + 1
    old = AgentView(caps=["adb", "fastboot"], partitions=[])  # pre-table: 2 GiB ceiling
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


async def test_an_image_symlinked_in_from_outside_the_workspace_is_used(tmp_path):
    """Multi-GB images live in a firmware archive; a package directory of
    links to them must plan without a copy. The link's own place decides
    "inside", and the digest is the target's bytes."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "boot.img").write_bytes(b"A" * 64)
    manifest = _package(workspace, "tool flash boot_a linked.img\n")
    (manifest.parent / "linked.img").symlink_to(archive / "boot.img")
    plan = await _plan(workspace, manifest, skip_actions=())
    assert plan.ok, plan.problems
    row = plan.rows[0]
    assert row.path == manifest.parent / "linked.img", "the link, as written"
    assert (row.size, row.sha256) == (64, hashlib.sha256(b"A" * 64).hexdigest())


async def test_a_dotdot_through_a_symlinked_directory_cannot_climb_out(tmp_path):
    """`lnk/../x` is folded on the written path: it names x next to the
    manifest, never x beside the link's target — and that folded path is
    the one opened, so the OS cannot walk out through the link either."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (tmp_path / "archive" / "deep").mkdir(parents=True)
    (tmp_path / "archive" / "secret.img").write_bytes(b"s")
    manifest = _package(workspace, "tool flash boot_a lnk/../secret.img\n")
    (manifest.parent / "lnk").symlink_to(tmp_path / "archive" / "deep")
    plan = await _plan(workspace, manifest, skip_actions=())
    assert plan.rows[0].path is None
    assert len(plan.problems) == 1 and "not found next to the manifest" in plan.problems[0]


async def test_a_symlink_with_a_missing_target_says_so(tmp_path):
    manifest = _package(tmp_path, "tool flash boot_a gone.img\n")
    (manifest.parent / "gone.img").symlink_to(tmp_path / "nowhere.img")
    plan = await _plan(tmp_path, manifest, skip_actions=())
    assert len(plan.problems) == 1
    assert "a symlink whose target is missing" in plan.problems[0]


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


def test_fingerprint_covers_the_oem_command():
    a, _ = parse_manifest("tool oem at-lock-vboot\n")
    b, _ = parse_manifest("tool oem at-unlock-vboot\n")
    assert fingerprint("m", a) != fingerprint("m", b)


# A vendor package's manifest in the shape real ones have: a title comment,
# a board check first, an oem lock and an erase after the flashes.
VENDOR_MANIFEST = """\
# board-x flashing commands
vtool getvar check-firmware-board-x
vtool flash boot_a        boot.img
vtool flash boot_b        boot.img
vtool reboot-bootloader
vtool flash super         super.img
vtool oem at-lock-vboot
vtool erase frp
vtool reboot
"""


async def test_a_vendor_manifest_with_comment_check_and_oem_plans_whole(tmp_path):
    manifest = _package(tmp_path, VENDOR_MANIFEST)
    plan = await _plan(tmp_path, manifest, skip_actions=())
    assert plan.problems == []
    assert [r.describe() for r in plan.rows] == [
        "getvar check-firmware-board-x",
        "flash boot_a <- boot.img",
        "flash boot_b <- boot.img",
        "reboot-bootloader",
        "flash super <- super.img",
        "oem at-lock-vboot",
        "erase frp",
        "reboot",
    ]
    assert [r.line for r in plan.rows] == [2, 3, 4, 5, 6, 7, 8, 9], "lines as in the file"


def test_fingerprint_covers_the_decision_not_just_the_rows():
    rows, _ = parse_manifest("tool flash boot_a boot.img\n")
    run = fingerprint("m", rows)
    rows[0].skip = "--skip boot_a"
    assert fingerprint("m", rows) != run
