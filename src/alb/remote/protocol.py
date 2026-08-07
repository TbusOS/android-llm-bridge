"""Wire protocol for the remote device agent (ADR-050/051/052).

Single source of truth shared by the hub (alb-api) and the agent. The
SIGNALING connection carries only these control frames (JSON text). Each
DATA channel is its own connection carrying raw bytes (no per-frame header);
the channel id (cid) is correlated once at dial-back, not per frame, and the
dial-back is authenticated by a per-channel secret minted with the cid (DEBT-084).

Control frame shape:
    {"v": <PROTOCOL_VERSION>, "verb": "<verb>", ...fields}

Channel roles (ADR-052) are the crux: an adb channel proxies a listen-socket
daemon (the local adb server) and must FAIL FAST with NO retry — a reset is a
real error (USB reauth / device drop / server crash). A serial channel proxies
a per-connection exclusive gateway (ser2net-style) and MAY use bounded retry.
Conflating the two is the L-034 anti-pattern.
"""

from __future__ import annotations

import enum
import json
import secrets
import uuid
from typing import Any

PROTOCOL_VERSION = 1

# tcp channel target allowlist (ADR-050 §6): the agent must only proxy the
# local adb server, NEVER an arbitrary host:port, or it becomes an open proxy
# on the remote machine's LAN. Enforced on BOTH the hub and the agent.
ADB_TARGET = "127.0.0.1:5037"

# Dial-back credential headers (ADR-055). The data-plane dial-back has to
# present the agent token AND the per-channel secret; carrying them as HTTP
# headers instead of query params keeps them out of the request line, which
# every access log, proxy log and connection-error message reproduces
# verbatim. Mirrored as string literals in clients/windows-agent/alb_agent.py
# (the agent is standalone and cannot import alb).
TOKEN_HEADER = "x-alb-token"
CSECRET_HEADER = "x-alb-csecret"


class ProtocolError(Exception):
    """A control frame is malformed or carries an unknown verb."""


class ChannelType(str, enum.Enum):
    TCP = "tcp"
    SERIAL = "serial"
    # ADR-056: not a byte-stream proxy at all — it carries one request/reply
    # job (send an image, run fastboot on the agent host, stream progress
    # back). There is no endpoint on the agent side to "connect" to.
    JOB = "job"


class ChannelRole(str, enum.Enum):
    # ADR-052:
    #   DAEMON  — proxied endpoint is a listen-socket daemon (adb server).
    #             Fail fast, NO retry.
    #   GATEWAY — proxied endpoint is a per-connection exclusive gateway
    #             (ser2net-style serial bridge). Bounded retry allowed.
    #   JOB     — one-shot unit of work (ADR-056). Same no-retry policy as
    #             DAEMON, kept distinct so nobody reads a fastboot channel as
    #             "proxies a daemon": retrying is wrong here for a different
    #             reason — the work may have already had a side effect on the
    #             device, so a silent second attempt is not a repeat, it is a
    #             second write.
    DAEMON = "daemon"
    GATEWAY = "gateway"
    JOB = "job"


_DEFAULT_ROLE: dict[ChannelType, ChannelRole] = {
    ChannelType.TCP: ChannelRole.DAEMON,
    ChannelType.SERIAL: ChannelRole.GATEWAY,
    ChannelType.JOB: ChannelRole.JOB,
}

# Agent capabilities advertised in `hello`. The hub uses these to answer
# "can this bench flash?" immediately instead of discovering it by timeout
# (ADR-056 §决定 7).
CAP_ADB = "adb"
CAP_FASTBOOT = "fastboot"


# The three job enums below use enum.StrEnum while their older neighbours
# use (str, enum.Enum). Not an oversight: StrEnum is the correct form on
# this target (py311) and new code should not repeat a lint the linter
# already flags. Converting the older three is a separate change — their
# str() rendering differs, and they are compared in tests.
class JobOp(enum.StrEnum):
    """What a job channel was opened to do. Sent in the opening control
    frame; the agent builds the actual argv itself (ADR-056 §决定 5) — the
    hub never sends a command line."""

    FLASH = "flash"
    REBOOT = "reboot"
    DEVICES = "devices"


class JobEvent(enum.StrEnum):
    """Agent → hub frames on a job channel."""

    ACCEPTED = "accepted"  # request understood; work is starting
    PROGRESS = "progress"
    DONE = "done"


class JobPhase(enum.StrEnum):
    """Which half of a flash the progress refers to. Two phases, not one
    percentage: transferring 1 KB over the tunnel and writing it to a
    partition fail for completely different reasons, and a caller staring at
    a stalled bar needs to know which one it is stuck in."""

    TRANSFER = "transfer"
    FLASH = "flash"


def default_role(ctype: ChannelType) -> ChannelRole:
    """The retry-role a channel type defaults to (ADR-052)."""
    return _DEFAULT_ROLE[ctype]


class Verb(str, enum.Enum):
    HELLO = "hello"
    HELLO_OK = "hello_ok"
    HEARTBEAT = "heartbeat"
    LIST_COM = "list_com"
    COM_LIST = "com_list"
    LIST_ADB = "list_adb"
    ADB_LIST = "adb_list"
    RESTART_ADB = "restart_adb"
    OPEN_CHANNEL = "open_channel"
    CHANNEL_OPENED = "channel_opened"
    CHANNEL_ERROR = "channel_error"
    CLOSE_CHANNEL = "close_channel"
    CHANNEL_CLOSED = "channel_closed"


_VERB_VALUES = {v.value for v in Verb}


def new_cid() -> str:
    """A hub-generated channel id used to correlate the dial-back to its pending
    forwarder request.

    The cid is the routing key only; the per-channel SECRET that authenticates
    the dial-back is minted separately by new_csecret() (DEBT-084). Binding the
    channel to a specific agent_id / device (multi-agent addressing) is still
    future work — see DEBT-083.
    """
    return uuid.uuid4().hex


def new_csecret() -> str:
    """A hub-minted, per-channel secret (DEBT-084).

    Minted at open_channel and delivered ONLY on the owning agent's signaling
    WS; the agent must present it on the data-plane dial-back, which the hub
    verifies with a constant-time compare (registry.resolve_pending). This binds
    the data channel to the agent that actually received the open_channel: a
    process that merely holds the shared agent token AND a live cid still cannot
    claim the channel without this secret. 256-bit and URL-safe; it rides the
    dial-back as a header (CSECRET_HEADER), not as a query param — see
    ADR-055 for why the query string was the wrong place for it.
    """
    return secrets.token_urlsafe(32)


# ── builders ─────────────────────────────────────────────────────────


def _frame(verb: Verb, **fields: Any) -> dict[str, Any]:
    return {"v": PROTOCOL_VERSION, "verb": verb.value, **fields}


def hello(
    *, agent_id: str, name: str, version: int, caps: list[str], token: str | None
) -> dict[str, Any]:
    return _frame(
        Verb.HELLO,
        agent_id=agent_id,
        name=name,
        agent_version=version,
        caps=caps,
        token=token,
    )


def hello_ok(*, server_version: int) -> dict[str, Any]:
    return _frame(Verb.HELLO_OK, server_version=server_version)


def heartbeat() -> dict[str, Any]:
    return _frame(Verb.HEARTBEAT)


def list_adb() -> dict[str, Any]:
    return _frame(Verb.LIST_ADB)


def adb_list(devices: list[str]) -> dict[str, Any]:
    return _frame(Verb.ADB_LIST, devices=devices)


def restart_adb(*, kill_conflicts: bool = False) -> dict[str, Any]:
    """Ask the agent to restart ITS local adb server, then re-report devices.
    The restart runs on the agent host by design — killing an adb server from
    the hub side is never allowed; agents that predate this verb ignore it.

    kill_conflicts asks the agent to first terminate adb-flavoured foreign
    processes (renamed vendor adb builds hold the exclusive USB interface, so
    a plain server restart just loses the race again). Deliberately a bool:
    WHAT matches is the agent's own heuristic — the hub can never name an
    arbitrary process to kill."""
    return _frame(Verb.RESTART_ADB, kill_conflicts=kill_conflicts)


def list_com() -> dict[str, Any]:
    return _frame(Verb.LIST_COM)


def com_list(ports: list[dict[str, Any]]) -> dict[str, Any]:
    return _frame(Verb.COM_LIST, ports=ports)


def open_channel(
    *,
    cid: str,
    csecret: str,
    ctype: ChannelType,
    role: ChannelRole,
    params: dict[str, Any],
) -> dict[str, Any]:
    return _frame(
        Verb.OPEN_CHANNEL,
        cid=cid,
        csecret=csecret,
        channel_type=ctype.value,
        role=role.value,
        params=params,
    )


def channel_opened(*, cid: str) -> dict[str, Any]:
    return _frame(Verb.CHANNEL_OPENED, cid=cid)


def channel_error(*, cid: str, reason: str) -> dict[str, Any]:
    return _frame(Verb.CHANNEL_ERROR, cid=cid, reason=reason)


def close_channel(*, cid: str) -> dict[str, Any]:
    return _frame(Verb.CLOSE_CHANNEL, cid=cid)


def channel_closed(*, cid: str) -> dict[str, Any]:
    return _frame(Verb.CHANNEL_CLOSED, cid=cid)


# ── codec ────────────────────────────────────────────────────────────


def encode_control(msg: dict[str, Any]) -> str:
    """Serialize a control frame to JSON text."""
    return json.dumps(msg, ensure_ascii=False)


def decode_control(text: str) -> dict[str, Any]:
    """Parse + validate a control frame. Raises ProtocolError on anything
    that is not a JSON object carrying a known verb."""
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, TypeError) as e:
        raise ProtocolError(f"not valid JSON: {e}") from e
    if not isinstance(obj, dict):
        raise ProtocolError("control frame must be a JSON object")
    verb = obj.get("verb")
    if verb not in _VERB_VALUES:
        raise ProtocolError(f"unknown verb: {verb!r}")
    return obj


# ── job channel messages (ADR-056) ───────────────────────────────────
#
# These ride the job channel's frames (alb.remote.jobframe), NOT the
# signaling WS, so they carry no `verb` and are not validated by
# decode_control. Builders live here anyway so the hub and the agent share
# one definition of the vocabulary.


def job_flash(*, partition: str, size: int, sha256: str) -> dict[str, Any]:
    """Opening frame of a flash job.

    Carries the digest UP FRONT so the agent can refuse a corrupt transfer
    before it touches the device (ADR-056 §决定 6) — the one moment when
    "is this image intact" is still a cheap question. Note what is absent:
    no file name, no path, no command line. The agent names its own temp
    file and assembles its own argv."""
    return {"op": JobOp.FLASH.value, "partition": partition, "size": size, "sha256": sha256}


def job_reboot(*, target: str) -> dict[str, Any]:
    """Ask the agent to run `fastboot reboot [target]`. The remedy for a
    board that alb itself pushed into fastboot and cannot get out of."""
    return {"op": JobOp.REBOOT.value, "target": target}


def job_devices() -> dict[str, Any]:
    """`fastboot devices` on the agent host — the only way to learn whether
    the board is actually in fastboot, since it vanishes from adb there."""
    return {"op": JobOp.DEVICES.value}


def job_accepted(*, detail: str = "") -> dict[str, Any]:
    return {"ev": JobEvent.ACCEPTED.value, "detail": detail}


def job_progress(*, phase: str, done: int, total: int, text: str = "") -> dict[str, Any]:
    """`total` may be 0 when the agent cannot know it (fastboot's own output
    is not always quantified) — renderers must treat 0 as "indeterminate"
    rather than dividing by it."""
    return {
        "ev": JobEvent.PROGRESS.value,
        "phase": phase,
        "done": done,
        "total": total,
        "text": text,
    }


def job_done(
    *,
    ok: bool,
    rc: int,
    stdout: str = "",
    stderr: str = "",
    error: str = "",
    code: str = "",
) -> dict[str, Any]:
    """Terminal frame. `code` is an alb error code (infra.errors) when the
    agent can name the failure precisely; `error` is the human sentence.
    Both are present on failure so the caller can branch on the code without
    parsing prose."""
    return {
        "ev": JobEvent.DONE.value,
        "ok": ok,
        "rc": rc,
        "stdout": stdout,
        "stderr": stderr,
        "error": error,
        "code": code,
    }
