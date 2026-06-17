"""Tests for /devices/{serial}/screenshots* endpoints (functional LOW-1)."""

from __future__ import annotations

import struct
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alb.api.server import create_app


SERIAL = "test-serial"


def _make_png(width: int = 64, height: int = 48) -> bytes:
    """Forge a minimal but parseable PNG: signature + IHDR header.

    The route only reads the first 24 bytes for dim parsing, so we
    don't need a real image — just signature + IHDR + size fields.
    Body bytes after [24:] are ignored by `_peek_png_dims`.
    """
    sig = b"\x89PNG\r\n\x1a\n"
    # IHDR chunk: 13 bytes data length (big-endian) + "IHDR" + width(4) +
    # height(4) + the rest doesn't matter for dim parsing
    ihdr_len = struct.pack(">I", 13)
    ihdr_type = b"IHDR"
    dims = struct.pack(">II", width, height)
    return sig + ihdr_len + ihdr_type + dims + b"\x00" * 5 + b"\x00" * 4


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Point ALB_WORKSPACE at a tmp dir so screenshots_dir resolves there."""
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def client(workspace):
    app = create_app()
    with TestClient(app) as c:
        yield c


def _shots_dir(workspace: Path) -> Path:
    d = workspace / "devices" / SERIAL / "screenshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_list_empty_when_no_dir(client) -> None:
    """No captures → ok=true + empty list (not 404)."""
    r = client.get(f"/devices/{SERIAL}/screenshots")
    assert r.status_code == 200
    body = r.json()
    assert body == {"ok": True, "serial": SERIAL, "screenshots": []}


def test_list_returns_newest_first_with_dims(client, workspace) -> None:
    d = _shots_dir(workspace)
    older = d / "2026-05-01T10-00-00.png"
    newer = d / "2026-05-01T11-00-00.png"
    older.write_bytes(_make_png(width=320, height=240))
    newer.write_bytes(_make_png(width=1920, height=1080))
    # Force mtimes so order is deterministic regardless of FS rounding.
    now = time.time()
    older.touch()
    import os
    os.utime(older, (now - 60, now - 60))
    os.utime(newer, (now, now))

    r = client.get(f"/devices/{SERIAL}/screenshots")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    names = [e["name"] for e in body["screenshots"]]
    assert names == [newer.name, older.name]
    # First entry is newer, must report 1920×1080.
    assert body["screenshots"][0]["width"] == 1920
    assert body["screenshots"][0]["height"] == 1080
    assert body["screenshots"][1]["width"] == 320
    assert body["screenshots"][1]["height"] == 240


def test_list_skips_unrelated_files(client, workspace) -> None:
    d = _shots_dir(workspace)
    (d / "shot.png").write_bytes(_make_png())
    (d / "notes.txt").write_text("not a screenshot")
    (d / "thumb.jpg").write_bytes(b"\xff\xd8\xff")

    r = client.get(f"/devices/{SERIAL}/screenshots")
    body = r.json()
    names = [e["name"] for e in body["screenshots"]]
    assert names == ["shot.png"]


def test_list_skips_symlinks(client, workspace) -> None:
    """SEC-3: a symlink in the screenshots dir must not be stat()'d (would
    leak size/mtime of a file outside the workspace)."""
    d = _shots_dir(workspace)
    (d / "real.png").write_bytes(_make_png())
    outside = workspace / "outside-secret.png"
    outside.write_bytes(_make_png(width=1234, height=5678))
    (d / "link.png").symlink_to(outside)

    r = client.get(f"/devices/{SERIAL}/screenshots")
    names = [e["name"] for e in r.json()["screenshots"]]
    assert "real.png" in names
    assert "link.png" not in names


def test_list_dims_none_for_corrupt_png(client, workspace) -> None:
    """A truncated/non-PNG file with .png suffix → entry kept but dims null."""
    d = _shots_dir(workspace)
    (d / "broken.png").write_bytes(b"not actually a png")

    r = client.get(f"/devices/{SERIAL}/screenshots")
    body = r.json()
    assert len(body["screenshots"]) == 1
    assert body["screenshots"][0]["name"] == "broken.png"
    assert body["screenshots"][0]["width"] is None
    assert body["screenshots"][0]["height"] is None


def test_read_returns_png_bytes(client, workspace) -> None:
    d = _shots_dir(workspace)
    png = _make_png(width=100, height=50)
    (d / "shot.png").write_bytes(png)

    r = client.get(f"/devices/{SERIAL}/screenshots/shot.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_read_404_when_missing(client) -> None:
    r = client.get(f"/devices/{SERIAL}/screenshots/missing.png")
    assert r.status_code == 404


def test_read_rejects_path_traversal(client) -> None:
    r = client.get(f"/devices/{SERIAL}/screenshots/..%2Fpasswd.png")
    # FastAPI's path matcher rejects %2F in `{name}` (not `{name:path}`),
    # so a 404 from the router is acceptable; we check we don't 200.
    assert r.status_code in (400, 404)


def test_read_rejects_non_png_suffix(client, workspace) -> None:
    d = _shots_dir(workspace)
    (d / "notes.txt").write_text("hello")

    r = client.get(f"/devices/{SERIAL}/screenshots/notes.txt")
    assert r.status_code == 400


def test_read_screenshot_resolve_runs_in_worker_thread_l033(
    monkeypatch, client, workspace
) -> None:
    """L-033: read_screenshot must offload _safe_resolve_screenshot to
    a worker thread (sync stat / resolve / symlink check inside).

    Locks the part 141 sweep.
    """
    # Plant a real PNG so the resolve actually fires
    d = _shots_dir(workspace)
    png = d / "lock.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    seen: list[str] = []
    import asyncio as _asyncio

    real_to_thread = _asyncio.to_thread

    async def tracking_to_thread(fn, /, *args, **kwargs):
        seen.append(getattr(fn, "__name__", repr(fn)))
        return await real_to_thread(fn, *args, **kwargs)

    monkeypatch.setattr(
        "alb.api.screenshots_route.asyncio.to_thread", tracking_to_thread
    )
    r = client.get(f"/devices/{SERIAL}/screenshots/lock.png")
    assert r.status_code == 200
    assert "_safe_resolve_screenshot" in seen


def test_serial_path_traversal_400(client, workspace) -> None:
    """L-035 root-layer reject: `serial=../etc` must 400, not silently
    list files outside the device dir.

    Before part 138 the bug worked like this: `_screenshots_dir(serial)`
    built `<root>/devices/../etc/screenshots`. `resolve_under` later
    called `base.resolve()` which flattened the `..`, so any file in
    `<root>/etc/screenshots` would `relative_to(base.resolve())`
    successfully — escape.
    """
    # Plant a screenshot in the escaped target so we'd see it if the
    # bug were unfixed (would 200 with the file listed).
    escaped = workspace / "etc" / "screenshots"
    escaped.mkdir(parents=True)
    (escaped / "leaked.png").write_bytes(
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    )

    # FastAPI / Starlette URL-decodes the path param; for safety we try
    # both the percent-encoded and the literal form.
    for serial in ("..%2Fetc", "../etc"):
        r = client.get(f"/devices/{serial}/screenshots")
        # If routing matched our endpoint, we expect 400 (validation);
        # if Starlette refused to route, 404 is also fine — neither
        # should be a successful list of the escaped target.
        assert r.status_code in (400, 404), (
            f"serial={serial!r}: expected 400/404, got {r.status_code} "
            f"body={r.text}"
        )
        if r.status_code == 200:
            assert "leaked.png" not in r.text


def test_read_rejects_symlink(client, workspace) -> None:
    """Code-review 2026-05-07 HIGH: a symlink inside the screenshots
    dir pointing to /etc/* (or any out-of-tree path) used to be served
    as image/png. Reject symlinks outright."""
    d = _shots_dir(workspace)
    target = workspace / "secret.txt"
    target.write_text("workspace-internal but not a screenshot")
    link = d / "evil.png"
    link.symlink_to(target)

    r = client.get(f"/devices/{SERIAL}/screenshots/evil.png")
    assert r.status_code == 400
    assert "symlink" in r.json()["detail"].lower()


def test_read_rejects_symlink_outside_workspace(client, workspace, tmp_path) -> None:
    """Even more direct: link points to /etc/hostname-style file
    outside the workspace. Pre-fix: 200 + plaintext leak. Post-fix: 400."""
    d = _shots_dir(workspace)
    outside = tmp_path / "outside-of-workspace.bin"
    outside.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    link = d / "leak.png"
    link.symlink_to(outside)

    r = client.get(f"/devices/{SERIAL}/screenshots/leak.png")
    assert r.status_code == 400


def test_endpoints_listed_in_schema(client) -> None:
    body = client.get("/api/version").json()
    paths = [(e["method"], e["path"]) for e in body["rest"]]
    assert ("GET", "/devices/{serial}/screenshots") in paths
    assert ("GET", "/devices/{serial}/screenshots/{name}") in paths
    assert ("DELETE", "/devices/{serial}/screenshots/{name}") in paths


def test_delete_removes_file_and_is_idempotent(client, workspace) -> None:
    """DELETE removes the file and returns removed=True; a second
    delete returns ok=true / removed=False (idempotent · UI can swallow
    stale double-clicks)."""
    d = _shots_dir(workspace)
    (d / "shot.png").write_bytes(_make_png(width=10, height=10))

    r1 = client.delete(f"/devices/{SERIAL}/screenshots/shot.png")
    assert r1.status_code == 200
    body1 = r1.json()
    assert body1["ok"] is True
    assert body1["removed"] is True
    assert not (d / "shot.png").exists()

    r2 = client.delete(f"/devices/{SERIAL}/screenshots/shot.png")
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["ok"] is True
    assert body2["removed"] is False


def test_delete_rejects_non_png_suffix(client, workspace) -> None:
    d = _shots_dir(workspace)
    (d / "notes.txt").write_text("hi")
    r = client.delete(f"/devices/{SERIAL}/screenshots/notes.txt")
    assert r.status_code == 400


def test_delete_rejects_symlink_inside_workspace(client, workspace) -> None:
    d = _shots_dir(workspace)
    secret = workspace / "secret.txt"
    secret.write_text("not yours")
    link = d / "evil.png"
    link.symlink_to(secret)
    r = client.delete(f"/devices/{SERIAL}/screenshots/evil.png")
    # _safe_resolve_screenshot refuses symlinks via resolve_under;
    # the linked file should remain.
    assert r.status_code == 400
    assert secret.exists()
