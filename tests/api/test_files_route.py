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
