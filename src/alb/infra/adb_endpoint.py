"""Where the adb *client* should look for an adb *server* (ADR-057).

## The failure this exists to prevent

An adb client never fails for want of a server. Point it at a port where some
other adb server happens to live and it connects, answers `host:version`, and
reports **zero devices**. Point it at nothing and it *starts* a server for you,
which also reports zero devices. There is no error path — the wrong endpoint
and an unplugged board are the same observation.

That is fine when the only adb server is the local one. It stops being fine the
moment alb forwards a *remote* bench's adb server to a local port (ADR-050): now
two adb servers exist on this machine, one with the board and one without, and
the difference between them is a port number that lives in the operator's shell.

Real cost, 2026-08-10: the bench's adb tunnel was declared broken and worked
around over UART for a day. The tunnel was fine end to end — a raw
`host:devices-l` through it returned the board. What was wrong was
``ADB_SERVER_SOCKET=tcp:localhost:5037`` left in a shell profile from an older
setup, while the forwarder had moved to 15037 to avoid colliding with the
machine's own adb server. `alb status` printed ``server_reachable: True`` and
``ok: True`` the whole time, because it *had* reached an adb server. Just not
that one.

## Why resolution belongs here and not in the shell

The port is chosen by the hub process (``ALB_ADB_FORWARD_PORT``) and consumed by
a different process (the CLI). Nothing connects the two except a human
remembering to export a matching value. Any hand-maintained copy — a shell
profile, a config file, a wiki line — is a second source of truth that goes
stale silently, because staleness looks exactly like "no board plugged in".

So the CLI asks the process that actually bound the port. In the hub itself that
is a function call; from the CLI it is one HTTP GET to a hub on this machine.
Neither can go stale: if the answer is wrong, the forwarder is wrong too.

## Precedence, and why the environment still wins

1. ``[transport.adb] server_socket`` in config.toml — an explicit operator choice
2. ``ADB_SERVER_SOCKET`` in the environment — adb's own convention
3. the local hub's adb forwarder, discovered at runtime
4. nothing — adb's own default

Auto-discovery deliberately sits *below* the environment. ``ADB_SERVER_SOCKET``
is not alb's variable; it steers every adb client in that shell. If alb quietly
overrode it, ``alb devices`` and ``adb devices`` in the same terminal would
disagree, and the operator would be debugging two truths instead of one. The
answer to a stale environment variable is to *say so* — see
:func:`endpoint_conflict` — not to route around the person who set it.

The consequence is worth stating plainly: a shell that exports nothing at all is
now the *best* configured shell, because layer 3 tracks the forwarder wherever
it moves.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

__all__ = [
    "AdbEndpoint",
    "HubAdbView",
    "endpoint_conflict",
    "hub_adb_view",
    "reset_hub_cache",
    "resolve_endpoint",
]

# Short on purpose. This runs before ordinary commands, so a hub that is down
# must cost a refused connection (microseconds on loopback), not a stall. The
# ceiling only applies to a host that accepts but does not answer.
_HUB_TIMEOUT_S = 0.4

_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})

# Per-process memo. Commands are short-lived, so "once per process" is the same
# as "once", while long-lived callers (the agent loop) keep paying nothing.
# Two variables rather than a sentinel value, because "probed, and there is no
# hub" must not re-probe on every command — that is the case a plain `None`
# cache would get wrong, and it is the common one on a laptop.
_HUB_PROBED = False
_HUB_CACHE: HubAdbView | None = None


@dataclass(frozen=True)
class AdbEndpoint:
    """The resolved answer plus *where it came from*.

    ``source`` is not decoration: every diagnostic in this file is the
    difference between two answers, and a difference is unreadable without
    knowing who supplied each side.
    """

    socket: str | None
    source: str  # config | env | hub | default

    @property
    def described(self) -> str:
        return f"{self.socket or 'adb default (tcp:localhost:5037)'} (from {self.source})"


@dataclass(frozen=True)
class HubAdbView:
    """What the hub says about adb: its forwarder port, and what the bench sees.

    The device list matters as much as the port. A port mismatch alone is not
    worth interrupting anyone over — two adb servers can legitimately coexist.
    A port mismatch *while the bench holds the board you are looking for* is the
    whole bug.
    """

    port: int | None
    bound: bool
    devices: tuple[str, ...]

    @property
    def socket(self) -> str | None:
        return f"tcp:127.0.0.1:{self.port}" if self.bound and self.port else None


def _socket_port(spec: str | None) -> int | None:
    """Port out of an ``ADB_SERVER_SOCKET`` spec, or None if it has none.

    Accepts what adb accepts: ``tcp:<port>``, ``tcp:<host>:<port>``. A
    ``unix:<path>`` spec has no port and yields None, which callers read as
    "not comparable" rather than "mismatch" — comparing a socket path against a
    TCP port would manufacture a conflict that does not exist.
    """
    if not spec:
        return None
    parts = spec.split(":")
    if parts[0] != "tcp" or len(parts) < 2:
        return None
    try:
        return int(parts[-1])
    except ValueError:
        return None


def _hub_base_url() -> str:
    """Same resolution the flash CLI uses, kept in step deliberately: an
    operator who points ALB_API_URL at a hub expects *everything* to follow."""
    base = os.environ.get("ALB_API_URL", "").strip()
    if base:
        return base.rstrip("/")
    host = os.environ.get("ALB_API_HOST", "127.0.0.1")
    if host == "0.0.0.0":  # a bind-all address is not a dial-able one
        host = "127.0.0.1"
    return f"http://{host}:{os.environ.get('ALB_API_PORT', '8765')}"


def _hub_is_local(url: str) -> bool:
    """A forwarder binds ``127.0.0.1`` on the *hub's* host. If the hub is
    somewhere else, its port number is useless here — worse than useless, since
    a port that happens to be open locally would silently become the target."""
    try:
        return (urlparse(url).hostname or "") in _LOCAL_HOSTS
    except ValueError:
        return False


def _view_from_status(payload: dict[str, Any]) -> HubAdbView:
    fwd = (payload.get("forwarders") or {}).get("adb") or {}
    port = fwd.get("port")
    devices: tuple[str, ...] = ()
    for a in payload.get("agents") or []:
        if a.get("current"):
            devices = tuple(str(d) for d in (a.get("adb_devices") or []))
            break
    return HubAdbView(
        port=int(port) if isinstance(port, int) else None,
        bound=bool(fwd.get("bound")),
        devices=devices,
    )


def _in_process_view() -> HubAdbView | None:
    """The hub asking itself. Returns None unless a forwarder is actually bound
    in *this* process.

    This branch is not an optimisation. Inside alb-api, an HTTP GET to our own
    ``/agent/status`` would be a self-request issued from the event loop that
    has to serve it — a deadlock waiting for the right amount of load. Reading
    the singleton is the same fact without the round trip.
    """
    try:
        from alb.remote.forwarder import forwarder_status
        from alb.remote.registry import get_agent_registry
    except Exception:  # pragma: no cover - alb-api extras absent
        return None
    try:
        fwd = forwarder_status().get("adb") or {}
        if not fwd.get("bound"):
            return None
        agent = get_agent_registry().current_agent()
        devices = tuple(getattr(agent, "adb_devices", ()) or ()) if agent else ()
        return HubAdbView(port=fwd.get("port"), bound=True, devices=devices)
    except Exception:  # pragma: no cover - never let discovery break a command
        return None


def hub_adb_view(*, refresh: bool = False) -> HubAdbView | None:
    """What the local hub reports about adb, or None if there is no local hub.

    Never raises and never blocks longer than ``_HUB_TIMEOUT_S``: this runs on
    the way to ordinary commands, so a hub that is down or wedged must cost
    nothing beyond a missed hint.
    """
    global _HUB_CACHE, _HUB_PROBED
    if _HUB_PROBED and not refresh:
        return _HUB_CACHE

    view = _in_process_view()
    if view is None:
        url = _hub_base_url()
        if _hub_is_local(url):
            try:
                import httpx

                r = httpx.get(f"{url}/agent/status", timeout=_HUB_TIMEOUT_S)
                if r.status_code == 200:
                    view = _view_from_status(r.json())
            except Exception:  # absence of a hub is the normal case, not an error
                view = None
    _HUB_CACHE = view
    _HUB_PROBED = True
    return view


def reset_hub_cache() -> None:
    """Drop the memo. For tests, and for any caller that just restarted a hub."""
    global _HUB_CACHE, _HUB_PROBED
    _HUB_CACHE = None
    _HUB_PROBED = False


def resolve_endpoint(config_socket: str | None = None, *, allow_hub: bool = True) -> AdbEndpoint:
    """Resolve the adb server endpoint by the precedence documented above.

    ``allow_hub=False`` keeps the answer purely local (config + environment) for
    callers that must not touch the network.
    """
    if config_socket:
        return AdbEndpoint(config_socket, "config")

    env = os.environ.get("ADB_SERVER_SOCKET", "").strip()
    if env:
        return AdbEndpoint(env, "env")

    if allow_hub:
        view = hub_adb_view()
        if view is not None and view.socket:
            return AdbEndpoint(view.socket, "hub")

    return AdbEndpoint(None, "default")


def endpoint_conflict(endpoint: AdbEndpoint, *, view: HubAdbView | None = None) -> str:
    """One sentence naming the contradiction, or ``""`` when there is none.

    Raised only when both halves are true — we are pointed somewhere other than
    the forwarder, **and** the bench on the other side of that forwarder is
    holding devices. Either alone is a legitimate setup; together they are the
    2026-08-10 failure, and the operator is about to be told "no devices" by a
    command that is looking in the wrong place.
    """
    if endpoint.source == "hub":
        return ""
    if view is None:
        view = hub_adb_view()
    if view is None or not view.bound or not view.devices:
        return ""

    ours = _socket_port(endpoint.socket)
    if ours is None and endpoint.source != "default":
        return ""  # a unix socket / unparsable spec: not comparable, so silent
    if ours is None:
        ours = 5037  # adb's own default, which is what we would end up on
    if ours == view.port:
        return ""

    where = {
        "config": "config.toml [transport.adb] server_socket",
        "env": "ADB_SERVER_SOCKET in the environment",
        "default": "adb's default port",
    }.get(endpoint.source, endpoint.source)
    seen = ", ".join(view.devices)
    return (
        f"adb is pointed at port {ours} ({where}), but alb's adb forwarder is on "
        f"port {view.port} and the bench reports {len(view.devices)} device(s): {seen}. "
        f"Unset it, or set it to tcp:127.0.0.1:{view.port}."
    )
