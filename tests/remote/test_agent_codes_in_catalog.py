"""Every error code the bench agent can send is in the hub's catalog.

The agent runs in another process on another machine; the hub turns its
`code` into a sentence and a remedy through infra.errors. A code missing from
the catalog still reaches the caller — with no explanation and no fix. That
happened: FLASH_VAR_REJECTED was sent by the agent for months before it was
catalogued (fixed with ADR-059)."""

from __future__ import annotations

import re
from pathlib import Path

from alb.infra.errors import ERROR_CODES

_AGENT = Path(__file__).resolve().parents[2] / "clients" / "windows-agent" / "alb_agent.py"
# Any quoted literal shaped like one of the agent's code families. Matching
# `code="..."` alone missed codes passed positionally (`_JobRejected("…")`).
_CODE_RE = re.compile(r'"((?:FLASH|FASTBOOT|BENCH|SERIAL|ADB|AGENT)_[A-Z0-9_]+)"')


def _agent_codes() -> set[str]:
    return set(_CODE_RE.findall(_AGENT.read_text(encoding="utf-8")))


def test_the_scan_finds_the_codes_it_is_meant_to_find():
    """Guard the guard: if the pattern stopped matching, the test below
    would pass on an empty set."""
    codes = _agent_codes()
    assert {
        "FLASH_PARTITION_REJECTED",
        "FASTBOOT_NO_DEVICE",
        "BENCH_OP_UNKNOWN",
        "BENCH_LOG_UNAVAILABLE",  # passed positionally — the case the first pattern missed
    } <= codes


def test_every_agent_code_is_catalogued():
    missing = sorted(_agent_codes() - set(ERROR_CODES))
    assert missing == [], f"add these to alb.infra.errors.ERROR_CODES: {missing}"
