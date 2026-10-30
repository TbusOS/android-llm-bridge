"""/api/flash/* — the HTTP surface of a fastboot job (ADR-056).

The property under test is the streaming contract: progress arrives as it
happens and the LAST line is always the verdict. A caller that read only the
final line must still learn everything it needs; a caller that reads
line-by-line must see movement while a partition is being written.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from alb.api.server import create_app
from alb.remote.flash import FlashEvent, FlashResult


@pytest.fixture(autouse=True)
def _ephemeral_forward_port(monkeypatch):
    monkeypatch.setenv("ALB_ADB_FORWARD_PORT", "0")


class _FakeService:
    def __init__(self, result: FlashResult, events: list[FlashEvent] | None = None) -> None:
        self.result = result
        self.events = events or []
        self.calls: list[tuple] = []

    def status(self):
        return {"available": True, "busy": False, "job": ""}

    async def devices(self, *, on_event=None):
        return self._run(("devices",), on_event)

    async def reboot(self, target="", *, on_event=None):
        return self._run(("reboot", target), on_event)

    async def oem(self, command, *, on_event=None):
        return self._run(("oem", command), on_event)

    async def flash(self, partition, image, *, on_event=None):
        return self._run(("flash", partition, str(image)), on_event)

    def _run(self, call, on_event):
        self.calls.append(call)
        for ev in self.events:
            if on_event:
                on_event(ev)
        return self.result


def _install(monkeypatch: pytest.MonkeyPatch, service: Any) -> Any:
    import alb.api.flash_route as route

    monkeypatch.setattr(route, "get_flash_service", lambda: service)
    return service


def _lines(resp) -> list[dict]:
    return [json.loads(x) for x in resp.text.splitlines() if x.strip()]


def test_status_reports_the_service_view(monkeypatch):
    _install(monkeypatch, _FakeService(FlashResult(ok=True)))
    with TestClient(create_app()) as c:
        body = c.get("/api/flash/status").json()
    assert body["ok"] is True
    assert body["available"] is True
    assert body["busy"] is False


def test_devices_streams_ndjson_ending_in_done(monkeypatch):
    svc = _install(monkeypatch, _FakeService(FlashResult(ok=True, rc=0, stdout="serial\tfastboot")))
    with TestClient(create_app()) as c:
        resp = c.post("/api/flash/devices")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")
    lines = _lines(resp)
    assert lines[-1]["ev"] == "done"
    assert lines[-1]["ok"] is True
    assert svc.calls == [("devices",)]


def test_progress_lines_precede_the_verdict(monkeypatch):
    """A reader must be able to render movement without waiting for the end —
    that is the whole reason this endpoint streams."""
    events = [
        FlashEvent(phase="transfer", done=5, total=10),
        FlashEvent(phase="flash", text="writing 'boot'"),
    ]
    _install(monkeypatch, _FakeService(FlashResult(ok=True, rc=0), events))
    with TestClient(create_app()) as c:
        resp = c.post("/api/flash/flash", json={"partition": "boot", "image": "x.bin"})
    # image resolution happens first — this one does not exist
    assert resp.status_code == 400

    # now with a real workspace file
    import alb.api.flash_route as route

    monkeypatch.setattr(route, "_resolve_image", lambda rel: rel)
    with TestClient(create_app()) as c:
        resp = c.post("/api/flash/flash", json={"partition": "boot", "image": "x.bin"})
    lines = _lines(resp)
    assert [ln["ev"] for ln in lines] == ["progress", "progress", "done"]
    assert lines[0]["phase"] == "transfer"
    assert lines[0]["done"] == 5
    assert lines[1]["text"] == "writing 'boot'"
    assert lines[-1]["ok"] is True


def test_failure_verdict_carries_the_code(monkeypatch):
    _install(
        monkeypatch,
        _FakeService(FlashResult(ok=False, rc=1, code="FASTBOOT_BUSY", error="another job")),
    )
    with TestClient(create_app()) as c:
        resp = c.post("/api/flash/devices")
    assert resp.status_code == 200  # the JOB failed, the request did not
    done = _lines(resp)[-1]
    assert done["ok"] is False
    assert done["code"] == "FASTBOOT_BUSY"
    assert done["error"] == "another job"


def test_reboot_passes_the_target(monkeypatch):
    svc = _install(monkeypatch, _FakeService(FlashResult(ok=True, rc=0)))
    with TestClient(create_app()) as c:
        c.post("/api/flash/reboot", json={"target": "bootloader"})
        c.post("/api/flash/reboot", json={})
    assert svc.calls == [("reboot", "bootloader"), ("reboot", "")]


def test_unknown_image_is_rejected_before_any_job(monkeypatch):
    svc = _install(monkeypatch, _FakeService(FlashResult(ok=True)))
    with TestClient(create_app()) as c:
        resp = c.post("/api/flash/flash", json={"partition": "boot", "image": "not-there.bin"})
    assert resp.status_code == 400
    assert svc.calls == []


def test_image_path_cannot_escape_the_workspace(monkeypatch):
    """The image is named workspace-relative on purpose; traversal must be
    refused by the shared resolver, not by a second copy of the rules."""
    svc = _install(monkeypatch, _FakeService(FlashResult(ok=True)))
    with TestClient(create_app()) as c:
        resp = c.post(
            "/api/flash/flash", json={"partition": "boot", "image": "../../etc/passwd"}
        )
    assert resp.status_code == 400
    assert svc.calls == []


def test_partition_is_required(monkeypatch):
    _install(monkeypatch, _FakeService(FlashResult(ok=True)))
    with TestClient(create_app()) as c:
        assert c.post("/api/flash/flash", json={"image": "x.bin"}).status_code == 422
        assert (
            c.post("/api/flash/flash", json={"partition": "", "image": "x.bin"}).status_code == 422
        )


def test_oem_passes_the_command(monkeypatch):
    svc = _install(monkeypatch, _FakeService(FlashResult(ok=True, rc=0)))
    with TestClient(create_app()) as c:
        resp = c.post("/api/flash/oem", json={"command": "at-lock-vboot"})
    assert resp.status_code == 200
    assert svc.calls == [("oem", "at-lock-vboot")]


def test_oem_command_is_required(monkeypatch):
    """No default: an empty oem request is a mistake, not `oem` with nothing."""
    svc = _install(monkeypatch, _FakeService(FlashResult(ok=True, rc=0)))
    with TestClient(create_app()) as c:
        resp = c.post("/api/flash/oem", json={})
    assert resp.status_code == 422
    assert svc.calls == []


# ── /api/flash/plan and /plan/run ───────────────────────────────────────

_PLAN_MANIFEST = "tool flash boot_a boot.img\ntool erase misc\ntool reboot\n"
_PLAN_CAPS = ["adb", "fastboot"]


class _PlanService:
    def __init__(self, partitions: list[str] | None = None) -> None:
        self.partitions = partitions or []
        self.runs: list = []

    def agent_view(self):
        from alb.remote.flash_plan import AgentView
        from tests.remote.bench_tables import new_agent_ops

        return AgentView(caps=_PLAN_CAPS, partitions=self.partitions, ops=new_agent_ops())

    async def run_plan(self, plan, *, reboot_wait_s, on_event=None):
        from alb.remote.flash_plan import PlanResult

        self.runs.append((plan, reboot_wait_s))
        if on_event:
            step = "line 1: flash boot_a <- boot.img"
            on_event(FlashEvent(phase="step", done=1, total=3, text=step))
        for row in plan.rows:
            row.status = "ok"
        return PlanResult(ok=True, manifest=plan.manifest, rows=plan.rows)


def _workspace_package() -> str:
    from alb.infra.workspace import workspace_root

    pkg = workspace_root() / "images" / "board-x"
    pkg.mkdir(parents=True)
    (pkg / "boot.img").write_bytes(b"B" * 64)
    (pkg / "flash.txt").write_text(_PLAN_MANIFEST, encoding="utf-8")
    return "images/board-x/flash.txt"


def test_plan_returns_rows_digests_and_a_fingerprint(monkeypatch):
    _install(monkeypatch, _PlanService())
    rel = _workspace_package()
    with TestClient(create_app()) as c:
        body = c.post("/api/flash/plan", json={"manifest": rel}).json()
    assert body["ok"] is True, body["problems"]
    assert (body["total"], body["run"], body["skipped"]) == (3, 3, 0)
    assert body["rows"][0]["size"] == 64 and len(body["rows"][0]["md5"]) == 32
    assert len(body["fingerprint"]) == 64
    assert "path" not in body["rows"][0], "the hub's own file path stays on the hub"


def test_plan_reports_problems_with_a_200(monkeypatch):
    """Problems are the answer to "what is wrong with this plan", not a
    failed request — the rows still come back so they can be shown."""
    _install(monkeypatch, _PlanService(partitions=["boot_a"]))
    rel = _workspace_package()
    with TestClient(create_app()) as c:
        body = c.post("/api/flash/plan", json={"manifest": rel, "skip": ["nope"]}).json()
    assert body["ok"] is False
    assert len(body["rows"]) == 3
    joined = " ".join(body["problems"])
    assert "--skip nope matches no row" in joined
    assert "misc (line 2)" in joined


def test_plan_from_line_resumes_and_the_run_must_use_it_too(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = _PlanService()
    _install(monkeypatch, svc)
    rel = _workspace_package()
    with TestClient(create_app()) as c:
        body = c.post("/api/flash/plan", json={"manifest": rel, "from_line": 2}).json()
        assert body["ok"] is True, body["problems"]
        assert [r["skip"] for r in body["rows"]] == ["--from-line 2", "", ""]
        # the shown plan started at line 2: running it from the top is a different plan
        whole = c.post(
            "/api/flash/plan/run", json={"manifest": rel, "fingerprint": body["fingerprint"]}
        )
        assert whole.status_code == 409 and svc.runs == []
        ok = c.post(
            "/api/flash/plan/run",
            json={"manifest": rel, "from_line": 2, "fingerprint": body["fingerprint"]},
        )
    assert ok.status_code == 200 and len(svc.runs) == 1


def test_plan_manifest_must_be_in_the_workspace(monkeypatch):
    _install(monkeypatch, _PlanService())
    with TestClient(create_app()) as c:
        assert c.post("/api/flash/plan", json={"manifest": "../../etc/passwd"}).status_code == 400
        assert c.post("/api/flash/plan", json={"manifest": "not-there.txt"}).status_code == 400


def test_plan_run_needs_the_fingerprint(monkeypatch):
    svc = _install(monkeypatch, _PlanService())
    rel = _workspace_package()
    with TestClient(create_app()) as c:
        resp = c.post("/api/flash/plan/run", json={"manifest": rel})
    assert resp.status_code == 422
    assert svc.runs == []


def test_plan_run_refuses_a_plan_that_changed_since_it_was_shown(monkeypatch):
    svc = _install(monkeypatch, _PlanService())
    rel = _workspace_package()
    with TestClient(create_app()) as c:
        shown = c.post("/api/flash/plan", json={"manifest": rel}).json()
        from alb.infra.workspace import workspace_root

        (workspace_root() / "images" / "board-x" / "boot.img").write_bytes(b"C" * 64)
        resp = c.post(
            "/api/flash/plan/run", json={"manifest": rel, "fingerprint": shown["fingerprint"]}
        )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "FLASH_PLAN_CHANGED"
    assert svc.runs == []


def test_plan_run_refuses_a_plan_with_problems(monkeypatch):
    svc = _install(monkeypatch, _PlanService(partitions=["boot_a"]))
    rel = _workspace_package()
    with TestClient(create_app()) as c:
        shown = c.post("/api/flash/plan", json={"manifest": rel}).json()
        resp = c.post(
            "/api/flash/plan/run", json={"manifest": rel, "fingerprint": shown["fingerprint"]}
        )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "FLASH_PLAN_REJECTED"
    assert any("misc" in p for p in detail["problems"])
    assert svc.runs == []


def test_plan_run_streams_steps_and_ends_with_every_row(monkeypatch):
    svc = _install(monkeypatch, _PlanService())
    rel = _workspace_package()
    with TestClient(create_app()) as c:
        shown = c.post("/api/flash/plan", json={"manifest": rel}).json()
        resp = c.post(
            "/api/flash/plan/run",
            json={"manifest": rel, "fingerprint": shown["fingerprint"], "reboot_wait_s": 30},
        )
    assert resp.status_code == 200
    lines = _lines(resp)
    assert lines[0]["phase"] == "step"
    done = lines[-1]
    assert done["ev"] == "done" and done["ok"] is True
    assert [r["status"] for r in done["rows"]] == ["ok", "ok", "ok"]
    assert svc.runs[0][1] == 30
