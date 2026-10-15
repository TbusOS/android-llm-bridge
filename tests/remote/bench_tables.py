"""Op tables for test doubles (ADR-059).

`new_agent_ops()` is the table a current agent reports, written out from the
wire contract. Tests that need "an agent able to run a whole package" use it
instead of cap strings, which is what the hub reads now."""

from __future__ import annotations

from alb.remote.bench_ops import OpSpec, ParamSpec

_DEV = "fastboot-device"
_PARTITION = ParamSpec("partition", "partition")


def new_agent_ops() -> dict[str, OpSpec]:
    rows = [
        OpSpec("devices", mutates=False, record="none"),
        OpSpec(
            "getvar",
            params=(
                ParamSpec(
                    "name",
                    "text",
                    False,
                    "all",
                    pattern=r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}",
                    max_len=64,
                ),
            ),
            needs=_DEV,
            mutates=False,
            record="none",
        ),
        OpSpec(
            "oem",
            params=(ParamSpec("command", "enum", choices=("at-lock-vboot", "at-unlock-vboot")),),
            needs=_DEV,
        ),
        OpSpec(
            "reboot",
            params=(
                ParamSpec("target", "enum", False, "", ("bootloader", "fastboot", "recovery")),
            ),
            needs=_DEV,
        ),
        OpSpec("flash", params=(_PARTITION,), needs=_DEV, image=True, record="uart"),
        OpSpec("erase", params=(_PARTITION,), needs=_DEV),
        OpSpec("set-active", params=(ParamSpec("slot", "enum", choices=("a", "b")),), needs=_DEV),
        OpSpec("boot", needs=_DEV, image=True, record="uart"),
        OpSpec("usb-list", group="host", mutates=False, exclusive=False, record="none"),
        OpSpec(
            "log-tail",
            group="host",
            params=(
                ParamSpec("source", "enum", choices=("agent", "setupapi")),
                ParamSpec("lines", "int", False, 200, min=1, max=2000),
                ParamSpec("grep", "text", False, "", pattern=r"[^\x00-\x1f\x7f]*", max_len=64),
            ),
            mutates=False,
            exclusive=False,
            record="none",
        ),
    ]
    return {r.name: r for r in rows}
