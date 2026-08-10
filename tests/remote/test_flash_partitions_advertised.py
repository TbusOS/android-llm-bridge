"""The agent's partition allowlist reaches the UI (ADR-056 follow-up).

Real-hardware defect, 2026-08-10: the web page's partition picker was a
hard-coded list of four names. The bench's agent allowed exactly one, and it
was not among them — so every flash started from the web ended at
FLASH_PARTITION_REJECTED while the same flash from the CLI worked. The
plumbing was fine end to end; only the list of choices was invented by the
side that could not know.

The subtle half, and what most of these tests pin: **empty means "no allowlist
configured", i.e. any well-formed name — not "nothing allowed"**. A consumer
that renders an empty picker on an empty list turns a permissive bench into an
unusable one.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from alb.remote.registry import AgentConnection, AgentRegistry

_AGENT_PATH = Path(__file__).resolve().parents[2] / "clients" / "windows-agent" / "alb_agent.py"


def _load_agent():
    spec = importlib.util.spec_from_file_location("alb_agent_partitions_under_test", _AGENT_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


agent_mod = _load_agent()


async def _noop(_m):  # pragma: no cover - registry needs a send callable
    return None


def _conn(**kw) -> AgentConnection:
    defaults = dict(
        agent_id="a",
        name="bench",
        version=1,
        caps=["adb", "fastboot"],
        send_control=_noop,
        registry=AgentRegistry(),
    )
    defaults.update(kw)
    return AgentConnection(**defaults)  # type: ignore[arg-type]


# ── the agent side: the allowlist must leave the machine that owns it ──


def test_hello_carries_the_allowlist(monkeypatch):
    monkeypatch.setattr(agent_mod, "_FLASH_PARTITIONS", frozenset({"cfg", "boot"}))
    hello = json.loads(agent_mod._hello("a", "n", None))
    assert hello["flash_partitions"] == ["boot", "cfg"], "sorted, so the UI order is stable"


def test_hello_reports_empty_when_no_allowlist_is_configured(monkeypatch):
    """Empty is a real answer ("any well-formed"), not a missing one — the
    field must still be present so the hub can tell it from an old agent."""
    monkeypatch.setattr(agent_mod, "_FLASH_PARTITIONS", frozenset())
    hello = json.loads(agent_mod._hello("a", "n", None))
    assert hello["flash_partitions"] == []


def test_allowlist_and_hello_cannot_drift(monkeypatch):
    """Whatever the agent advertises, it must actually accept — otherwise the
    picker offers a name the very same process will refuse."""
    monkeypatch.setattr(agent_mod, "_FLASH_PARTITIONS", frozenset({"cfg"}))
    advertised = json.loads(agent_mod._hello("a", "n", None))["flash_partitions"]
    assert advertised, "guard: the case only means something when non-empty"
    for name in advertised:
        assert agent_mod._partition_allowed(name)


# ── the hub side: carry it, don't invent it ────────────────────────


def test_connection_keeps_the_allowlist():
    assert _conn(flash_partitions=["cfg"]).flash_partitions == ["cfg"]


def test_connection_defaults_to_empty_for_older_agents():
    """An agent predating the field sends nothing. That must land as empty
    (= unknown = fall back), never as a crash or a fabricated list."""
    assert _conn().flash_partitions == []


def test_status_listing_exposes_it():
    reg = AgentRegistry()
    conn = _conn(registry=reg, flash_partitions=["cfg"])
    reg._agents[conn.agent_id] = conn  # bypass the async register for a pure read
    assert reg.list_agents()[0]["flash_partitions"] == ["cfg"]


def test_flash_view_reports_the_agents_list(monkeypatch):
    from alb.remote import forwarder

    conn = _conn(flash_partitions=["cfg"])
    monkeypatch.setattr(
        "alb.remote.registry.get_agent_registry", lambda: type("R", (), {"current_agent": lambda _s: conn})()
    )
    view = forwarder._flash_view()
    assert view["partitions"] == ["cfg"]
    assert view["available"] is True


def test_flash_view_with_no_agent_is_empty_not_a_crash(monkeypatch):
    from alb.remote import forwarder

    monkeypatch.setattr(
        "alb.remote.registry.get_agent_registry", lambda: type("R", (), {"current_agent": lambda _s: None})()
    )
    view = forwarder._flash_view()
    assert view["partitions"] == []
    assert view["available"] is False


def test_service_status_carries_partitions():
    from alb.remote.flash import FlashService

    conn = _conn(flash_partitions=["cfg", "boot"])
    svc = FlashService(lambda: conn)
    assert svc.status()["partitions"] == ["cfg", "boot"]


def test_service_partitions_empty_without_an_agent():
    from alb.remote.flash import FlashService

    svc = FlashService(lambda: None)
    assert svc.partitions() == []


@pytest.mark.parametrize("advertised", [[], ["cfg"], ["cfg", "boot"]])
def test_status_always_has_the_key(advertised):
    """A consumer must never have to distinguish "absent" from "empty" —
    that ambiguity is what a fallback rule cannot be written against."""
    from alb.remote.flash import FlashService

    svc = FlashService(lambda: _conn(flash_partitions=advertised))
    assert "partitions" in svc.status()
