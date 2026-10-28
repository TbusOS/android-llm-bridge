"""GET /api/flash/jobs and /api/flash/jobs/{id} -- records read from disk."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alb.api.server import create_app


@pytest.fixture(autouse=True)
def _ephemeral_forward_port(monkeypatch):
    monkeypatch.setenv("ALB_ADB_FORWARD_PORT", "0")


class _Service:
    def __init__(self, job: str = "") -> None:
        self.job = job

    def status(self):
        return {"available": True, "busy": bool(self.job), "job": self.job}


@pytest.fixture
def ws(monkeypatch, tmp_path: Path) -> Path:
    import alb.api.flash_route as route

    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(route, "get_flash_service", lambda: _Service())
    return tmp_path


def _job(
    ws: Path,
    name: str,
    job: dict | None,
    *,
    label: str | None = None,
    op: str | None = None,
    device: str | None = None,
) -> Path:
    base = ws / "devices" / device / "flash" if device else ws / "flash"
    d = base / name
    d.mkdir(parents=True)
    header = {"t": 0.0, "src": "meta", "ev": "start", "wall": "x"}
    if label:
        header["label"] = label
    if op:
        header["op"] = op
    (d / "timeline.jsonl").write_text(json.dumps(header) + "\n")
    if job is not None:
        (d / "job.json").write_text(json.dumps(job))
    return d


PLAN_FAILED = {
    "ok": False,
    "code": "FLASH_FAILED",
    "failed_line": 7,
    "total": 5,
    "rows": [
        {"line": 3, "status": "ok"},
        {"line": 4, "status": "ok"},
        {"line": 5, "status": "skipped"},
        {"line": 7, "status": "failed"},
        {"line": 8, "status": "not-run"},
    ],
}


def _client() -> TestClient:
    return TestClient(create_app())


def test_list_is_newest_first_with_row_counts_and_running(monkeypatch, ws: Path) -> None:
    import alb.api.flash_route as route

    _job(ws, "flash-boot-2026-10-23T09-00-00", {"ok": True, "code": ""}, label="flash boot", op="flash")
    _job(ws, "plan-pkg-2026-10-23T10-00-00", PLAN_FAILED, label="plan pkg", op="plan")
    _job(ws, "plan-pkg-2026-10-23T10-00-00-2", None, label="plan pkg", op="plan")
    monkeypatch.setattr(route, "get_flash_service", lambda: _Service("plan pkg: line 9 (6/7) flash vendor"))
    with _client() as c:
        body = c.get("/api/flash/jobs").json()
    assert body["ok"] is True
    assert body["running"] == "plan pkg: line 9 (6/7) flash vendor"
    ids = [j["id"] for j in body["jobs"]]
    assert ids == [
        "plan-pkg-2026-10-23T10-00-00-2",
        "plan-pkg-2026-10-23T10-00-00",
        "flash-boot-2026-10-23T09-00-00",
    ]
    running_one, plan, single = body["jobs"]
    assert plan == {
        "id": "plan-pkg-2026-10-23T10-00-00",
        "op": "plan",
        "label": "plan pkg",
        "started_at": "2026-10-23T10:00:00+00:00",
        "ok": False,
        "code": "FLASH_FAILED",
        "failed_line": 7,
        "rows": {"total": 5, "ok": 2, "failed": 1, "skipped": 1, "not_run": 1},
    }
    assert single["ok"] is True and single["rows"] == {
        "total": 1, "ok": 1, "failed": 0, "skipped": 0, "not_run": 0
    }
    assert running_one["ok"] is None  # no job.json yet


def test_idle_hub_reports_running_null_and_limit_applies(ws: Path) -> None:
    for i in range(3):
        _job(ws, f"flash-x-2026-10-23T09-00-0{i}", {"ok": True})
    with _client() as c:
        body = c.get("/api/flash/jobs?limit=2").json()
    assert body["running"] is None
    assert len(body["jobs"]) == 2


def test_empty_workspace_lists_nothing(ws: Path) -> None:
    with _client() as c:
        body = c.get("/api/flash/jobs").json()
    assert body["jobs"] == [] and body["running"] is None


def test_per_device_records_get_a_prefixed_id(ws: Path) -> None:
    _job(ws, "flash-boot-2026-10-23T11-00-00", {"ok": True}, device="board-a")
    with _client() as c:
        jobs = c.get("/api/flash/jobs").json()["jobs"]
        detail = c.get("/api/flash/jobs/board-a~flash-boot-2026-10-23T11-00-00").json()
    assert jobs[0]["id"] == "board-a~flash-boot-2026-10-23T11-00-00"
    assert detail["ok"] is True and detail["job"] == {"ok": True}


def test_detail_returns_job_json_and_timeline_name(ws: Path) -> None:
    _job(ws, "plan-pkg-2026-10-23T10-00-00", PLAN_FAILED, label="plan pkg", op="plan")
    with _client() as c:
        body = c.get("/api/flash/jobs/plan-pkg-2026-10-23T10-00-00").json()
    assert body["ok"] is False  # the job's verdict, as in the list
    assert body["job"] == PLAN_FAILED
    assert body["timeline"] == "timeline.jsonl"
    assert body["timeline_path"] == "flash/plan-pkg-2026-10-23T10-00-00/timeline.jsonl"
    assert body["rows"]["failed"] == 1


def test_detail_of_a_job_without_a_verdict(ws: Path) -> None:
    _job(ws, "plan-pkg-2026-10-23T10-00-00", None)
    with _client() as c:
        body = c.get("/api/flash/jobs/plan-pkg-2026-10-23T10-00-00").json()
    assert body["job"] is None and body["ok"] is None


@pytest.mark.parametrize("job_id", ["..", "a..b/c", "x%2Fy", "plan-pkg", ".hidden", "a~b~c"])
def test_bad_or_unknown_ids_never_reach_outside(ws: Path, job_id: str) -> None:
    (ws / "secret-2026-10-23T10-00-00").mkdir()
    with _client() as c:
        r = c.get(f"/api/flash/jobs/{job_id}")
    assert r.status_code in (400, 404)


def test_traversal_in_id_is_refused(ws: Path) -> None:
    with _client() as c:
        r = c.get("/api/flash/jobs/..~plan-pkg-2026-10-23T10-00-00")
    assert r.status_code == 400


def test_symlinked_job_dir_is_ignored(ws: Path) -> None:
    real = _job(ws, "flash-a-2026-10-23T09-00-00", {"ok": True})
    (ws / "flash" / "flash-b-2026-10-23T09-00-01").symlink_to(real, target_is_directory=True)
    with _client() as c:
        ids = [j["id"] for j in c.get("/api/flash/jobs").json()["jobs"]]
        r = c.get("/api/flash/jobs/flash-b-2026-10-23T09-00-01")
    assert ids == ["flash-a-2026-10-23T09-00-00"]
    assert r.status_code == 404
