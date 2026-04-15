"""Tests for SshTransport — argv + error classification + permissions.

We don't spin up a real sshd; instead we cover:
  - parameter / error-mapping logic (_classify_ssh_error)
  - transport-specific check_permissions rules
  - rsync argv construction via mocked subprocess
  - graceful ImportError when asyncssh isn't installed
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from alb.transport.ssh import SshTransport, _classify_ssh_error


# ─── Error classification ──────────────────────────────────────────
def test_classify_auth_failures() -> None:
    assert _classify_ssh_error(Exception("Permission denied (publickey)")) == "SSH_AUTH_FAILED"
    assert _classify_ssh_error(Exception("Authentication failed")) == "SSH_AUTH_FAILED"
    assert _classify_ssh_error(Exception("host key verification failed")) == "SSH_AUTH_FAILED"


def test_classify_host_unreachable() -> None:
    assert _classify_ssh_error(Exception("Connection refused")) == "SSH_HOST_UNREACHABLE"
    assert _classify_ssh_error(Exception("No route to host")) == "SSH_HOST_UNREACHABLE"
    assert _classify_ssh_error(Exception("getaddrinfo failed")) == "SSH_HOST_UNREACHABLE"


def test_classify_timeout_and_key() -> None:
    assert _classify_ssh_error(Exception("operation timed out")) == "TIMEOUT_CONNECT"
    assert _classify_ssh_error(Exception("No such file or directory: my key")) == "SSH_KEY_NOT_FOUND"


def test_classify_generic_fallback() -> None:
    assert _classify_ssh_error(Exception("some other problem")) == "SSH_COMMAND_FAILED"


# ─── Permission rules ──────────────────────────────────────────────
@pytest.mark.asyncio
async def test_check_permissions_allows_benign() -> None:
    t = SshTransport(host="1.2.3.4")
    r = await t.check_permissions("shell.execute", {"cmd": "ls /data"})
    assert r.behavior == "allow"


@pytest.mark.asyncio
async def test_check_permissions_asks_on_system_push() -> None:
    t = SshTransport(host="1.2.3.4")
    r = await t.check_permissions(
        "filesync.push", {"local": "/x", "remote": "/vendor/etc/foo.conf"}
    )
    assert r.behavior == "ask"


@pytest.mark.asyncio
async def test_check_permissions_denies_block_device_push() -> None:
    t = SshTransport(host="1.2.3.4")
    r = await t.check_permissions(
        "filesync.push", {"local": "/x", "remote": "/dev/block/by-name/boot"}
    )
    assert r.behavior == "deny"


@pytest.mark.asyncio
async def test_check_permissions_denies_rm_rf_root() -> None:
    t = SshTransport(host="1.2.3.4")
    r = await t.check_permissions("shell.execute", {"cmd": "rm -rf /"})
    assert r.behavior == "deny"


# ─── ImportError when asyncssh is missing ──────────────────────────
@pytest.mark.asyncio
async def test_shell_returns_structured_error_when_asyncssh_missing() -> None:
    t = SshTransport(host="1.2.3.4")

    def boom() -> None:
        raise ImportError("asyncssh required")

    with patch.object(SshTransport, "_load_asyncssh", staticmethod(boom)):
        r = await t.shell("whoami")
    assert not r.ok
    assert r.error_code == "SYSTEM_DEPENDENCY_MISSING"


# ─── Non-normal reboot is refused ──────────────────────────────────
@pytest.mark.asyncio
async def test_reboot_non_normal_unsupported() -> None:
    t = SshTransport(host="1.2.3.4")
    r = await t.reboot("recovery")
    assert not r.ok
    assert r.error_code == "TRANSPORT_NOT_SUPPORTED"


# ─── rsync argv construction ───────────────────────────────────────
class _FakeProc:
    def __init__(self, out: bytes = b"", err: bytes = b"", code: int = 0) -> None:
        self._out = out
        self._err = err
        self.returncode = code

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:  # noqa: A002
        return self._out, self._err


@pytest.mark.asyncio
async def test_rsync_argv_includes_ssh_opts(tmp_path: Path) -> None:
    src = tmp_path / "pkg"
    src.mkdir()
    recorded: dict = {}

    async def fake_exec(*args: str, **kw: object) -> _FakeProc:
        recorded["args"] = args
        return _FakeProc(out=b"sent 1 file\n")

    # Mock both the rsync binary presence and subprocess.
    with patch("alb.transport.ssh.shutil.which", return_value="/usr/bin/rsync"), \
         patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        t = SshTransport(
            host="10.0.0.5",
            port=2222,
            user="root",
            key_path="~/.ssh/alb",
            known_hosts="",  # relaxed
        )
        r = await t.rsync(src, "/data/dev")

    assert r.ok
    argv = recorded["args"]
    assert argv[0] == "rsync"
    # -e "ssh -p 2222 -i ~/.ssh/alb -o StrictHostKeyChecking=no ..."
    e_idx = list(argv).index("-e")
    ssh_cmd = argv[e_idx + 1]
    assert "-p 2222" in ssh_cmd
    assert "-i" in ssh_cmd
    assert "StrictHostKeyChecking=no" in ssh_cmd
    # target host:dir is last arg
    assert str(argv[-1]).startswith("root@10.0.0.5:/data/dev/")


@pytest.mark.asyncio
async def test_rsync_missing_binary(tmp_path: Path) -> None:
    src = tmp_path / "pkg"
    src.mkdir()
    with patch("alb.transport.ssh.shutil.which", return_value=None):
        t = SshTransport(host="10.0.0.5")
        r = await t.rsync(src, "/data/dev")
    assert not r.ok
    assert r.error_code == "SYSTEM_DEPENDENCY_MISSING"
