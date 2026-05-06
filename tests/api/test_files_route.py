"""Tests for /devices/{serial}/files + /workspace/files (DEBT-022 PR-H)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from alb.api.server import create_app
from alb.transport.base import ShellResult, Transport


# ─── Fake transport ──────────────────────────────────────────────────
class _FakeAdbTransport(Transport):
    """Records shell calls + lets tests inject canned responses."""

    name = "adb"

    def __init__(self) -> None:
        self.shell_calls: list[str] = []
        self.shell_response: ShellResult = ShellResult(ok=True, stdout="", stderr="")
        self.push_calls: list[tuple[Path, str]] = []
        self.push_response: ShellResult = ShellResult(ok=True, duration_ms=42)
        self.pull_calls: list[tuple[str, Path]] = []
        self.pull_response: ShellResult = ShellResult(ok=True, duration_ms=33)
        # MID-6 streaming: tests inject a list of TransferEvent that
        # push_stream / pull_stream will yield. Callers also get a
        # `stream_was_aclose`d flag to assert cancel cleanup.
        self.stream_events: list[Any] = []
        self.stream_delay_s: float = 0.0
        self.stream_was_aclosed: bool = False

    async def shell(self, cmd: str, *, timeout: int = 30) -> ShellResult:
        self.shell_calls.append(cmd)
        return self.shell_response

    async def stream_read(self, source: str, **kwargs: Any):  # noqa: ANN001
        if False:
            yield b""

    async def push(self, local: Path, remote: str) -> ShellResult:
        self.push_calls.append((local, remote))
        return self.push_response

    async def pull(self, remote: str, local: Path) -> ShellResult:
        self.pull_calls.append((remote, local))
        # Real adb writes to local; mimic so the route can read mtime if it wants.
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(b"pulled bytes\n")
        return self.pull_response

    async def push_stream(self, local: Path, remote: str):  # noqa: ANN201
        self.push_calls.append((local, remote))
        try:
            for ev in self.stream_events:
                if self.stream_delay_s > 0:
                    import asyncio as _a
                    await _a.sleep(self.stream_delay_s)
                yield ev
        finally:
            self.stream_was_aclosed = True

    async def pull_stream(self, remote: str, local: Path):  # noqa: ANN201
        self.pull_calls.append((remote, local))
        local.parent.mkdir(parents=True, exist_ok=True)
        try:
            for ev in self.stream_events:
                if self.stream_delay_s > 0:
                    import asyncio as _a
                    await _a.sleep(self.stream_delay_s)
                yield ev
        finally:
            self.stream_was_aclosed = True

    async def reboot(self, mode: str = "normal") -> ShellResult:
        return ShellResult(ok=True)

    async def health(self) -> dict[str, Any]:
        return {"ok": True}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("ALB_WORKSPACE", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def fake_transport() -> _FakeAdbTransport:
    return _FakeAdbTransport()


@pytest.fixture
def client(workspace, fake_transport, monkeypatch):
    monkeypatch.setattr(
        "alb.api.files_route.build_transport",
        lambda **kwargs: fake_transport,
    )
    app = create_app()
    with TestClient(app) as c:
        yield c


# ─── GET /devices/{serial}/files ────────────────────────────────────
_LS_SAMPLE = (
    "total 28\n"
    "drwxrwx--x  2 root sdcard_rw 4096 2026-04-30 12:34 Download\n"
    "-rw-rw---- 1 root sdcard_rw  123 2026-04-30 12:35 hello.txt\n"
    "lrwxrwxrwx 1 root root        11 2026-04-30 12:36 self -> /sdcard/\n"
    "drwx------ 2 root root      4096 2026-04-30 12:00 Android\n"
)


def test_device_files_lists_parsed_entries(client, fake_transport) -> None:
    fake_transport.shell_response = ShellResult(ok=True, stdout=_LS_SAMPLE, stderr="")
    body = client.get("/devices/SERIAL01/files", params={"path": "/sdcard/"}).json()
    assert body["ok"] is True
    assert body["serial"] == "SERIAL01"
    assert body["path"] == "/sdcard/"
    last = fake_transport.shell_calls[-1]
    assert last.startswith("ls -la ")
    assert "/sdcard/" in last

    names = [e["name"] for e in body["entries"]]
    assert names == ["Android", "Download", "hello.txt", "self"]
    by_name = {e["name"]: e for e in body["entries"]}
    assert by_name["Download"]["is_dir"] is True
    assert by_name["hello.txt"]["is_dir"] is False
    assert by_name["hello.txt"]["size"] == 123
    assert by_name["self"]["is_link"] is True
    assert by_name["self"]["link_target"] == "/sdcard/"
    assert body["truncated"] is False


def test_device_files_quotes_paths_with_spaces(client, fake_transport) -> None:
    fake_transport.shell_response = ShellResult(ok=True, stdout="", stderr="")
    client.get(
        "/devices/SERIAL01/files", params={"path": "/sdcard/My Folder/"}
    )
    last = fake_transport.shell_calls[-1]
    # shlex.quote → single-quoted because of the space.
    assert "'/sdcard/My Folder/'" in last


def test_device_files_rejects_relative_path(client) -> None:
    body = client.get(
        "/devices/SERIAL01/files", params={"path": "sdcard"}
    ).json()
    assert body["ok"] is False
    assert "invalid" in body["error"]


def test_device_files_shell_failure_returns_inline(client, fake_transport) -> None:
    fake_transport.shell_response = ShellResult(
        ok=False, stdout="", stderr="ls: /nope: No such file or directory",
        exit_code=1,
    )
    body = client.get(
        "/devices/SERIAL01/files", params={"path": "/nope"}
    ).json()
    assert body["ok"] is False
    assert "No such file" in body["error"]
    assert body["exit_code"] == 1


def test_device_files_build_transport_failure(workspace, monkeypatch) -> None:
    def _boom(**kwargs: Any):
        raise RuntimeError("no adb daemon")

    monkeypatch.setattr("alb.api.files_route.build_transport", _boom)
    app = create_app()
    with TestClient(app) as c:
        body = c.get("/devices/SERIAL01/files", params={"path": "/sdcard/"}).json()
    assert body["ok"] is False
    assert "RuntimeError" in body["error"]


# ─── GET /workspace/files ───────────────────────────────────────────
def test_workspace_files_lists_directory(client, workspace) -> None:
    (workspace / "devices" / "abc" / "logs").mkdir(parents=True)
    (workspace / "devices" / "abc" / "logs" / "a.log").write_text("a\n")
    (workspace / "devices" / "abc" / "logs" / "b.log").write_text("bb\n")

    body = client.get(
        "/workspace/files", params={"path": "devices/abc/logs"}
    ).json()
    assert body["ok"] is True
    names = sorted(e["name"] for e in body["entries"])
    assert names == ["a.log", "b.log"]
    by_name = {e["name"]: e for e in body["entries"]}
    assert by_name["a.log"]["size"] == 2
    assert by_name["b.log"]["size"] == 3


def test_workspace_files_rejects_traversal(client, workspace) -> None:
    body = client.get(
        "/workspace/files", params={"path": "../etc"}
    ).json()
    assert body["ok"] is False
    assert "escape" in body["error"]


def test_workspace_files_404_when_missing(client) -> None:
    body = client.get(
        "/workspace/files", params={"path": "nope"}
    ).json()
    assert body["ok"] is False
    assert "does not exist" in body["error"]


def test_workspace_files_truncates_large_directory(
    client, workspace, monkeypatch
) -> None:
    """50k-file dirs should slice on cheap DirEntry sort and not pay
    50k stat() calls (MID-3). We patch _MAX_ENTRIES to 5 and create 50
    files; truncated must be True and entries length must be 5."""
    import alb.api.files_route as mod

    monkeypatch.setattr(mod, "_MAX_ENTRIES", 5)
    big = workspace / "big"
    big.mkdir()
    for i in range(50):
        (big / f"f{i:03d}.txt").write_text("x")

    body = client.get("/workspace/files", params={"path": "big"}).json()
    assert body["ok"] is True
    assert body["truncated"] is True
    assert len(body["entries"]) == 5
    # Sort guarantees first 5 names alphabetical.
    names = [e["name"] for e in body["entries"]]
    assert names == sorted(names)


def test_workspace_files_dirs_first_within_truncation(
    client, workspace, monkeypatch
) -> None:
    """Mixed dirs + files: dirs must come before files so a 50k-file
    dir doesn't hide its subdirs past the cutoff."""
    import alb.api.files_route as mod

    monkeypatch.setattr(mod, "_MAX_ENTRIES", 5)
    mixed = workspace / "mixed"
    mixed.mkdir()
    # 50 files + 3 dirs, dirs alphabetically late
    for i in range(50):
        (mixed / f"file{i:03d}.txt").write_text("x")
    for n in ("zzz_dir1", "zzz_dir2", "zzz_dir3"):
        (mixed / n).mkdir()

    body = client.get("/workspace/files", params={"path": "mixed"}).json()
    assert body["ok"] is True
    assert body["truncated"] is True
    # All 3 dirs must survive the cap; they sort first regardless of name.
    is_dir = [e["is_dir"] for e in body["entries"]]
    assert is_dir.count(True) == 3


# ─── POST /devices/{serial}/files/pull ──────────────────────────────
def test_pull_default_local_lands_in_workspace(client, fake_transport, workspace) -> None:
    body = client.post(
        "/devices/SERIAL01/files/pull",
        json={"remote": "/sdcard/Download/foo.txt"},
    ).json()
    assert body["ok"] is True
    assert body["remote"] == "/sdcard/Download/foo.txt"
    assert body["local"] is not None
    pulled = Path(body["local"])
    assert pulled.exists()
    # Should land under the device's pulls/ directory.
    assert "devices/SERIAL01/pulls" in str(pulled).replace("\\", "/")


def test_pull_explicit_local_path(client, fake_transport, workspace) -> None:
    body = client.post(
        "/devices/SERIAL01/files/pull",
        json={"remote": "/sdcard/foo.txt", "local": "devices/SERIAL01/pulls/custom.txt"},
    ).json()
    assert body["ok"] is True
    assert body["local"].endswith("custom.txt")
    assert (workspace / "devices/SERIAL01/pulls/custom.txt").exists()


def test_pull_rejects_invalid_remote(client) -> None:
    body = client.post(
        "/devices/SERIAL01/files/pull", json={"remote": "relative.txt"}
    ).json()
    assert body["ok"] is False
    assert "invalid" in body["error"]


def test_pull_rejects_local_traversal(client) -> None:
    body = client.post(
        "/devices/SERIAL01/files/pull",
        json={"remote": "/sdcard/foo.txt", "local": "../etc/x"},
    ).json()
    assert body["ok"] is False
    assert "escape" in body["error"]


# ─── POST /devices/{serial}/files/push ──────────────────────────────
def test_push_to_sdcard_passes_through(client, fake_transport, workspace) -> None:
    src = workspace / "devices/SERIAL01/upload/a.txt"
    src.parent.mkdir(parents=True)
    src.write_text("hello\n")

    body = client.post(
        "/devices/SERIAL01/files/push",
        json={"local": "devices/SERIAL01/upload/a.txt", "remote": "/sdcard/a.txt"},
    ).json()
    assert body["ok"] is True
    assert body["remote"] == "/sdcard/a.txt"
    assert fake_transport.push_calls == [(src, "/sdcard/a.txt")]
    assert body["bytes_transferred"] == len("hello\n")


def test_push_rejects_missing_local(client, workspace) -> None:
    body = client.post(
        "/devices/SERIAL01/files/push",
        json={"local": "devices/nope/missing.txt", "remote": "/sdcard/x"},
    ).json()
    assert body["ok"] is False
    assert "does not exist" in body["error"]


def test_push_to_system_requires_confirm(client, workspace) -> None:
    src = workspace / "uploads/sys.bin"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"\x00\x01")

    body = client.post(
        "/devices/SERIAL01/files/push",
        json={"local": "uploads/sys.bin", "remote": "/system/lib/foo.so"},
    ).json()
    assert body["ok"] is False
    assert body["requires_confirm"] is True
    assert "/system/lib/foo.so" in body["error"]


def test_push_to_system_with_force_proceeds(client, fake_transport, workspace) -> None:
    src = workspace / "uploads/sys.bin"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"\x00\x01")

    body = client.post(
        "/devices/SERIAL01/files/push",
        json={
            "local": "uploads/sys.bin",
            "remote": "/system/lib/foo.so",
            "force": True,
        },
    ).json()
    assert body["ok"] is True
    assert fake_transport.push_calls == [(src, "/system/lib/foo.so")]


def test_push_to_data_local_tmp_does_not_warn(client, fake_transport, workspace) -> None:
    """Standard scratch dir is exempt from the HITL gate."""
    src = workspace / "uploads/scratch.bin"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"x")

    body = client.post(
        "/devices/SERIAL01/files/push",
        json={"local": "uploads/scratch.bin", "remote": "/data/local/tmp/x"},
    ).json()
    assert body["ok"] is True
    assert "requires_confirm" not in body


def test_push_rejects_invalid_remote(client, workspace) -> None:
    src = workspace / "uploads/x"
    src.parent.mkdir(parents=True)
    src.write_text("x")
    body = client.post(
        "/devices/SERIAL01/files/push",
        json={"local": "uploads/x", "remote": "relative"},
    ).json()
    assert body["ok"] is False
    assert "invalid" in body["error"]


# ─── GET /workspace/files/download/{path} ───────────────────────────
def test_workspace_download_streams_file(client, workspace) -> None:
    f = workspace / "devices" / "abc" / "pulls" / "a.bin"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"\x00binarydata\xff")

    r = client.get("/workspace/files/download/devices/abc/pulls/a.bin")
    assert r.status_code == 200
    assert r.content == b"\x00binarydata\xff"
    assert r.headers["content-type"].startswith("application/octet-stream")


def test_workspace_download_404_when_missing(client) -> None:
    r = client.get("/workspace/files/download/nope/x.bin")
    assert r.status_code == 404


def test_workspace_download_rejects_traversal(client) -> None:
    r = client.get("/workspace/files/download/../etc/passwd")
    # Either 400 (our gate) or 404 (FastAPI normalises). Both are fine.
    assert r.status_code in (400, 404)


# ─── Range request regression (functional audit 2026-05-02 MID-4) ───
# Audit claimed "no Range header support — multi-GB downloads can't
# resume". 2026-05-05 retroactive verify: Starlette FileResponse 1.0
# already handles Range natively (Accept-Ranges / 206 Partial Content /
# 416 Range Not Satisfiable / If-Range). MID-4 was a virtual finding.
# These tests lock the behavior in so future Starlette downgrade /
# response refactor doesn't silently kill resumable downloads.


def test_workspace_download_advertises_accept_ranges(client, workspace) -> None:
    """200 response must carry `Accept-Ranges: bytes` so browsers /
    download managers know they can resume."""
    f = workspace / "ranges" / "small.bin"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"X" * 100)

    r = client.get("/workspace/files/download/ranges/small.bin")
    assert r.status_code == 200
    assert r.headers.get("accept-ranges") == "bytes"
    assert r.headers.get("content-length") == "100"


def test_workspace_download_partial_range_returns_206(client, workspace) -> None:
    """`Range: bytes=START-END` must return 206 Partial Content with
    only the requested slice + Content-Range header."""
    f = workspace / "ranges" / "thousand.bin"
    f.parent.mkdir(parents=True)
    payload = bytes(range(256)) * 4  # 1024 bytes, deterministic
    assert len(payload) == 1024
    f.write_bytes(payload)

    r = client.get(
        "/workspace/files/download/ranges/thousand.bin",
        headers={"Range": "bytes=100-199"},
    )
    assert r.status_code == 206
    assert r.headers.get("content-range") == "bytes 100-199/1024"
    assert r.headers.get("content-length") == "100"
    assert r.content == payload[100:200]


def test_workspace_download_open_ended_range(client, workspace) -> None:
    """`bytes=START-` (open-ended) reads from START to EOF."""
    f = workspace / "ranges" / "open.bin"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"abcdefghij")

    r = client.get(
        "/workspace/files/download/ranges/open.bin",
        headers={"Range": "bytes=5-"},
    )
    assert r.status_code == 206
    assert r.content == b"fghij"
    assert r.headers.get("content-range") == "bytes 5-9/10"


def test_workspace_download_suffix_range(client, workspace) -> None:
    """`bytes=-N` reads the LAST N bytes."""
    f = workspace / "ranges" / "tail.bin"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"abcdefghij")

    r = client.get(
        "/workspace/files/download/ranges/tail.bin",
        headers={"Range": "bytes=-3"},
    )
    assert r.status_code == 206
    assert r.content == b"hij"


def test_workspace_download_unsatisfiable_range_returns_416(
    client, workspace,
) -> None:
    """Range past EOF must 416 with `Content-Range: bytes */<size>`
    so clients can recover by re-requesting full or trimmed range."""
    f = workspace / "ranges" / "small.bin"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"X" * 100)

    r = client.get(
        "/workspace/files/download/ranges/small.bin",
        headers={"Range": "bytes=500-1000"},
    )
    assert r.status_code == 416
    assert r.headers.get("content-range") == "bytes */100"


# ─── Regression tests added 2026-05-02 (PR-H code review) ──────────
def test_push_rejects_dotdot_traversal_bypass(client, workspace) -> None:
    """`/data/local/tmp/../system/lib/foo.so` would slip past the
    HITL exemption gate (matches /data/local/tmp prefix) and adb
    resolves `..` on-device → writes to /system. Must be rejected
    by `_is_safe_remote_path` outright."""
    src = workspace / "uploads/x"
    src.parent.mkdir(parents=True)
    src.write_text("x")
    body = client.post(
        "/devices/SERIAL01/files/push",
        json={
            "local": "uploads/x",
            "remote": "/data/local/tmp/../system/lib/foo.so",
        },
    ).json()
    assert body["ok"] is False
    assert "invalid" in body["error"]
    assert "requires_confirm" not in body  # never reached HITL gate


def test_is_sensitive_remote_normalizes_dotdot() -> None:
    """Defense in depth — if `..` ever slips past the safety gate
    (e.g. via a future refactor), normpath inside _is_sensitive_remote
    still classifies the resolved path correctly."""
    from alb.api.files_route import _is_sensitive_remote

    assert _is_sensitive_remote("/data/local/tmp/../system/lib/foo") is True
    assert _is_sensitive_remote("/sdcard/../system") is True
    assert _is_sensitive_remote("/data/local/tmp/foo") is False
    assert _is_sensitive_remote("/sdcard/Download") is False


def test_device_files_truncates_after_sort(client, fake_transport) -> None:
    """Sort happens before truncate — directories should always be
    visible even when entry count exceeds _MAX_ENTRIES."""
    from alb.api.files_route import _MAX_ENTRIES

    # 1 dir + (_MAX_ENTRIES + 5) files; toybox typically returns inode
    # order so the dir lands somewhere arbitrary. Sort-before-cap must
    # surface it.
    lines = ["total 9999"]
    lines.append(
        "drwxr-xr-x  2 root root 4096 2026-05-01 00:00 zzz_dir_at_end"
    )
    for i in range(_MAX_ENTRIES + 5):
        lines.append(
            f"-rw-r--r-- 1 root root {i:>10} 2026-05-01 00:00 file{i:05d}.bin"
        )
    fake_transport.shell_response = ShellResult(
        ok=True, stdout="\n".join(lines), stderr=""
    )

    body = client.get(
        "/devices/SERIAL01/files", params={"path": "/big"}
    ).json()
    assert body["ok"] is True
    assert body["truncated"] is True
    assert len(body["entries"]) == _MAX_ENTRIES
    # Dir was at toybox inode position 1 but should sort to position 0
    # AND survive the cap (regression for PR-H code-review MID #3).
    assert body["entries"][0]["name"] == "zzz_dir_at_end"
    assert body["entries"][0]["is_dir"] is True


def test_push_empty_file(client, fake_transport, workspace) -> None:
    """0-byte push is a real Android workflow (touch sentinels) —
    bytes_transferred=0 must not be confused with failure."""
    src = workspace / "uploads/empty.flag"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"")
    body = client.post(
        "/devices/SERIAL01/files/push",
        json={"local": "uploads/empty.flag", "remote": "/sdcard/sentinel"},
    ).json()
    assert body["ok"] is True
    assert body["bytes_transferred"] == 0


def test_endpoints_carry_transport_field(client, fake_transport) -> None:
    """All 3 device-scoped endpoints surface `transport` so the UI can
    render hybrid-target indicators consistently with /devices/* endpoints."""
    fake_transport.shell_response = ShellResult(
        ok=True,
        stdout="-rw-r--r-- 1 root root 1 2026-05-01 00:00 a\n",
        stderr="",
    )
    ls_body = client.get(
        "/devices/SERIAL01/files", params={"path": "/sdcard/"}
    ).json()
    assert ls_body["transport"] == "_FakeAdbTransport"


# ─── Schema discovery ───────────────────────────────────────────────
def test_files_endpoints_listed_in_schema(client) -> None:
    body = client.get("/api/version").json()
    paths = [(e["method"], e["path"]) for e in body["rest"]]
    assert ("GET", "/devices/{serial}/files") in paths
    assert ("GET", "/workspace/files") in paths
    assert ("POST", "/devices/{serial}/files/pull") in paths
    assert ("POST", "/devices/{serial}/files/push") in paths
    assert ("GET", "/workspace/files/download/{path}") in paths
    ws_paths = [w["path"] for w in body["ws"]]
    assert "/devices/{serial}/files/push/stream" in ws_paths
    assert "/devices/{serial}/files/pull/stream" in ws_paths


# ─── WS /devices/{s}/files/push/stream + pull/stream (MID-6) ────────


def _drain_ws(ws, max_frames: int = 50) -> tuple[dict | None, list[dict]]:
    """Drain frames until a `closed` JSON arrives (or max_frames). Returns
    (closed_obj, all_progress_frames)."""
    progress: list[dict] = []
    closed: dict | None = None
    for _ in range(max_frames):
        try:
            obj = ws.receive_json()
        except Exception:
            break
        t = obj.get("type")
        if t == "progress":
            progress.append(obj)
        elif t == "closed":
            closed = obj
            break
        # Skip "ready" and other frames silently — caller already
        # received `ready` before calling _drain_ws if needed.
    return closed, progress


def test_push_stream_happy_path(client, fake_transport, workspace) -> None:
    """3 progress events + done → 3 progress frames + closed{ok:true}."""
    from alb.transport.base import TransferEvent

    src = workspace / "x.bin"
    src.write_bytes(b"X" * 100)
    fake_transport.stream_events = [
        TransferEvent(kind="progress", percent=0.0, file="/sdcard/x", bytes_transferred=0),
        TransferEvent(kind="progress", percent=50.0, file="/sdcard/x", bytes_transferred=50),
        TransferEvent(kind="progress", percent=100.0, file="/sdcard/x", bytes_transferred=100),
        TransferEvent(
            kind="done", ok=True, bytes_transferred=100,
            duration_ms=42, percent=100.0,
        ),
    ]

    with client.websocket_connect("/devices/SERIAL01/files/push/stream") as ws:
        ws.send_json({"local": "x.bin", "remote": "/sdcard/x"})
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        assert ready["serial"] == "SERIAL01"
        assert ready["direction"] == "push"
        closed, prog = _drain_ws(ws)

    assert len(prog) == 3
    assert [p["percent"] for p in prog] == [0.0, 50.0, 100.0]
    assert closed is not None
    assert closed["reason"] == "done"
    assert closed["ok"] is True
    assert closed["bytes_transferred"] == 100
    assert closed["duration_ms"] == 42


def test_pull_stream_default_local_lands_in_workspace(
    client, fake_transport, workspace,
) -> None:
    """Pull without `local` defaults to devices/<serial>/pulls/<basename>-<ts>."""
    from alb.transport.base import TransferEvent

    fake_transport.stream_events = [
        TransferEvent(kind="done", ok=True, bytes_transferred=10, duration_ms=10),
    ]

    with client.websocket_connect("/devices/SERIAL01/files/pull/stream") as ws:
        ws.send_json({"remote": "/sdcard/foo.txt"})
        ready = ws.receive_json()
        assert ready["direction"] == "pull"
        assert "/devices/SERIAL01/pulls/" in ready["local"]
        closed, _ = _drain_ws(ws)

    assert closed["reason"] == "done"
    assert closed["ok"] is True


def test_push_stream_missing_local_returns_bad_config(client) -> None:
    with client.websocket_connect("/devices/SERIAL01/files/push/stream") as ws:
        ws.send_json({"remote": "/sdcard/x"})  # missing 'local'
        closed = ws.receive_json()
        assert closed["type"] == "closed"
        assert closed["reason"] == "bad_config"
        assert "local" in (closed["error"] or "").lower()


def test_push_stream_invalid_remote_returns_bad_config(client, workspace) -> None:
    src = workspace / "x.bin"
    src.write_bytes(b"x")
    with client.websocket_connect("/devices/SERIAL01/files/push/stream") as ws:
        ws.send_json({"local": "x.bin", "remote": "../etc/passwd"})
        closed = ws.receive_json()
        assert closed["type"] == "closed"
        assert closed["reason"] == "bad_config"


def test_push_stream_sensitive_path_without_force_blocks(
    client, fake_transport, workspace,
) -> None:
    """/system without force=true → closed{reason:'sensitive_path'}."""
    src = workspace / "boot.img"
    src.write_bytes(b"x")
    with client.websocket_connect("/devices/SERIAL01/files/push/stream") as ws:
        ws.send_json({"local": "boot.img", "remote": "/system/boot.img"})
        closed = ws.receive_json()
        assert closed["type"] == "closed"
        assert closed["reason"] == "sensitive_path"
        assert "force" in (closed["error"] or "")
    # adb push_stream must NOT have been invoked.
    assert fake_transport.push_calls == []


def test_push_stream_sensitive_path_with_force_proceeds(
    client, fake_transport, workspace,
) -> None:
    from alb.transport.base import TransferEvent

    src = workspace / "boot.img"
    src.write_bytes(b"x")
    fake_transport.stream_events = [
        TransferEvent(kind="done", ok=True, bytes_transferred=1, duration_ms=1),
    ]
    with client.websocket_connect("/devices/SERIAL01/files/push/stream") as ws:
        ws.send_json({
            "local": "boot.img",
            "remote": "/system/boot.img",
            "force": True,
        })
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        closed, _ = _drain_ws(ws)
    assert closed["reason"] == "done"
    assert closed["ok"] is True


def test_push_stream_unsupported_transport_closes(workspace, monkeypatch) -> None:
    """Transport without push_stream → closed{reason:'unsupported_transport'}."""

    class _NoStreamTransport:
        async def shell(self, *a, **kw):
            return ShellResult(ok=True)

    monkeypatch.setenv("ALB_WORKSPACE", str(workspace))
    monkeypatch.setattr(
        "alb.api.files_route.build_transport",
        lambda **kwargs: _NoStreamTransport(),
    )
    src = workspace / "x.bin"
    src.write_bytes(b"x")
    app = create_app()
    with TestClient(app) as c:
        with c.websocket_connect("/devices/SERIAL01/files/push/stream") as ws:
            ws.send_json({"local": "x.bin", "remote": "/sdcard/x"})
            closed = ws.receive_json()
            assert closed["type"] == "closed"
            assert closed["reason"] == "unsupported_transport"


def test_push_stream_init_failed_closes(workspace, monkeypatch) -> None:
    def _boom(**_):
        raise RuntimeError("adb server unreachable")

    monkeypatch.setenv("ALB_WORKSPACE", str(workspace))
    monkeypatch.setattr(
        "alb.api.files_route.build_transport", _boom,
    )
    src = workspace / "x.bin"
    src.write_bytes(b"x")
    app = create_app()
    with TestClient(app) as c:
        with c.websocket_connect("/devices/SERIAL01/files/push/stream") as ws:
            ws.send_json({"local": "x.bin", "remote": "/sdcard/x"})
            closed = ws.receive_json()
            assert closed["type"] == "closed"
            assert closed["reason"] == "init_failed"
            assert "RuntimeError" in (closed["error"] or "")


def test_push_stream_cancel_returns_cancelled_reason(
    client, fake_transport, workspace,
) -> None:
    """Client sends {type:'cancel'} after first progress → server
    closes with reason='cancelled' + ok=false. The inner generator's
    finally semantics (terminating adb) is verified by
    test_push_stream_cancel_terminates_subprocess in
    tests/transport/test_adb_transfer_stream.py — at this layer we
    only assert the WS protocol contract."""
    from alb.transport.base import TransferEvent

    src = workspace / "big.bin"
    src.write_bytes(b"X" * 1000)
    # Many progress events with a small delay so cancel can interleave.
    fake_transport.stream_events = [
        TransferEvent(kind="progress", percent=p, file="/sdcard/big",
                      bytes_transferred=p * 10)
        for p in range(0, 100, 5)
    ] + [
        TransferEvent(kind="done", ok=True, bytes_transferred=1000, duration_ms=999),
    ]
    fake_transport.stream_delay_s = 0.05

    closed = None
    with client.websocket_connect("/devices/SERIAL01/files/push/stream") as ws:
        ws.send_json({"local": "big.bin", "remote": "/sdcard/big"})
        ws.receive_json()  # ready
        # Wait for first progress.
        first = ws.receive_json()
        assert first["type"] == "progress"
        # Cancel.
        ws.send_json({"type": "cancel"})
        # Drain until closed (or hit exception on closed-WS read).
        for _ in range(60):
            try:
                obj = ws.receive_json()
            except Exception:
                break
            if obj.get("type") == "closed":
                closed = obj
                break

    assert closed is not None
    assert closed["reason"] == "cancelled"
    assert closed["ok"] is False


def test_push_stream_no_first_message_returns_bad_config(workspace, monkeypatch) -> None:
    """Connect but never send the first JSON → after timeout closes
    with reason='bad_config'."""
    monkeypatch.setenv("ALB_WORKSPACE", str(workspace))
    # No transport patch needed — we close before that path.
    app = create_app()
    with TestClient(app) as c:
        with c.websocket_connect("/devices/SERIAL01/files/push/stream") as ws:
            # Don't send anything; server config-read times out at 2s.
            closed = ws.receive_json()
            assert closed["type"] == "closed"
            assert closed["reason"] == "bad_config"
