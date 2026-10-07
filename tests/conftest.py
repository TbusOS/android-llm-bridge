"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

import pytest

import alb.infra.workspace as workspace_mod
from alb.infra.event_bus import reset_bus
from alb.infra.metric_store import reset_metric_store
from alb.remote.forwarder import reset_forwarders
from alb.remote.registry import reset_agent_registry

# ─── keep every test out of the real workspace ─────────────────────
#
# workspace_root() falls back to <cwd>/workspace, then ~/.alb-workspace,
# when ALB_WORKSPACE is unset (src/alb/infra/workspace.py). pytest runs
# from the repo root, so a test that forgot to set it wrote flash job
# records into the repo's workspace/flash/, chat sessions into
# ~/.alb-workspace/sessions/ and lines into ~/.alb-workspace/events.jsonl
# — next to, and indistinguishable from, records of real board work.


class _DefaultWorkspaceTripwire(type(Path())):  # type: ignore[misc]
    """Stands in for ``Path`` inside ``alb.infra.workspace``.

    workspace_root() calls ``Path.cwd()`` / ``Path.home()`` only on its
    fallback branch. Each call is recorded and answered with a scratch
    dir, so a test that reaches the fallback fails (see
    `_isolate_workspace`) instead of writing into the real workspace.
    """

    hits: ClassVar[list[str]] = []
    scratch: ClassVar[Path] = Path("/nonexistent")

    @classmethod
    def cwd(cls) -> Path:
        cls.hits.append("Path.cwd()")
        return cls.scratch

    @classmethod
    def home(cls) -> Path:
        cls.hits.append("Path.home()")
        return cls.scratch


@pytest.fixture(scope="session")
def _workspace_tripwire(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    _DefaultWorkspaceTripwire.scratch = tmp_path_factory.mktemp("default-workspace-fallback")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(workspace_mod, "Path", _DefaultWorkspaceTripwire)
        yield


@pytest.fixture(autouse=True)
def _isolate_workspace(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    _workspace_tripwire: None,
) -> Iterator[None]:
    """Give every test its own empty ALB_WORKSPACE, and fail a test that
    still resolves the default one (e.g. after ``delenv("ALB_WORKSPACE")``).

    The dir comes from tmp_path_factory, not ``tmp_path``, so tests that
    inspect their own ``tmp_path`` don't see workspace files in it. A test
    that needs its own workspace keeps doing ``setenv("ALB_WORKSPACE", …)``.
    """
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path_factory.mktemp("alb-workspace")))
    _DefaultWorkspaceTripwire.hits.clear()
    yield
    hits = list(_DefaultWorkspaceTripwire.hits)
    _DefaultWorkspaceTripwire.hits.clear()
    if hits:
        pytest.fail(
            "workspace_root() fell back to the real default workspace "
            f"({', '.join(hits)}): ALB_WORKSPACE was unset during this test. "
            "Point it at a tmp dir instead of deleting it.",
            pytrace=False,
        )


@pytest.fixture
def default_workspace_hits() -> list[str]:
    """The tripwire's record, for tests of the tripwire itself. Clear it
    before returning, or the test fails at teardown."""
    return _DefaultWorkspaceTripwire.hits


@pytest.fixture(autouse=True)
def _reset_event_infra():
    """Reset the event-bus + metric-store + agent-registry + adb-forwarder
    singletons around every test (ADR-049 / ADR-051).

    Paired + ordered (store first, bus second) so the MetricStore never
    holds a reference to a stale bus, and a per-test `create_app()`
    lifespan can't leak listeners across tests (which would double-count
    every `tps_sample`). The agent registry + forwarder are reset too so a
    remote-agent test never sees a connection / pending channel / bound
    listener left by another test. Supersedes the manual `reset_bus()` calls
    scattered across individual test files — those remain harmless."""
    reset_metric_store()
    reset_bus()
    reset_agent_registry()
    reset_forwarders()
    yield
    reset_metric_store()
    reset_bus()
    reset_agent_registry()
    reset_forwarders()


@pytest.fixture
def dummy_transport():
    """Placeholder for a mocked Transport. Real fixture lands in M1 tests."""
    return None
