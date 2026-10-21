"""/api/bench/* — the op table over HTTP (ADR-059)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from alb.api.server import create_app
from alb.remote.bench_board import PartitionList
from alb.remote.flash import FlashEvent, FlashResult
from tests.remote.bench_tables import new_agent_ops


@pytest.fixture(autouse=True)
def _ephemeral_forward_port(monkeypatch):
    monkeypatch.setenv("ALB_ADB_FORWARD_PORT", "0")


class _Svc:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def agent_summary(self):
        return {"id": "agent-1", "name": "bench"}

    def ops_reported(self):
        return True

    def partition_list(self):
        return PartitionList(["boot_a"])

    def ops(self):
        return new_agent_ops()

    async def run_op(self, name, params, *, image=None, on_event=None):
        self.calls.append((name, params, image))
        if on_event:
            on_event(FlashEvent(phase="flash", text="working"))
        return FlashResult(ok=True, rc=0, data={"devices": [], "boards": []})


@pytest.fixture
def svc(monkeypatch):
    import alb.api.bench_route as route

    s = _Svc()
    monkeypatch.setattr(route, "get_flash_service", lambda: s)
    return s


def test_ops_lists_the_table_with_parameters(svc):
    with TestClient(create_app()) as c:
        body = c.get("/api/bench/ops").json()
    names = [o["name"] for o in body["ops"]]
    assert names == sorted(names) and "usb-list" in names
    log = next(o for o in body["ops"] if o["name"] == "log-tail")
    assert [p["name"] for p in log["params"]] == ["source", "lines", "grep"]
    assert body["reported"] is True and body["agent"]["name"] == "bench"
    assert (body["partitions"], body["partitions_problem"], body["board"]) == (["boot_a"], "", None)


def test_run_streams_progress_then_the_verdict_with_data(svc):
    with TestClient(create_app()) as c:
        resp = c.post("/api/bench/ops/usb-list", json={"params": {}})
    lines = [json.loads(x) for x in resp.text.splitlines() if x.strip()]
    assert lines[0]["ev"] == "progress"
    assert lines[-1]["ev"] == "done" and lines[-1]["data"] == {"devices": [], "boards": []}
    assert svc.calls == [("usb-list", {}, None)]


def test_an_image_must_be_in_the_workspace(svc):
    with TestClient(create_app()) as c:
        resp = c.post("/api/bench/ops/boot", json={"params": {}, "image": "../../etc/passwd"})
    assert resp.status_code == 400
    assert svc.calls == []


def test_both_endpoints_are_listed_in_the_schema(svc):
    with TestClient(create_app()) as c:
        rest = c.get("/api/version").json()["rest"]
    paths = {(e["method"], e["path"]) for e in rest}
    assert {("GET", "/api/bench/ops"), ("POST", "/api/bench/ops/{op}")} <= paths
