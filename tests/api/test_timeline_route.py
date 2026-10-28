"""/api/timeline/* -- capture stream, listing, lines, density, boot.

The capture runs the real `capture_timeline` against fake transports (byte
streams that yield a few lines and then stay quiet), so what is checked is
the route's contract: NDJSON shape, the reboot being issued after the
capture started, and what lands on disk. Read endpoints are checked against
hand-written `timeline.jsonl` files.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from alb.api import timeline_route
from alb.api.server import create_app
from alb.transport.base import PermissionResult, ShellResult

SERIAL = "0123456789ABCDEF"
CLOCK = "10-23 12:00:00.000\n"
STAMP_CMD = "date '+%m-%d %H:%M:%S.%3N'"


def _ndjson(resp) -> list[dict[str, Any]]:
    return [json.loads(x) for x in resp.text.splitlines() if x.strip()]


def _adb(lines: dict[str, list[bytes]], *, reboot_ok: bool = True) -> AsyncMock:
    t = AsyncMock()
    t.name = "adb"
    t.check_permissions = AsyncMock(return_value=PermissionResult(behavior="allow"))
    answers = {STAMP_CMD: CLOCK, "dmesg | tail -n 8": "", "dmesg | wc -l": "0\n"}
    calls: list[tuple[str, dict]] = []

    async def shell(cmd: str, **_kw) -> ShellResult:
        out = answers.get(cmd)
        if out is None:
            return ShellResult(ok=False, exit_code=1, stderr="no")
        return ShellResult(ok=True, stdout=out)

    def stream_read(source: str, **kw) -> AsyncIterator[bytes]:
        calls.append((source, kw))

        async def gen() -> AsyncIterator[bytes]:
            for line in lines.get(source, []):
                await asyncio.sleep(0.01)
                yield line
            await asyncio.sleep(3600)

        return gen()

    t.shell = shell
    t.stream_read = stream_read
    t.calls = calls
    t.reboot = AsyncMock(
        return_value=ShellResult(ok=reboot_ok, error_code=None if reboot_ok else "DEVICE_NOT_FOUND")
    )
    return t


def _serial(lines: list[bytes]) -> AsyncMock:
    s = AsyncMock()
    s.name = "serial"

    def stream_read(_source: str, **_kw) -> AsyncIterator[bytes]:
        async def gen() -> AsyncIterator[bytes]:
            for line in lines:
                await asyncio.sleep(0.01)
                yield line
            await asyncio.sleep(3600)

        return gen()

    s.stream_read = stream_read
    s.reboot = AsyncMock(return_value=ShellResult(ok=True))
    return s


@pytest.fixture
def ws(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(timeline_route, "_PROGRESS_EVERY_S", 0.2)
    monkeypatch.setattr(timeline_route, "_REBOOT_DELAY_S", 0.1)
    return tmp_path


def _install(monkeypatch, adb: AsyncMock | None, serial: AsyncMock | None) -> None:
    def build(*, override: str | None = None, device_serial: str | None = None):
        t = serial if override == "serial" else adb
        if t is None:
            raise RuntimeError("no such transport")
        return t

    monkeypatch.setattr(timeline_route, "build_transport", build)


def _client() -> TestClient:
    return TestClient(create_app())


# ─── capture ───────────────────────────────────────────────────────
def test_capture_streams_progress_then_done_and_writes_the_files(monkeypatch, ws) -> None:
    adb = _adb(
        {
            "logcat": [b"10-23 12:00:01.000  1  1 I Tag: hello\n"],
            "dmesg": [b"[    1.000000] kernel line\n"],
        }
    )
    serial = _serial([b"Starting kernel ...\n"])
    _install(monkeypatch, adb, serial)
    with _client() as c:
        resp = c.post(f"/api/timeline/capture?device={SERIAL}", json={"duration_s": 1})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")
    evs = _ndjson(resp)
    assert evs[-1]["ev"] == "done"
    assert all(e["ev"] == "progress" for e in evs[:-1]) and len(evs) >= 2
    first = evs[0]
    assert set(first) == {"ev", "elapsed_s", "lines"}
    assert set(first["lines"]) == {"logcat", "dmesg", "uart"}
    done = evs[-1]
    assert done["ok"] is True
    data = done["data"]
    assert data["name"].startswith("capture-")
    assert data["sources"] == {
        "logcat": {"lines": 1, "missing": False},
        "dmesg": {"lines": 1, "missing": False},
        "uart": {"lines": 1, "missing": False},
    }
    assert 0.9 <= data["duration_s"] < 3
    # The last progress line counted what the capture had written.
    assert max(e["lines"]["uart"] for e in evs if e["ev"] == "progress") == 1
    capdir = ws / "devices" / SERIAL / "timeline" / data["name"]
    assert (capdir / "timeline.jsonl").is_file() and (capdir / "summary.json").is_file()
    # Default buffers include events (boot_progress_* tags live there).
    assert ("logcat", {"filter": None, "tail": 1, "buffers": ["main", "system", "crash", "events"]}) in adb.calls


def test_capture_with_reboot_issues_it_after_start_and_reports_it(monkeypatch, ws) -> None:
    adb = _adb({"logcat": [b"x\n"]})
    _install(monkeypatch, adb, _serial([]))
    with _client() as c:
        resp = c.post(
            f"/api/timeline/capture?device={SERIAL}",
            json={"duration_s": 1, "reboot": "bootloader", "sources": ["logcat", "uart"]},
        )
    done = _ndjson(resp)[-1]
    adb.reboot.assert_awaited_once_with("bootloader")
    assert done["reboot"]["ok"] is True
    assert done["reboot"]["mode"] == "bootloader"
    assert 0.05 <= done["reboot"]["at_s"] < 1
    assert set(done["data"]["sources"]) == {"logcat", "uart"}


def test_capture_reboot_system_maps_to_normal(monkeypatch, ws) -> None:
    adb = _adb({})
    _install(monkeypatch, adb, _serial([]))
    with _client() as c:
        resp = c.post(
            f"/api/timeline/capture?device={SERIAL}",
            json={"duration_s": 1, "reboot": "system", "sources": ["uart"]},
        )
    assert _ndjson(resp)[-1]["reboot"]["ok"] is True
    adb.reboot.assert_awaited_once_with("normal")


def test_reboot_falls_back_to_the_serial_console_when_adb_is_gone(monkeypatch, ws) -> None:
    adb = _adb({}, reboot_ok=False)
    serial = _serial([])
    _install(monkeypatch, adb, serial)
    with _client() as c:
        resp = c.post(
            f"/api/timeline/capture?device={SERIAL}",
            json={"duration_s": 1, "reboot": "system", "sources": ["uart"]},
        )
    done = _ndjson(resp)[-1]
    serial.reboot.assert_awaited_once_with("normal")
    assert done["reboot"]["ok"] is True
    assert done["reboot"]["data"]["fallback"]["error_code"] == "DEVICE_NOT_FOUND"


def test_a_source_that_cannot_be_read_is_marked_missing(monkeypatch, ws) -> None:
    _install(monkeypatch, _adb({"logcat": [b"x\n"]}), None)  # no serial transport
    with _client() as c:
        resp = c.post(f"/api/timeline/capture?device={SERIAL}", json={"duration_s": 1, "sources": ["logcat", "uart"]})
    sources = _ndjson(resp)[-1]["data"]["sources"]
    assert sources["logcat"] == {"lines": 1, "missing": False}
    assert sources["uart"] == {"lines": 0, "missing": True}


def test_nothing_readable_is_an_error_envelope_that_still_names_the_capture(monkeypatch, ws) -> None:
    _install(monkeypatch, _adb({}), None)
    with _client() as c:
        resp = c.post(f"/api/timeline/capture?device={SERIAL}", json={"duration_s": 1, "sources": ["uart"]})
    done = _ndjson(resp)[-1]
    assert done["ok"] is False
    assert done["error"]["code"] == "TIMELINE_NO_SOURCE"
    assert done["name"].startswith("capture-")


def test_two_captures_in_one_second_get_two_directories(ws) -> None:
    a = timeline_route._new_capture_dir(SERIAL)
    b = timeline_route._new_capture_dir(SERIAL)
    assert a != b and a.is_dir() and b.is_dir()
    assert timeline_route._NAME_RE.match(a.name) and timeline_route._NAME_RE.match(b.name)


def test_transport_init_failure_is_an_envelope_not_a_stream(monkeypatch, ws) -> None:
    _install(monkeypatch, None, None)
    with _client() as c:
        resp = c.post(f"/api/timeline/capture?device={SERIAL}", json={"duration_s": 5})
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json()["error"]["code"] == "TRANSPORT_INIT_FAILED"


@pytest.mark.parametrize(
    "body",
    [
        {"duration_s": 0},
        {"duration_s": 601},
        {"duration_s": 5, "sources": ["gps"]},
        {"duration_s": 5, "sources": []},
        {"duration_s": 5, "reboot": "recovery"},
        {"duration_s": 5, "logcat_buffers": ["main; rm -rf /"]},
    ],
)
def test_bad_capture_bodies_are_rejected(ws, body) -> None:
    with _client() as c:
        assert c.post(f"/api/timeline/capture?device={SERIAL}", json=body).status_code == 422


def test_unsafe_device_is_rejected_without_echo(ws) -> None:
    with _client() as c:
        resp = c.post("/api/timeline/capture?device=../etc", json={"duration_s": 5})
    assert resp.status_code == 400
    assert "etc" not in resp.text


# ─── read endpoints ────────────────────────────────────────────────
def _make_capture(
    ws: Path, name: str, records: list[dict], *, summary: dict | None = None, device: str = SERIAL
) -> Path:
    d = ws / "devices" / device / "timeline" / name
    d.mkdir(parents=True)
    meta = {"t": 0.0, "wall": "2026-10-23T12:00:00.000+00:00", "src": "meta", "ev": "start"}
    with (d / "timeline.jsonl").open("w") as f:
        for r in [meta, *records]:
            f.write(json.dumps(r) + "\n")
    (d / "summary.json").write_text(
        json.dumps(summary or {"lines": {}, "duration_captured_ms": 0})
    )
    return d


def _r(t: float, src: str, line: str, **extra) -> dict:
    return {"t": t, "wall": f"2026-10-23T12:00:{int(t):02d}.000+00:00", "src": src, "line": line, **extra}


BOOT_RECS = [
    _r(1.0, "uart", "Starting kernel ..."),
    _r(1.5, "dmesg", "[    0.000000] Booting Linux"),
    _r(2.5, "dmesg", "[    1.000000] Run /init as init process"),
    _r(5.0, "logcat", "10-23 12:00:05.000  1000  1000 I boot_progress_start: 5000"),
    _r(9.0, "logcat", "10-23 12:00:09.000  1000  1000 I boot_progress_enable_screen: 9000"),
]
BOOT_SUMMARY = {"lines": {"logcat": 2, "dmesg": 2, "uart": 1}, "duration_captured_ms": 12345}


def test_list_captures_newest_first_with_shape(ws) -> None:
    _make_capture(ws, "capture-2026-10-23T12-00-00", BOOT_RECS, summary=BOOT_SUMMARY)
    _make_capture(
        ws,
        "capture-2026-10-23T13-00-00",
        [_r(1.0, "logcat", "10-23 13:00:01.000  1  1 I x: y")],
        summary={"lines": {"logcat": 1}, "duration_captured_ms": 2000},
    )
    (ws / "devices" / SERIAL / "timeline" / "not-a-capture").mkdir()
    with _client() as c:
        body = c.get(f"/api/timeline/captures?device={SERIAL}").json()
    assert body["ok"] is True
    names = [x["name"] for x in body["data"]]
    assert names == ["capture-2026-10-23T13-00-00", "capture-2026-10-23T12-00-00"]
    old = body["data"][1]
    assert old["started_at"] == "2026-10-23T12:00:00.000+00:00"
    assert old["duration_s"] == 12.345
    assert old["sources"] == {"logcat": 2, "dmesg": 2, "uart": 1}
    assert old["has_boot"] is True and body["data"][0]["has_boot"] is False
    assert old["size_bytes"] > 0


def test_list_respects_limit_and_other_devices_are_separate(ws) -> None:
    for i in range(3):
        _make_capture(ws, f"capture-2026-10-23T12-00-0{i}", [])
    _make_capture(ws, "capture-2026-10-23T12-00-09", [], device="OTHER1")
    with _client() as c:
        assert len(c.get(f"/api/timeline/captures?device={SERIAL}&limit=2").json()["data"]) == 2
        other = c.get("/api/timeline/captures?device=OTHER1").json()["data"]
    assert [x["name"] for x in other] == ["capture-2026-10-23T12-00-09"]


def test_lines_endpoint_parses_logcat_and_filters(ws) -> None:
    recs = [
        _r(0.5, "uart", "console hello"),
        _r(1.0, "logcat", "10-23 12:00:01.000  4321  4321 E MyTag: it broke"),
        _r(2.0, "dmesg", "[    2.0] kernel thing"),
        _r(3.0, "logcat", "plain logcat line"),
    ]
    _make_capture(ws, "capture-2026-10-23T12-00-00", recs)
    base = f"/api/timeline/captures/capture-2026-10-23T12-00-00/lines?device={SERIAL}"
    with _client() as c:
        allv = c.get(base).json()["data"]
        only = c.get(base + "&sources=logcat").json()["data"]["lines"]
        grep = c.get(base + "&grep=broke").json()["data"]["lines"]
        window = c.get(base + "&t0=0.9&t1=2.0").json()["data"]["lines"]
        paged = c.get(base + "&limit=2&offset=1").json()["data"]
    assert allv["truncated"] is False and len(allv["lines"]) == 4
    assert allv["lines"][0] == {
        "t": 0.5,
        "ts": "2026-10-23T12:00:00.000+00:00",
        "src": "uart",
        "text": "console hello",
        "level": None,
        "tag": None,
        "pid": None,
    }
    assert only[0]["text"].endswith("it broke")
    assert only[0]["level"] == "E" and only[0]["tag"] == "MyTag" and only[0]["pid"] == 4321
    assert only[1]["level"] is None
    assert [x["src"] for x in grep] == ["logcat"]
    assert [x["t"] for x in window] == [1.0, 2.0]
    assert [x["t"] for x in paged["lines"]] == [1.0, 2.0] and paged["truncated"] is True


def test_lines_rejects_unknown_sources(ws) -> None:
    _make_capture(ws, "capture-2026-10-23T12-00-00", [])
    with _client() as c:
        r = c.get(f"/api/timeline/captures/capture-2026-10-23T12-00-00/lines?device={SERIAL}&sources=gps")
    assert r.status_code == 400


def test_density_endpoint(ws) -> None:
    _make_capture(
        ws,
        "capture-2026-10-23T12-00-00",
        [
            _r(0.1, "uart", "a"),
            _r(1.2, "logcat", "10-23 12:00:01.000  1  1 W T: w"),
            _r(1.6, "dmesg", "Kernel panic - not syncing: boom"),
        ],
    )
    with _client() as c:
        data = c.get(
            f"/api/timeline/captures/capture-2026-10-23T12-00-00/density?device={SERIAL}&bucket_s=1"
        ).json()["data"]
    assert data["t0"] == 0.0 and data["t1"] == 1.6 and data["bucket_s"] == 1.0
    assert data["tracks"]["uart"] == [1, 0]
    assert data["tracks"]["dmesg"] == [0, 1]
    assert data["tracks"]["logcat"]["W"] == [0, 1]
    assert data["markers"] == [
        {"t": 1.6, "src": "dmesg", "kind": "panic", "text": "Kernel panic - not syncing: boom"}
    ]


def test_boot_endpoint_with_previous_boot(ws) -> None:
    older = [
        _r(1.0, "uart", "Starting kernel ..."),
        _r(2.0, "dmesg", "[ 1.0] Run /init as init process"),
    ]
    _make_capture(ws, "capture-2026-10-22T09-00-00", older)
    _make_capture(ws, "capture-2026-10-22T10-00-00", [_r(1.0, "dmesg", "[ 99.0] no boot here")])
    _make_capture(ws, "capture-2026-10-23T12-00-00", BOOT_RECS)
    with _client() as c:
        data = c.get(
            f"/api/timeline/captures/capture-2026-10-23T12-00-00/boot?device={SERIAL}"
        ).json()["data"]
        first = c.get(
            f"/api/timeline/captures/capture-2026-10-22T09-00-00/boot?device={SERIAL}"
        ).json()["data"]
    assert data["found"] is True
    assert data["total_s"] == 8.0  # 1.0 (handoff) .. 9.0 (enable_screen)
    assert [m["key"] for m in data["milestones"]] == [
        "uart_handoff",
        "dmesg_first",
        "init_start",
        "boot_progress_start",
        "boot_progress_enable_screen",
    ]
    assert {s["key"] for s in data["stages"]} >= {"kernel", "userspace_init"}
    # The 10-22 10:00 capture has no boot, so the previous boot is the 09:00 one.
    assert data["previous"]["name"] == "capture-2026-10-22T09-00-00"
    assert data["previous"]["total_s"] == 1.0
    assert [m["key"] for m in data["previous"]["milestones"]] == ["uart_handoff", "dmesg_first", "init_start"]
    assert first["previous"] is None


def test_boot_of_a_capture_without_a_boot(ws) -> None:
    _make_capture(ws, "capture-2026-10-23T12-00-00", [_r(1.0, "logcat", "10-23 12:00:01.000 1 1 I a: b")])
    with _client() as c:
        data = c.get(f"/api/timeline/captures/capture-2026-10-23T12-00-00/boot?device={SERIAL}").json()["data"]
    assert data == {"found": False, "total_s": 0.0, "milestones": [], "stages": [], "previous": None}


@pytest.mark.parametrize("name", ["..", "capture-x", "capture-2026-10-23T12-00-00/../x", "%2e%2e", "timeline.jsonl"])
def test_bad_capture_names_are_refused(ws, name) -> None:
    with _client() as c:
        r = c.get(f"/api/timeline/captures/{name}/lines?device={SERIAL}")
    assert r.status_code in (400, 404)


def test_unknown_capture_is_404_and_bad_device_is_400(ws) -> None:
    with _client() as c:
        assert c.get(f"/api/timeline/captures/capture-2026-10-23T12-00-00/boot?device={SERIAL}").status_code == 404
        r = c.get("/api/timeline/captures?device=../..")
    assert r.status_code == 400 and ".." not in r.text


def test_symlinked_capture_is_not_followed(ws) -> None:
    real = _make_capture(ws, "capture-2026-10-23T12-00-00", [])
    link = real.parent / "capture-2026-10-23T12-00-01"
    link.symlink_to(real, target_is_directory=True)
    with _client() as c:
        assert c.get(f"/api/timeline/captures/{link.name}/lines?device={SERIAL}").status_code == 404
        names = [x["name"] for x in c.get(f"/api/timeline/captures?device={SERIAL}").json()["data"]]
    assert link.name not in names
