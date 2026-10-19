"""adbd capability — root / unroot / remount.

These are not shell commands: `adb root` asks the *daemon* to restart itself
with a different uid, and `adb remount` asks it to remount the system
partitions. Both go through :class:`alb.transport.adb.AdbTransport`'s own
subcommand runner so they reach the same adb server (and the same ``-s``
serial) as every other alb command — ADR-057: the server socket is resolved
once by the transport factory, never re-derived here.

Why a bounded poll after `adb root`: adbd exits and init restarts it, so the
transport is gone for roughly a second (longer through a tunnel). Modern adb
waits for the device inside `adb root`; old builds return at once and the
next `adb shell` may still hit the dying daemon or get "device offline". A
fixed sleep is either too short on a slow link or wastes time on a fast one,
so `id -u` is polled until it reports the wanted uid or the budget runs out.

Why adbd's pid is read too: a wrong uid alone cannot tell "the restart has
not happened yet / never happened" from "adbd restarted and came back with
the same uid". Some builds decide adbd's uid themselves and ignore
`adb unroot`: it restarts the daemon, which comes back as root every time.
Waiting longer, or rebooting, changes nothing there — so a new pid with the
wrong uid ends the poll at once and is reported as what it is.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Literal

from alb.infra.result import Result, fail, ok
from alb.transport.base import ShellResult, Transport

# Verbatim phrases adbd replies with (system/core/adb/daemon/services.cpp).
# Matched case-insensitively on the whole reply; the reply itself is always
# passed through untouched in ``adbd_output`` / the error message.
_REFUSED_PHRASES = ("cannot run as root",)
_ALREADY_ROOT_PHRASES = ("already running as root",)
_ALREADY_NON_ROOT_PHRASES = ("not running as root",)

# `adb remount` wording. The device decides what happened; alb only sorts the
# reply into three buckets the caller can act on. "needs reboot" is checked
# before "failed" because the reboot-required path exits non-zero
# (fs_mgr_remount MUST_REBOOT) while still being the way forward.
_REMOUNT_REBOOT_PHRASES = ("reboot your device", "now reboot")
_REMOUNT_FAILED_PHRASES = (
    "remount failed",
    "remount of the",
    "not running as root",
    "permission denied",
    "read-only file system",
    "must be disabled",
)

Outcome = Literal["became_root", "already_root", "became_non_root", "already_non_root"]
RemountOutcome = Literal["remounted", "needs_reboot"]

SU_HINT = (
    "For single commands prefix `su 0 <cmd>` (toybox su takes the uid first — "
    "`su 0 cat /proc/cmdline`, not `su -c`); for a root adbd flash a userdebug / eng build"
)

UNROOT_NO_EFFECT_HINT = (
    "Nothing to retry: rebooting or waiting will not change it, and commands already run "
    "as root. To run one command as the shell user, prefix `su 2000 <cmd>`"
)

REMOUNT_HINT = (
    "Run `alb adb root` first (remount needs adbd as root). If the output mentions "
    "verity, disable it yourself with `adb disable-verity` and reboot — alb never runs "
    "that for you because it changes the boot verification state"
)


@dataclass(frozen=True)
class AdbdResult:
    """What `adb root` / `adb unroot` did, verified from the device side."""

    outcome: Outcome
    uid: int
    adbd_output: str
    polls: int
    wait_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "uid": self.uid,
            "adbd_output": self.adbd_output,
            "polls": self.polls,
            "wait_ms": self.wait_ms,
        }


@dataclass(frozen=True)
class RemountResult:
    outcome: RemountOutcome
    output: str

    def to_dict(self) -> dict[str, Any]:
        return {"outcome": self.outcome, "output": self.output}


# ─── Locating the adb transport ────────────────────────────────────
_AdbRunner = Callable[..., Awaitable[ShellResult]]


def _find_adb(transport: Transport) -> Transport | None:
    """Return the adb transport underneath `transport`, or None.

    A hybrid transport exposes its members as public attributes; the adb
    member is the only one that can talk to adbd, so it is used even when the
    primary is ssh / serial.
    """
    if transport.name == "adb":
        return transport
    primary = getattr(transport, "primary", None)
    alternates = getattr(transport, "alternates", None) or []
    for t in (primary, *alternates):
        if t is not None and getattr(t, "name", "") == "adb":
            return t  # type: ignore[no-any-return]
    return None


def _adb_runner(adb_t: Transport | None) -> _AdbRunner | None:
    """AdbTransport's raw subcommand runner (`adb <args>`), if there is one.

    The Transport ABC only models `shell`; `root` / `remount` are daemon-level
    verbs with no shell equivalent, so this is the one place a capability
    reaches past the ABC (same precedent as `alb setup wifi` using
    `adb tcpip`).
    """
    run = getattr(adb_t, "_run", None)
    return run if callable(run) else None


def _unsupported(transport: Transport) -> Result[Any]:
    return fail(
        code="TRANSPORT_NOT_SUPPORTED",
        message=f"{transport.name} transport has no adb underneath; adbd commands need adb",
        suggestion="Run with --transport adb, or set up adb: alb setup adb",
        category="transport",
    )


# ─── root / unroot ─────────────────────────────────────────────────
# `timeout` is a poll *budget* (how long to keep asking `id -u`), named like
# power.wait_boot_completed(timeout=) so callers see one convention. ASYNC109
# wants `asyncio.timeout()` instead, but that would cancel the loop and lose
# the attempt count / last uid the error messages are built from.
async def root(
    transport: Transport,
    *,
    timeout: float = 15.0,  # noqa: ASYNC109
    poll_sec: float = 1.0,
) -> Result[AdbdResult]:
    """`adb root`, wait for adbd to come back, verify `id -u` == 0.

    Outcomes: became_root / already_root; a production build's refusal is a
    failure (ADB_ROOT_REFUSED) carrying the device's own words.
    """
    return await _switch_uid(
        transport, verb="root", want_root=True, timeout=timeout, poll_sec=poll_sec
    )


async def unroot(
    transport: Transport,
    *,
    timeout: float = 15.0,  # noqa: ASYNC109
    poll_sec: float = 1.0,
) -> Result[AdbdResult]:
    """`adb unroot`, wait for adbd to come back, verify `id -u` != 0."""
    return await _switch_uid(
        transport, verb="unroot", want_root=False, timeout=timeout, poll_sec=poll_sec
    )


async def _switch_uid(
    transport: Transport,
    *,
    verb: str,
    want_root: bool,
    timeout: float,  # noqa: ASYNC109
    poll_sec: float,
) -> Result[AdbdResult]:
    adb_t = _find_adb(transport)
    run = _adb_runner(adb_t)
    if adb_t is None or run is None:
        return _unsupported(transport)

    perm = await transport.check_permissions(f"adbd.{verb}", {"verb": verb})
    if perm.behavior == "deny":
        return fail(
            code="PERMISSION_DENIED",
            message=perm.reason or f"adb {verb} blocked",
            suggestion=perm.suggestion or "",
            category="permission",
        )

    pid_before = await _adbd_pid(adb_t)
    # Modern adb blocks inside `adb root` until the device reconnects, so the
    # subcommand itself needs more than the poll budget.
    r: ShellResult = await run([verb], timeout=int(timeout) + 30)
    reply = (r.stdout + r.stderr).strip()
    low = reply.lower()

    if want_root and r.error_code != "TIMEOUT_SHELL" and any(p in low for p in _REFUSED_PHRASES):
        # adbd's own refusal, whatever exit status the adb client chose to
        # give it — ahead of the generic failure so the `su 0` hint is not
        # replaced by "check alb devices". adbd did not restart, so there is
        # nothing to wait for.
        return fail(
            code="ADB_ROOT_REFUSED",
            message=reply,
            suggestion=SU_HINT,
            category="device",
            details={"adbd_output": reply},
            timing_ms=r.duration_ms,
        )

    if not r.ok:
        if r.error_code == "TIMEOUT_SHELL":
            return fail(
                code="ADBD_RESTART_TIMEOUT",
                message=f"adb {verb} did not return within {int(timeout) + 30}s: {reply}",
                suggestion="Run `alb devices`; over a tunnel the reconnect can take longer",
                category="timeout",
                timing_ms=r.duration_ms,
            )
        return fail(
            code=r.error_code or "ADB_COMMAND_FAILED",
            message=reply or f"adb {verb} failed",
            suggestion="Check `alb devices` / `alb status`",
            category="transport",
            details={"stdout": r.stdout, "stderr": r.stderr, "exit_code": r.exit_code},
            timing_ms=r.duration_ms,
        )

    already_phrases = _ALREADY_ROOT_PHRASES if want_root else _ALREADY_NON_ROOT_PHRASES
    already = any(p in low for p in already_phrases)

    uid, pid_after, polls, wait_ms = await _poll_uid(
        adb_t, want_root=want_root, timeout=timeout, poll_sec=poll_sec, pid_before=pid_before
    )
    total_ms = r.duration_ms + wait_ms

    if uid is None:
        return fail(
            code="ADBD_RESTART_TIMEOUT",
            message=(
                f"adb {verb} said {reply!r} but the device did not answer `id -u` "
                f"within {timeout:g}s ({polls} attempts)"
            ),
            suggestion=(
                "Run `alb devices`; over a tunnel the reconnect can take longer — "
                "retry with a larger --timeout"
            ),
            category="timeout",
            details={"adbd_output": reply, "polls": polls},
            timing_ms=total_ms,
        )

    if (uid == 0) != want_root:
        # Reachable, but still the old uid. Which of three things happened is
        # told by adbd's pid, and each needs a different next step.
        details = {
            "adbd_output": reply,
            "uid": uid,
            "polls": polls,
            "adbd_pid_before": pid_before,
            "adbd_pid_after": pid_after,
        }
        restarted = pid_before is not None and pid_after is not None and pid_after != pid_before
        if want_root:
            how = f" (adbd restarted, pid {pid_before} → {pid_after})" if restarted else ""
            return fail(
                code="ADB_ROOT_REFUSED",
                message=(
                    f"adb root said {reply!r} but the shell uid is still {uid}{how} "
                    f"after {polls} attempt(s)"
                ),
                suggestion=SU_HINT,
                category="device",
                details=details,
                timing_ms=total_ms,
            )
        if restarted:
            return fail(
                code="ADB_UNROOT_NO_EFFECT",
                message=(
                    f"adb unroot restarted adbd (pid {pid_before} → {pid_after}) but it came "
                    f"back as uid {uid}: this build keeps adbd root whatever `adb unroot` asks"
                ),
                suggestion=UNROOT_NO_EFFECT_HINT,
                category="device",
                details=details,
                timing_ms=total_ms,
            )
        if pid_before is not None and pid_after == pid_before:
            return fail(
                code="ADBD_RESTART_TIMEOUT",
                message=(
                    f"adb unroot said {reply!r} but adbd did not restart (pid {pid_before} "
                    f"unchanged after {timeout:g}s); the shell uid is still {uid}"
                ),
                suggestion="Reboot the device and retry `alb adb unroot`",
                category="timeout",
                details=details,
                timing_ms=total_ms,
            )
        return fail(
            code="ADBD_RESTART_TIMEOUT",
            message=(
                f"adb unroot said {reply!r} but the shell uid is still {uid} after "
                f"{timeout:g}s ({polls} attempts); adbd's pid could not be read, so whether "
                "it restarted is unknown"
            ),
            suggestion=(
                "Compare `alb shell pidof adbd` before and after `alb adb unroot`: a new pid "
                "that is still root means this build keeps adbd root (rebooting will not "
                "change that); the same pid means the restart did not happen — reboot and retry"
            ),
            category="timeout",
            details=details,
            timing_ms=total_ms,
        )

    outcome: Outcome
    if want_root:
        outcome = "already_root" if already else "became_root"
    else:
        outcome = "already_non_root" if already else "became_non_root"
    return ok(
        data=AdbdResult(outcome=outcome, uid=uid, adbd_output=reply, polls=polls, wait_ms=wait_ms),
        timing_ms=total_ms,
    )


async def _poll_uid(
    adb_t: Transport,
    *,
    want_root: bool,
    timeout: float,  # noqa: ASYNC109
    poll_sec: float,
    pid_before: int | None = None,
) -> tuple[int | None, int | None, int, int]:
    """Poll `id -u` until it reports the wanted side of root, or the budget ends.

    Returns (uid, adbd_pid, attempts, elapsed_ms). uid is the last value the
    device reported — None when it never answered — so the caller can tell
    "never came back" from "came back with the wrong uid". adbd_pid is the
    pid read on the last wrong-uid answer (None if never read).

    Once adbd's pid differs from ``pid_before``, the new daemon's own
    `id -u` ends the poll at once: adbd settles its uid before it serves
    anything, so that answer is final. A wrong uid from the old pid is the
    old daemon still answering, and the poll goes on.
    """
    start = perf_counter()
    last_uid: int | None = None
    last_pid: int | None = None
    polls = 0
    while True:
        polls += 1
        r = await adb_t.shell("id -u", timeout=5)
        if r.ok:
            uid = _parse_uid(r.stdout)
            if uid is not None:
                last_uid = uid
                if (uid == 0) == want_root:
                    break
                if pid_before is not None:
                    pid = await _adbd_pid(adb_t)
                    if pid is not None:
                        last_pid = pid
                        if pid != pid_before:
                            # The `id -u` above may be the old adbd's last
                            # answer: the restart can fall between the two
                            # calls. Only the new adbd is alive now, so its
                            # own answer decides; none yet → keep polling.
                            polls += 1
                            r = await adb_t.shell("id -u", timeout=5)
                            fresh = _parse_uid(r.stdout) if r.ok else None
                            if fresh is not None:
                                last_uid = fresh
                                break
        if perf_counter() - start >= timeout:
            break
        await asyncio.sleep(poll_sec)
    return last_uid, last_pid, polls, int((perf_counter() - start) * 1000)


async def _adbd_pid(adb_t: Transport) -> int | None:
    """adbd's pid, or None when it cannot be told. The shell runs under
    adbd's own uid, so the pid is visible whether adbd is root or not; a
    missing `pidof`, an offline device or more than one match is None."""
    r = await adb_t.shell("pidof adbd", timeout=5)
    if not r.ok:
        return None
    tokens = r.stdout.split()
    if len(tokens) != 1 or not tokens[0].isdigit():
        return None
    return int(tokens[0])


def _parse_uid(stdout: str) -> int | None:
    # `id -u` prints a bare number; anything else (a login banner, an adb
    # "error:" line that landed on stdout) is not a uid.
    token = stdout.strip().split()
    if not token or not token[0].isdigit():
        return None
    return int(token[0])


# ─── remount ───────────────────────────────────────────────────────
async def remount(transport: Transport) -> Result[RemountResult]:
    """`adb remount`, output passed through verbatim.

    Sorted into exactly three buckets — remounted / needs_reboot / failed —
    because that is all the caller needs to decide the next step. Verity is
    never disabled from here: that changes what the bootloader verifies, so
    it stays a deliberate manual command.
    """
    adb_t = _find_adb(transport)
    run = _adb_runner(adb_t)
    if adb_t is None or run is None:
        return _unsupported(transport)

    perm = await transport.check_permissions("adbd.remount", {"verb": "remount"})
    if perm.behavior == "deny":
        return fail(
            code="PERMISSION_DENIED",
            message=perm.reason or "adb remount blocked",
            suggestion=perm.suggestion or "",
            category="permission",
        )

    r: ShellResult = await run(["remount"], timeout=60)
    output = r.stdout + r.stderr
    low = output.lower()

    if r.error_code in {
        "TIMEOUT_SHELL",
        "ADB_BINARY_NOT_FOUND",
        "DEVICE_NOT_FOUND",
        "DEVICE_OFFLINE",
        "DEVICE_UNAUTHORIZED",
        "ADB_SERVER_UNREACHABLE",
    }:
        return fail(
            code=r.error_code,
            message=output.strip() or "adb remount failed",
            suggestion="Check `alb devices` / `alb status`",
            category="transport",
            timing_ms=r.duration_ms,
        )

    if any(p in low for p in _REMOUNT_REBOOT_PHRASES):
        return ok(
            data=RemountResult(outcome="needs_reboot", output=output), timing_ms=r.duration_ms
        )

    # Old adb exits 0 even when adbd reports "remount failed", so the words
    # count as much as the exit code.
    if not r.ok or any(p in low for p in _REMOUNT_FAILED_PHRASES):
        return fail(
            code="ADB_REMOUNT_FAILED",
            message=output.strip() or f"adb remount exited {r.exit_code}",
            suggestion=REMOUNT_HINT,
            category="device",
            details={"stdout": r.stdout, "stderr": r.stderr, "exit_code": r.exit_code},
            timing_ms=r.duration_ms,
        )

    return ok(data=RemountResult(outcome="remounted", output=output), timing_ms=r.duration_ms)
