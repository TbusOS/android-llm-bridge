"""Wire protocol for the remote device agent (ADR-050/051/052).

Single source of truth shared by the hub (alb-api) and the agent. The
SIGNALING connection carries only these control frames (JSON text). Each
DATA channel is its own connection carrying raw bytes (no per-frame header);
the channel id (cid) is correlated once at dial-back, not per frame.

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
    OPEN_CHANNEL = "open_channel"
    CHANNEL_OPENED = "channel_opened"
    CHANNEL_ERROR = "channel_error"
    CLOSE_CHANNEL = "close_channel"
    CHANNEL_CLOSED = "channel_closed"


_VERB_VALUES = {v.value for v in Verb}


def new_cid() -> str:
    """A hub-generated, unguessable channel id.

    Unguessable (uuid4, 122 bits) + token-gated on the dial-back, so a third
    party cannot claim a channel's data plane. NOTE: P0 (single-agent) does NOT
    yet bind the cid to a specific agent_id; per-agent binding is required before
    multi-agent — see DEBT-084.
    """
    return uuid.uuid4().hex


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


def list_com() -> dict[str, Any]:
    return _frame(Verb.LIST_COM)


def com_list(ports: list[dict[str, Any]]) -> dict[str, Any]:
    return _frame(Verb.COM_LIST, ports=ports)


def open_channel(
    *, cid: str, ctype: ChannelType, role: ChannelRole, params: dict[str, Any]
) -> dict[str, Any]:
    return _frame(
        Verb.OPEN_CHANNEL,
        cid=cid,
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
