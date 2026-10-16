"""Push a new copy of the bench agent and confirm it came back (ADR-060).

The bundle is this hub's own `clients/windows-agent/` folder — the agent
mirrors wire constants of `alb.remote.protocol`, so the copy that matches the
running hub is the right one to send. It travels as an image op
(`agent-update`) over the same job channel as a flash: size + sha256 checked
on arrival, nothing named by path.

The agent unpacks it, runs the new build's self-check, and hands over to an
update helper that stops the agent, swaps the files and starts the new build.
The helper puts the old files back when the new build does not reach the hub
in time. This module then waits for the agent to reconnect and reads which
build came back:

    new build in hello                  -> updated
    hello.update says rolled_back       -> AGENT_UPDATE_ROLLED_BACK
    nobody came back in time            -> AGENT_UPDATE_NO_RETURN

The same wait runs when the job channel closed before the agent answered:
the update may have gone ahead regardless, so the reconnect decides.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

from alb.remote.flash import EventSink, FlashEvent, FlashResult, FlashService

# MUST stay in lockstep with the agent's _BUNDLE_* rules: a file this side
# packs and that side refuses fails the whole update.
BUNDLE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
BUNDLE_SUFFIXES = (".py", ".bat", ".md", ".txt", ".example")
BUNDLE_CONFIG = "agent.conf"
BUNDLE_MAIN = "alb_agent.py"
OP = "agent-update"
# The helper waits up to 60 s for the new build, then rolls back and waits
# for the old one; transfer and self-check come before that.
RECONNECT_TIMEOUT_S = 240.0
_POLL_S = 0.5
# A fixed timestamp keeps the bundle byte-identical for identical files.
_ZIP_DATE = (1980, 1, 1, 0, 0, 0)


def bundle_dir() -> Path:
    """Where the agent files come from: ALB_AGENT_BUNDLE_DIR, else this
    checkout's clients/windows-agent."""
    override = os.environ.get("ALB_AGENT_BUNDLE_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parents[3] / "clients" / "windows-agent"


def member_ok(name: str) -> bool:
    if not BUNDLE_NAME_RE.match(name):
        return False
    return name == BUNDLE_CONFIG or name.endswith(BUNDLE_SUFFIXES)


def build_of(data: bytes) -> str:
    """Same digest the agent computes over its own file (`_file_build`)."""
    return hashlib.sha256(data).hexdigest()[:12]


def bundle_files(src: Path) -> list[Path]:
    """The files that make up the agent. agent.conf is never taken from the
    folder: a real config is sent only when the caller passes one."""
    return sorted(
        p for p in src.iterdir() if p.is_file() and p.name != BUNDLE_CONFIG and member_ok(p.name)
    )


def local_build(src: Path | None = None) -> str:
    """Build of the agent file this hub would send ("" when there is none)."""
    try:
        return build_of(((src or bundle_dir()) / BUNDLE_MAIN).read_bytes())
    except OSError:
        return ""


def make_bundle(src: Path, config_text: str | None, dest: Path) -> tuple[str, list[str]]:
    """Write the bundle zip to `dest`; return (build, member names)."""
    files = bundle_files(src)
    names = [p.name for p in files]
    if BUNDLE_MAIN not in names:
        raise ValueError(f"no {BUNDLE_MAIN} in {src}")
    build = ""
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            data = p.read_bytes()
            if p.name == BUNDLE_MAIN:
                build = build_of(data)
            zf.writestr(zipfile.ZipInfo(p.name, date_time=_ZIP_DATE), data, zipfile.ZIP_DEFLATED)
        if config_text is not None:
            zf.writestr(
                zipfile.ZipInfo(BUNDLE_CONFIG, date_time=_ZIP_DATE),
                config_text.encode("utf-8"),
                zipfile.ZIP_DEFLATED,
            )
            names.append(BUNDLE_CONFIG)
    return build, names


def _say(sink: EventSink | None, phase: str, text: str) -> None:
    if sink is not None:
        sink(FlashEvent(phase=phase, text=text))


def _fail(code: str, error: str, data: dict[str, Any] | None = None) -> FlashResult:
    return FlashResult(ok=False, code=code, error=error, data=dict(data or {}))


async def update_agent(
    svc: FlashService,
    registry: Any,
    *,
    config_text: str | None = None,
    src: Path | None = None,
    on_event: EventSink | None = None,
    reconnect_timeout_s: float = RECONNECT_TIMEOUT_S,
) -> FlashResult:
    """Send the bundle to the current agent, then wait for it to come back."""
    started = time.monotonic()
    old = registry.current_agent()
    if old is None:
        return _fail("FASTBOOT_UNAVAILABLE", "no agent connected")
    src = src or bundle_dir()
    if OP not in getattr(old, "op_table", {}):
        return _fail(
            "BENCH_AGENT_TOO_OLD",
            f"agent {old.name} predates remote update — copy {src} to the bench host by "
            "hand once and restart the agent there; later updates go through this command",
        )

    with tempfile.TemporaryDirectory(prefix="alb-agent-bundle-") as tmp:
        zpath = Path(tmp) / "agent-bundle.zip"
        try:
            to_build, names = await asyncio.to_thread(make_bundle, src, config_text, zpath)
        except (OSError, ValueError) as e:
            return _fail("AGENT_UPDATE_REJECTED", f"cannot build the bundle from {src}: {e}")
        from_build = getattr(old, "build", "") or "?"
        _say(on_event, "bundle", f"{len(names)} files from {src}, build {from_build} -> {to_build}")
        result = await svc.run_op(
            OP, {}, image=zpath, label=f"{OP} {from_build} -> {to_build}", on_event=on_event
        )
    lost = not result.ok and bool((result.data or {}).get("result_lost"))
    if not result.ok and not lost:
        return result
    if lost:
        # The agent may have started the update and then stopped before its
        # answer got out (2026-10-16 on the bench: a text selection in its
        # console window froze it until the helper killed it). Which build
        # comes back tells; the update id was lost with the answer.
        data: dict[str, Any] = {"to_build": to_build, "result_lost": True}
        _say(on_event, "restart", "the agent's answer was lost; waiting to see which build returns")
    else:
        data = dict(result.data or {})
        if not data.get("restarting"):
            return result  # the bench already has exactly these files
        _say(
            on_event,
            "restart",
            "the agent is restarting on the new files; waiting for it to reconnect",
        )

    update_id = str(data.get("update_id") or "")
    expect = str(data.get("to_build") or to_build)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + reconnect_timeout_s
    while loop.time() < deadline:
        conn = registry.get(old.agent_id)
        if conn is not None and conn is not old:
            last = dict(getattr(conn, "last_update", {}) or {})
            if update_id:
                same_update = last.get("id") == update_id
            else:
                same_update = last.get("from_build") == from_build and (
                    last.get("to_build") == expect
                )
            if same_update and last.get("outcome") in ("rolled_back", "aborted"):
                return _fail(
                    "AGENT_UPDATE_ROLLED_BACK",
                    f"build {expect} did not reach the hub; the bench host put build "
                    f"{conn.build or '?'} back",
                    {**data, "build": conn.build, "outcome": last.get("outcome")},
                )
            # A rolled-back agent reports its outcome in hello, so any other
            # return on the expected build is the new one.
            if conn.build == expect:
                return FlashResult(
                    ok=True,
                    rc=0,
                    stdout=f"agent {conn.name} is back on build {expect}",
                    duration_s=time.monotonic() - started,
                    data={**data, "build": conn.build, "outcome": "updated"},
                )
        await asyncio.sleep(_POLL_S)
    if lost and registry.get(old.agent_id) is old:
        return _fail(
            "AGENT_UPDATE_NO_RETURN",
            f"the agent ended the update job without an answer and is still on build "
            f"{from_build} after {reconnect_timeout_s:.0f}s — `alb bench log update` shows "
            "whether the update helper ran",
            data,
        )
    return _fail(
        "AGENT_UPDATE_NO_RETURN",
        f"the agent restarted for build {expect} and has not reconnected within "
        f"{reconnect_timeout_s:.0f}s",
        data,
    )
