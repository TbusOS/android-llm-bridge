"""`alb config` CLI surface.

The JSON branch has its own test because that is where it broke: the first
version called `print_result(result)` without the context argument, which only
raises on `--json`. A human running the command once sees a perfect table and
concludes it works — the defect lives on a branch the eye never takes.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from alb.cli.main import app
from alb.infra.result import fail, ok

runner = CliRunner()

_CANDIDATES = {
    "candidates": [{"name": "cfgpart", "node": "/dev/block/mmcblk0p56", "lines": 11}],
    "hint": "",
}
_READ = {
    "device": "cfgpart",
    "node": "/dev/block/by-name/cfgpart",
    "size_bytes": 2097152,
    "read_bytes": 4096,
    "parsed": True,
    "entries": [{"key": "PORT_BOARD", "value": "V03"}],
    "raw": 'PORT_BOARD="V03"',
}


def _patch(monkeypatch, *, scan=None, read=None):
    monkeypatch.setattr("alb.cli.config_cli.get_transport", lambda _ctx: object())
    if scan is not None:
        monkeypatch.setattr("alb.capabilities.board_config.scan", scan)
    if read is not None:
        monkeypatch.setattr("alb.capabilities.board_config.read", read)


def test_scan_table(monkeypatch):
    async def scan(_t, **_k):
        return ok(_CANDIDATES)

    _patch(monkeypatch, scan=scan)
    r = runner.invoke(app, ["config", "scan"])
    assert r.exit_code == 0
    assert "cfgpart" in r.stdout and "mmcblk0p56" in r.stdout


def test_scan_json_branch_does_not_crash(monkeypatch):
    """Regression: this branch raised TypeError while the table branch was
    fine. Machine-readable output is exactly what an agent would use."""

    async def scan(_t, **_k):
        return ok(_CANDIDATES)

    _patch(monkeypatch, scan=scan)
    r = runner.invoke(app, ["--json", "config", "scan"])
    assert r.exit_code == 0, r.stdout
    assert json.loads(r.stdout)["data"]["candidates"][0]["name"] == "cfgpart"


def test_scan_empty_prints_the_root_hint(monkeypatch):
    async def scan(_t, **_k):
        return ok({"candidates": [], "hint": "nothing matched — is the shell root?"})

    _patch(monkeypatch, scan=scan)
    r = runner.invoke(app, ["config", "scan"])
    assert r.exit_code == 0
    assert "root" in r.stdout, "an empty result must name its usual cause"


def test_read_table(monkeypatch):
    async def read(_t, _d, **_k):
        return ok(_READ)

    _patch(monkeypatch, read=read)
    r = runner.invoke(app, ["config", "read", "cfgpart"])
    assert r.exit_code == 0
    assert "PORT_BOARD" in r.stdout and "V03" in r.stdout


def test_read_json_branch_does_not_crash(monkeypatch):
    async def read(_t, _d, **_k):
        return ok(_READ)

    _patch(monkeypatch, read=read)
    r = runner.invoke(app, ["--json", "config", "read", "cfgpart"])
    assert r.exit_code == 0, r.stdout
    assert json.loads(r.stdout)["data"]["entries"][0]["key"] == "PORT_BOARD"


def test_read_unparsed_shows_raw_not_an_empty_table(monkeypatch):
    async def read(_t, _d, **_k):
        return ok({**_READ, "parsed": False, "entries": [], "raw": "\\0\\0\\0binary"})

    _patch(monkeypatch, read=read)
    r = runner.invoke(app, ["config", "read", "userdata"])
    assert r.exit_code == 0
    assert "does not parse" in r.stdout
    assert "binary" in r.stdout


def test_read_failure_exits_nonzero(monkeypatch):
    async def read(_t, _d, **_k):
        return fail("BOARD_CONFIG_BAD_DEVICE", "device name rejected")

    _patch(monkeypatch, read=read)
    r = runner.invoke(app, ["config", "read", "bad name"])
    assert r.exit_code != 0
