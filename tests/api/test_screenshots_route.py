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
