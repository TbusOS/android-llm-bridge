"""Flash plans: run a line-based flashing manifest as ONE checked unit.

A manifest is the per-build list a vendor flashing tool walks line by line:

    # comment
    <tool> <action> <args...>
    tool getvar check-firmware-board-x
    tool flash boot_a boot.img
    tool erase misc
    tool oem at-lock-vboot
    tool reboot-bootloader

The first column names the vendor's own tool and is ignored. Running the
same list from here, with named exclusions, is the point: flashing partitions
one by one by hand eventually misses one; a generated list with explicit
skips does not.

Two properties shape this module:

* **Checked as a whole before anything runs.** An unknown action, a missing
  image, a malformed partition name, a skip that matches nothing, a partition
  the agent will refuse, an op the agent is too old for — each refuses the
  WHOLE plan. A plan that stops at row 17 for a reason that was knowable at
  row 0 leaves a half-written board, the one outcome a plan exists to prevent.
* **What runs is what was confirmed.** The plan carries a fingerprint over
  every row's decision and every image's digest. The run endpoint rebuilds
  the plan and refuses on mismatch, so an image rebuilt between "looks
  right" and "go" is caught before the first row. (The agent also re-checks
  each image's digest before writing it — ADR-056 §决定 6.)

Parsing is pure; resolving files and hashing them touches the disk and runs
off the event loop.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from alb.remote.bench_ops import PARTITION_RE, OpSpec, check_params, legacy_ops

# action -> number of arguments after it.
ACTIONS: dict[str, int] = {
    "flash": 2,  # flash <partition> <file>
    "erase": 1,  # erase <partition>
    "setvar": 2,  # setvar <key> <value>
    "getvar": 1,  # getvar <name> — a check: the device answering FAIL stops the plan
    "oem": 1,  # oem <command> — the agent runs only the commands on its own list
    "reboot-bootloader": 0,
    "reboot": 0,
}

# Actions that carry no partition, so `--skip <partition>` cannot reach them.
# flash / erase are deliberately NOT here: skipping "every flash row" by
# action is never what anyone means, and a partition skip says exactly which.
SKIPPABLE_ACTIONS = ("setvar", "getvar", "oem", "reboot-bootloader", "reboot")

# Actions no agent can execute. AOSP fastboot has no `setvar` command (its
# command dispatch rejects it as unknown), so a row asking for one can only be
# skipped — and the skip has to be explicit, because a vendor tool may use it
# for a check the operator should consciously decide to drop.
UNRUNNABLE = {
    "setvar": "fastboot on the agent host has no setvar command",
}

# Same shapes the agent enforces (alb_agent._PARTITION_RE / _GETVAR_RE).
# Checked here too so a bad name refuses the whole plan up front instead of
# being refused by the agent halfway through it.
# PARTITION_RE is imported from bench_ops — one copy for the hub side.
VAR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}\Z")
OEM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")

# Sanity ceilings: a manifest is a few dozen short lines. Anything far
# larger is the wrong file, and reading it whole would be the first mistake.
MAX_MANIFEST_BYTES = 256 * 1024
MAX_ROWS = 512

_HASH_CHUNK = 1024 * 1024


def _show(text: str) -> str:
    """A manifest is user-supplied bytes; quote and bound anything echoed
    back from it so a control character cannot reshape the report (L-051)."""
    return repr(text[:80])


@dataclass
class PlanRow:
    line: int
    action: str
    partition: str = ""
    file: str = ""  # as written in the manifest
    key: str = ""  # setvar / getvar variable name
    value: str = ""
    command: str = ""  # oem
    path: Path | None = None  # resolved image, flash rows that run
    size: int = 0
    md5: str = ""
    sha256: str = ""
    skip: str = ""  # "" = runs; otherwise why it is skipped
    # Filled in while the plan runs.
    status: str = "pending"  # pending / ok / failed / skipped / not-run
    duration_s: float = 0.0
    code: str = ""
    error: str = ""

    @property
    def runs(self) -> bool:
        return not self.skip

    def describe(self) -> str:
        if self.action == "flash":
            return f"flash {self.partition} <- {self.file}"
        if self.action == "erase":
            return f"erase {self.partition}"
        if self.action == "setvar":
            return f"setvar {self.key} {self.value}"
        if self.action == "getvar":
            return f"getvar {self.key}"
        if self.action == "oem":
            return f"oem {self.command}"
        return self.action

    def as_dict(self) -> dict[str, Any]:
        return {
            "line": self.line,
            "action": self.action,
            "partition": self.partition,
            "file": self.file,
            "key": self.key,
            "value": self.value,
            "command": self.command,
            "size": self.size,
            "md5": self.md5,
            "sha256": self.sha256,
            "skip": self.skip,
            "status": self.status,
            "duration_s": round(self.duration_s, 3),
            "code": self.code,
            "error": self.error,
        }


@dataclass(frozen=True)
class AgentView:
    """What the connected agent said about itself at hello time."""

    caps: list[str]
    partitions: list[str]  # empty = no allowlist = any well-formed name
    # The op table it reported (ADR-059). None = an agent from before the
    # table; its ops are then inferred from caps (bench_ops.legacy_ops).
    ops: dict[str, OpSpec] | None = None

    def table(self) -> dict[str, OpSpec]:
        return self.ops if self.ops is not None else legacy_ops(self.caps)


@dataclass
class Plan:
    manifest: str  # as the caller named it (workspace-relative)
    rows: list[PlanRow]
    problems: list[str]
    fingerprint: str = ""

    @property
    def ok(self) -> bool:
        return not self.problems

    def as_dict(self) -> dict[str, Any]:
        return {
            "manifest": self.manifest,
            "fingerprint": self.fingerprint,
            "problems": list(self.problems),
            "total": len(self.rows),
            "run": sum(1 for r in self.rows if r.runs),
            "skipped": sum(1 for r in self.rows if not r.runs),
            "rows": [r.as_dict() for r in self.rows],
        }


@dataclass
class PlanResult:
    """Verdict of a plan run. `rows` carries every row's own outcome, so the
    summary can say exactly which rows ran, which were skipped and which
    never got their turn — "it failed" alone does not say how far it got."""

    ok: bool
    manifest: str = ""
    rows: list[PlanRow] = field(default_factory=list)
    code: str = ""
    error: str = ""
    failed_line: int = 0
    # Output of the failing row's fastboot — the evidence for `error`.
    stdout: str = ""
    stderr: str = ""
    duration_s: float = 0.0
    artifacts: str = ""

    def as_dict(self) -> dict[str, Any]:
        def count(status: str) -> int:
            return sum(1 for r in self.rows if r.status == status)

        return {
            "ok": self.ok,
            "manifest": self.manifest,
            "code": self.code,
            "error": self.error,
            "failed_line": self.failed_line,
            "total": len(self.rows),
            "ran": count("ok") + count("failed"),
            "skipped": count("skipped"),
            "not_run": count("not-run"),
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_s": round(self.duration_s, 3),
            "artifacts": self.artifacts,
            "rows": [r.as_dict() for r in self.rows],
        }


# ── parsing (pure) ──────────────────────────────────────────────────────


def parse_manifest(text: str) -> tuple[list[PlanRow], list[str]]:
    """Rows plus every problem found — all of them, not just the first, so
    one dry run is enough to fix a manifest.

    Whitespace between fields may be any run of spaces or tabs (generated
    manifests pad columns); blank lines and lines starting with ``#`` are
    ignored; CRLF and a leading BOM are tolerated. Anything else that does
    not parse is a problem."""
    rows: list[PlanRow] = []
    problems: list[str] = []
    for no, raw in enumerate(text.lstrip("﻿").splitlines(), start=1):
        tokens = raw.split()
        if not tokens or tokens[0].startswith("#"):
            continue
        if len(rows) >= MAX_ROWS:
            problems.append(f"more than {MAX_ROWS} rows — this does not look like a manifest")
            break
        if len(tokens) < 2:
            problems.append(f"line {no}: expected '<tool> <action> [args]', got {_show(raw)}")
            continue
        action, args = tokens[1], tokens[2:]
        if action not in ACTIONS:
            known = ", ".join(ACTIONS)
            problems.append(f"line {no}: unknown action {_show(action)} (known: {known})")
            continue
        if len(args) != ACTIONS[action]:
            problems.append(
                f"line {no}: {action} takes {ACTIONS[action]} argument(s), got {len(args)}"
            )
            continue
        row = PlanRow(line=no, action=action)
        if action in ("flash", "erase"):
            row.partition = args[0]
            if not PARTITION_RE.match(row.partition):
                problems.append(f"line {no}: partition name {_show(row.partition)} is malformed")
        if action == "flash":
            row.file = args[1]
        if action == "setvar":
            row.key, row.value = args
            for what, value in (("key", row.key), ("value", row.value)):
                if not VAR_RE.match(value):
                    problems.append(f"line {no}: setvar {what} {_show(value)} is malformed")
        if action == "getvar":
            row.key = args[0]
            if not VAR_RE.match(row.key):
                problems.append(f"line {no}: getvar name {_show(row.key)} is malformed")
        if action == "oem":
            row.command = args[0]
            if not OEM_RE.match(row.command):
                problems.append(f"line {no}: oem command {_show(row.command)} is malformed")
        rows.append(row)
    if not rows and not problems:
        problems.append("the manifest has no rows")
    return rows, problems


def _split(values: list[str]) -> list[str]:
    """`--skip a,b --skip c` → [a, b, c], order kept, duplicates dropped."""
    out: list[str] = []
    for item in values:
        for part in item.split(","):
            part = part.strip()
            if part and part not in out:
                out.append(part)
    return out


def apply_skips(rows: list[PlanRow], skip: list[str], skip_actions: list[str]) -> list[str]:
    """Mark skipped rows; return problems.

    A skip that matches no row is a problem, not a no-op. The skip is how
    the caller protects something — a debug bootloader, say — and a typo
    (`abl` for `abl_a`) would otherwise flash exactly what it was meant to
    protect, silently."""
    problems: list[str] = []
    partitions = sorted({r.partition for r in rows if r.partition})
    for name in _split(skip):
        if not PARTITION_RE.match(name):
            problems.append(f"--skip {_show(name)} is not a well-formed partition name")
            continue
        hits = [r for r in rows if r.partition == name]
        if not hits:
            shown = ", ".join(partitions[:40]) + (" ..." if len(partitions) > 40 else "")
            problems.append(f"--skip {name} matches no row (partitions in this manifest: {shown})")
        for r in hits:
            r.skip = f"--skip {name}"
    for action in _split(skip_actions):
        if action not in SKIPPABLE_ACTIONS:
            problems.append(
                f"--skip-action {_show(action)}: only {', '.join(SKIPPABLE_ACTIONS)} can be "
                "skipped by action — skip flash / erase rows by partition with --skip"
            )
            continue
        hits = [r for r in rows if r.action == action]
        if not hits:
            problems.append(f"--skip-action {action} matches no row")
        for r in hits:
            r.skip = r.skip or f"--skip-action {action}"
    for r in rows:
        if r.runs and r.action in UNRUNNABLE:
            problems.append(
                f"line {r.line}: {r.action} cannot run — {UNRUNNABLE[r.action]}; "
                f"skip it explicitly with --skip-action {r.action}"
            )
    return problems


def check_agent(rows: list[PlanRow], agent: AgentView | None) -> list[str]:
    """Problems the agent would raise halfway through, raised now instead.

    The agent remains the authority — it re-checks every row. This exists
    because its refusal arrives at the row it refuses, after every earlier
    row has already been written."""
    running = [r for r in rows if r.runs]
    if agent is None:
        return ["no agent is connected — nothing can run, and its allowlist cannot be checked"]
    ops = agent.table()
    if "flash" not in ops:
        return ["the connected agent does not advertise the fastboot capability"]
    problems: list[str] = []
    # Every action a row needs must be in the agent's op table. Reported per
    # action with its lines, because "update the agent" is the fix and the
    # caller needs to know which rows forced it.
    needed = {
        "flash": "flash",
        "erase": "erase",
        "getvar": "getvar",
        "oem": "oem",
        "reboot": "reboot",
        "reboot-bootloader": "reboot",
    }
    for action, op in needed.items():
        lines = [r.line for r in running if r.action == action]
        if lines and op not in ops:
            problems.append(
                f"the agent is too old to {action} (line {', '.join(map(str, lines))}) — "
                "update alb_agent.py on the agent host"
            )
    # The agent's own list of oem commands (and getvar's name shape), read
    # from its table: a row it would refuse is refused here, before row 1.
    for r in running:
        if r.action not in ("getvar", "oem") or r.action not in ops:
            continue
        params = {"name": r.key} if r.action == "getvar" else {"command": r.command}
        _, _, problem = check_params(ops[r.action], params, [])
        if problem:
            problems.append(f"line {r.line}: the agent would refuse {r.describe()}: {problem}")
    ceiling = ops["flash"].max_image_bytes
    large = [r for r in running if r.action == "flash" and ceiling and r.size > ceiling]
    if large:
        shown = ", ".join(f"line {r.line} {r.file} ({r.size} bytes)" for r in large)
        problems.append(
            f"the agent refuses images over {ceiling} bytes ({shown}) — "
            "update alb_agent.py on the agent host"
        )
    if agent.partitions:
        allowed = set(agent.partitions)
        refused = [r for r in running if r.partition and r.partition not in allowed]
        if refused:
            names = sorted({r.partition for r in refused})
            where = ", ".join(f"{r.partition} (line {r.line})" for r in refused)
            problems.append(
                f"not on the agent's partition allowlist: {where} — add "
                f"{','.join(names)} to flash_partitions in agent.conf, or skip them"
            )
    return problems


def fingerprint(manifest: str, rows: list[PlanRow]) -> str:
    """Digest of everything a confirmation is a confirmation OF: each row,
    its decision, and the bytes of each image that runs."""
    body = [
        [r.line, r.action, r.partition, r.file, r.key, r.value, r.command, r.size, r.sha256, r.skip]
        for r in rows
    ]
    blob = json.dumps({"manifest": manifest, "rows": body}, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()


# ── disk side ───────────────────────────────────────────────────────────


def _digests(path: Path) -> tuple[int, str, str]:
    """(size, md5, sha256) in one pass. sha256 is what the agent checks;
    md5 is what people compare against a vendor tool's listing."""
    md5 = hashlib.md5(usedforsecurity=False)
    sha = hashlib.sha256()
    size = 0
    with open(path, "rb") as fh:
        while chunk := fh.read(_HASH_CHUNK):
            size += len(chunk)
            md5.update(chunk)
            sha.update(chunk)
    return size, md5.hexdigest(), sha.hexdigest()


def _locate(row: PlanRow, base: Path, root: Path) -> tuple[Path | None, str]:
    """Image path for a flash row, or a problem. Names are relative to the
    manifest's directory and must stay inside the workspace."""
    name = row.file.replace("\\", "/")  # a Windows-generated manifest
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        return None, f"line {row.line}: file {_show(row.file)} must be relative to the manifest"
    candidate = (base / name).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None, f"line {row.line}: file {_show(row.file)} is outside the workspace"
    if not candidate.is_file():
        return None, f"line {row.line}: file {_show(row.file)} not found next to the manifest"
    return candidate, ""


def _resolve_files(rows: list[PlanRow], base: Path, root: Path) -> list[str]:
    """Locate and hash every image that will be written. Skipped rows are
    not required to have their file: a package stripped of the very image
    being protected is still a valid package to run with that skip."""
    problems: list[str] = []
    cache: dict[Path, tuple[int, str, str]] = {}
    for row in rows:
        if row.action != "flash" or not row.runs:
            continue
        path, problem = _locate(row, base, root)
        if path is None:
            problems.append(problem)
            continue
        if path not in cache:
            try:
                cache[path] = _digests(path)
            except OSError as e:
                problems.append(f"line {row.line}: cannot read {_show(row.file)}: {e}")
                continue
        row.path = path
        row.size, row.md5, row.sha256 = cache[path]
        if row.size == 0:
            problems.append(f"line {row.line}: file {_show(row.file)} is empty")
    return problems


async def build_plan(
    manifest: Path,
    *,
    name: str,
    root: Path,
    skip: list[str],
    skip_actions: list[str],
    agent: AgentView | None,
) -> Plan:
    """Parse, resolve, hash and check — the whole plan or its problems.

    `manifest` is the resolved file (the caller has already confined it to
    the workspace); `name` is how the caller referred to it; `root` is the
    resolved workspace root that image paths must stay inside."""

    def _work() -> Plan:
        try:
            data = manifest.read_bytes()[: MAX_MANIFEST_BYTES + 1]
        except OSError as e:
            return Plan(name, [], [f"cannot read the manifest: {e}"])
        if len(data) > MAX_MANIFEST_BYTES:
            return Plan(name, [], [f"the manifest is over {MAX_MANIFEST_BYTES} bytes"])
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return Plan(name, [], ["the manifest is not UTF-8 text"])
        rows, problems = parse_manifest(text)
        problems += apply_skips(rows, skip, skip_actions)
        problems += _resolve_files(rows, manifest.parent, root)
        problems += check_agent(rows, agent)
        return Plan(name, rows, problems, fingerprint(name, rows))

    return await asyncio.to_thread(_work)
