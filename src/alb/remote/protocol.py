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


class ProtocolError(Exception):
    """A control frame is malformed or carries an unknown verb."""


class ChannelType(str, enum.Enum):
    TCP = "tcp"
    SERIAL = "serial"


class ChannelRole(str, enum.Enum):
    # ADR-052:
    #   DAEMON  — proxied endpoint is a listen-socket daemon (adb server).
    #             Fail fast, NO retry.
    #   GATEWAY — proxied endpoint is a per-connection exclusive gateway
    #             (ser2net-style serial bridge). Bounded retry allowed.
    DAEMON = "daemon"
    GATEWAY = "gateway"


_DEFAULT_ROLE: dict[ChannelType, ChannelRole] = {
    ChannelType.TCP: ChannelRole.DAEMON,
    ChannelType.SERIAL: ChannelRole.GATEWAY,
}


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
    claim the channel without this secret. 256-bit, URL-safe so it rides the
    dial-back query string alongside the cid (same wss posture as the token).
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
