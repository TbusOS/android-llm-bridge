"""The bench agent's op table, as the hub sees it (ADR-059).

The agent on the bench host owns a table of operations it can run there —
each with typed parameters, an argv template it fills in itself, and flags
for "needs a board in fastboot", "changes the device", "needs the job lock"
and "what to record". It reports the table in its hello. Everything on this
side — the generic `/api/bench/ops` route, `alb bench`, the web and the
flash-plan pre-check — reads that one table instead of hard-coding a list of
verbs, so an op that only uses existing parameter kinds needs no hub change.

What this module is NOT: an authority. The agent re-validates every value
and builds every argv (ADR-056 §决定 5). The checks here exist so a typo is
reported before a channel is opened, and so a UI can offer a dropdown
instead of a text box. Where this side cannot judge — an unknown kind, a
pattern that does not compile — it lets the value through and the agent
decides.

An agent from before the table (no `ops` in its hello) gets a conservative
synthesised table from its caps: only the five ops every such agent has,
each treated as one that changes the device.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from alb.remote.protocol import CAP_FASTBOOT, LEGACY_FLASH_MAX_BYTES

# Same shape rule the agent applies (`_PARTITION_RE` in alb_agent.py). The
# leading-character class keeps a name from starting with `-`, which fastboot
# would parse as a flag.
PARTITION_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}\Z")
# An op name ends up in a URL path, a timeline directory name and a log line.
OP_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")

KINDS = ("partition", "enum", "int", "text")
RECORD_TIERS = ("none", "events", "uart")


@dataclass(frozen=True)
class ParamSpec:
    name: str
    kind: str
    required: bool = True
    default: Any = None
    choices: tuple[str, ...] = ()
    min: int | None = None
    max: int | None = None
    pattern: str = ""
    max_len: int = 0

    @classmethod
    def from_wire(cls, raw: Any) -> ParamSpec | None:
        if not isinstance(raw, dict):
            return None
        name = raw.get("name")
        if not isinstance(name, str) or not OP_NAME_RE.match(name):
            return None
        return cls(
            name=name,
            kind=str(raw.get("kind") or "text"),
            required=bool(raw.get("required", True)),
            default=raw.get("default"),
            choices=tuple(str(c) for c in raw.get("choices") or [] if isinstance(c, str)),
            min=_strict_int(raw.get("min")),
            max=_strict_int(raw.get("max")),
            pattern=str(raw.get("pattern") or ""),
            max_len=_strict_int(raw.get("max_len")) or 0,
        )

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "kind": self.kind,
            "required": self.required,
            "default": self.default,
        }
        if self.kind == "enum":
            out["choices"] = list(self.choices)
        if self.kind == "int":
            out["min"], out["max"] = self.min, self.max
        if self.kind == "text":
            out["pattern"], out["max_len"] = self.pattern, self.max_len
        return out

    def check(self, value: Any, allowlist: list[str]) -> tuple[Any, str]:
        """(normalised value, problem). `problem` is "" when the value passes.

        A missing optional value becomes the default. Integers may arrive as
        decimal strings (`alb bench run log-tail lines=50` has nothing else to
        send); they are normalised so the agent receives a real int."""
        if value is None or value == "":
            if self.required:
                return None, f"{self.name} is required"
            return self.default, ""
        if self.kind == "partition":
            if not isinstance(value, str) or not PARTITION_RE.match(value):
                return None, f"{self.name} {value!r} is not a well-formed partition name"
            if allowlist and value not in allowlist:
                return None, (
                    f"partition {value!r} is not on the agent's allowlist ({', '.join(allowlist)})"
                )
            return value, ""
        if self.kind == "enum":
            if not isinstance(value, str) or value not in self.choices:
                return None, f"{self.name} must be one of {', '.join(self.choices)} — got {value!r}"
            return value, ""
        if self.kind == "int":
            number = _strict_int(value)
            if number is None:
                return None, f"{self.name} must be an integer — got {value!r}"
            if (self.min is not None and number < self.min) or (
                self.max is not None and number > self.max
            ):
                return None, f"{self.name} must be within {self.min}..{self.max} — got {number}"
            return number, ""
        if not isinstance(value, str):
            return None, f"{self.name} must be text — got {value!r}"
        if self.kind == "text":
            if self.max_len and len(value) > self.max_len:
                return None, f"{self.name} is longer than {self.max_len} characters"
            try:
                if self.pattern and not re.fullmatch(self.pattern, value):
                    return None, f"{self.name} {value!r} does not have the allowed shape"
            except re.error:
                pass  # the agent's regex dialect is the authority; let it judge
        # An unknown kind (a newer agent): pass the string through untouched.
        return value, ""


@dataclass(frozen=True)
class OpSpec:
    name: str
    group: str = "fastboot"
    summary: str = ""
    params: tuple[ParamSpec, ...] = ()
    needs: str = ""
    image: bool = False
    max_image_bytes: int = 0  # image ops: 0 = bounded only by the agent's temp space
    mutates: bool = True
    exclusive: bool = True
    record: str = "events"
    timeout_s: float = 0.0

    @classmethod
    def from_wire(cls, raw: Any) -> OpSpec | None:
        """Tolerant: a malformed entry is dropped, not fatal — one bad row
        from a newer agent must not take the whole bench offline."""
        if not isinstance(raw, dict):
            return None
        name = raw.get("name")
        if not isinstance(name, str) or not OP_NAME_RE.match(name):
            return None
        params = tuple(p for p in (ParamSpec.from_wire(x) for x in raw.get("params") or []) if p)
        record = str(raw.get("record") or "events")
        try:
            timeout_s = float(raw.get("timeout_s") or 0.0)
        except (TypeError, ValueError):
            return None  # an unreadable timeout drops the row, not the agent's session
        if not math.isfinite(timeout_s) or timeout_s < 0:
            return None
        return cls(
            name=name,
            group=str(raw.get("group") or ""),
            summary=str(raw.get("summary") or ""),
            params=params,
            needs=str(raw.get("needs") or ""),
            image=bool(raw.get("image")),
            max_image_bytes=_strict_int(raw.get("max_image_bytes")) or 0,
            # Missing = unknown = assume it changes the device: a needless
            # confirmation costs a keypress, a missing one can cost a board.
            mutates=bool(raw.get("mutates", True)),
            exclusive=bool(raw.get("exclusive", True)),
            record=record if record in RECORD_TIERS else "events",
            timeout_s=timeout_s,
        )

    def param(self, name: str) -> ParamSpec | None:
        return next((p for p in self.params if p.name == name), None)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "group": self.group,
            "summary": self.summary,
            "params": [p.as_dict() for p in self.params],
            "needs": self.needs,
            "image": self.image,
            "max_image_bytes": self.max_image_bytes,
            "mutates": self.mutates,
            "exclusive": self.exclusive,
            "record": self.record,
            "timeout_s": self.timeout_s,
        }


def parse_ops(raw: Any) -> dict[str, OpSpec] | None:
    """The table from a hello. None when the hello carried no `ops` at all —
    distinct from an empty table, which is an agent that offers nothing."""
    if raw is None:
        return None
    if not isinstance(raw, list):
        return {}
    table: dict[str, OpSpec] = {}
    for item in raw:
        spec = OpSpec.from_wire(item)
        if spec is not None and spec.name not in table:
            table[spec.name] = spec
    return table


_GETVAR_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}"
_OEM_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}"


def legacy_ops(caps: list[str]) -> dict[str, OpSpec]:
    """What an agent from before the op table can do, inferred from its caps.

    Only the five ops every such agent had. Every row says `mutates` — the
    hub cannot know, and guessing "harmless" is the one wrong answer that
    costs something. The oem allowlist lives in that agent's code and is not
    reported, so `command` is shape-checked only; the agent still refuses
    what is not on its list."""
    if CAP_FASTBOOT not in caps:
        return {}
    dev = "fastboot-device"
    rows = [
        OpSpec("devices", summary="fastboot devices", record="none"),
        OpSpec(
            "getvar",
            summary="fastboot getvar <name>",
            params=(ParamSpec("name", "text", False, "", pattern=_GETVAR_PATTERN, max_len=64),),
            needs=dev,
            record="none",
        ),
        OpSpec(
            "oem",
            summary="fastboot oem <command>",
            params=(ParamSpec("command", "text", pattern=_OEM_PATTERN, max_len=64),),
            needs=dev,
        ),
        OpSpec(
            "reboot",
            summary="fastboot reboot [target]",
            params=(
                ParamSpec("target", "enum", False, "", ("bootloader", "fastboot", "recovery")),
            ),
            needs=dev,
        ),
        OpSpec(
            "flash",
            summary="fastboot flash <partition> <image>",
            params=(ParamSpec("partition", "partition"),),
            needs=dev,
            image=True,
            max_image_bytes=LEGACY_FLASH_MAX_BYTES,
            record="uart",
        ),
    ]
    return {r.name: r for r in rows}


def ops_of(agent: Any) -> dict[str, OpSpec]:
    """The table for a connected agent object (or a test double that only
    has `caps`)."""
    if agent is None:
        return {}
    table = getattr(agent, "op_table", None)
    if isinstance(table, dict):
        return table
    return legacy_ops(list(getattr(agent, "caps", []) or []))


def reject_code(op: str, param: ParamSpec) -> str:
    """The error code for a rejected value. The three pre-table codes are
    kept for the params they always covered, so a caller that branches on
    FLASH_PARTITION_REJECTED keeps working."""
    if param.kind == "partition":
        return "FLASH_PARTITION_REJECTED"
    if op == "oem" and param.name == "command":
        return "FLASH_OEM_REJECTED"
    if op == "getvar" and param.name == "name":
        return "FLASH_VAR_REJECTED"
    return "BENCH_PARAM_REJECTED"


def check_params(
    spec: OpSpec, params: dict[str, Any], allowlist: list[str]
) -> tuple[dict[str, Any], str, str]:
    """(normalised params, error code, problem). An unknown parameter is an
    error rather than ignored: `alb bench run erase partiton=misc` silently
    dropping the misspelt key would leave the op without its argument — or,
    for an optional one, running with the default the caller meant to
    override."""
    known = {p.name for p in spec.params}
    stray = sorted(set(params) - known)
    if stray:
        allowed = ", ".join(sorted(known)) or "none"
        return (
            {},
            "BENCH_PARAM_REJECTED",
            (f"{spec.name} has no parameter {', '.join(stray)} (parameters: {allowed})"),
        )
    out: dict[str, Any] = {}
    for p in spec.params:
        value, problem = p.check(params.get(p.name), allowlist)
        if problem:
            return {}, reject_code(spec.name, p), problem
        if value is not None:
            out[p.name] = value
    return out, "", ""


def _strict_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"-?\d{1,18}", value.strip()):
        return int(value.strip())
    return None
