"""Tests for infra.host_deps — the outside tools alb needs (2026-11-09).

The installers are exercised against a fake home, fake downloads and fake
subprocesses: nothing here touches the network or the real ~/.local.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import zipfile
from collections.abc import Sequence
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from alb.infra import host_deps as hd

REPO = Path(__file__).resolve().parents[2]


# ─── helpers ──────────────────────────────────────────────────────
def _exe(path: Path, body: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(0o755)
    return path


def _pt_zip(path: Path, entries: Sequence[str] = ("adb", "fastboot")) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name in entries:
            info = zipfile.ZipInfo(f"platform-tools/{name}")
            info.external_attr = 0o100755 << 16
            zf.writestr(info, "#!/bin/sh\necho fake\n")
        zf.writestr("platform-tools/NOTICE.txt", "notice")
    return path


def _no_run(argv: Sequence[str], cwd: Path | None, timeout: float) -> tuple[int, str]:
    raise AssertionError(f"unexpected command: {argv}")


def _no_fetch(url: str, dest: Path) -> None:
    raise AssertionError(f"unexpected download: {url}")


def _env(tmp_path: Path, **kw: object) -> hd.HostEnv:
    args: dict[str, object] = {
        "home": tmp_path / "home", "system": "Linux", "repo": None,
        "run": _no_run, "fetch": _no_fetch,
    }
    args.update(kw)
    return hd.HostEnv(**args)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALB_CONFIG", str(tmp_path / "no-config.toml"))
    for var in ("ALB_OLLAMA_URL", "ALB_PLATFORM_TOOLS_URL", "PLAYWRIGHT_BROWSERS_PATH", "XDG_CACHE_HOME"):
        monkeypatch.delenv(var, raising=False)


def _by_name(results: Sequence[hd.DepResult]) -> dict[str, hd.DepResult]:
    return {r.name: r for r in results}


# ─── Platform-Tools (adb / fastboot) ──────────────────────────────
def test_install_downloads_unpacks_and_links_platform_tools(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    env = _env(tmp_path, assume_yes=True)
    monkeypatch.setenv("PATH", str(env.bin_dir))  # empty for now; links land here
    src = _pt_zip(tmp_path / "pt.zip")
    calls: list[str] = []

    def fetch(url: str, dest: Path) -> None:
        calls.append(url)
        shutil.copy(src, dest)

    env.fetch = fetch
    res = _by_name(hd.install_missing(hd.BASE_TIERS, env))

    assert calls == [hd.PLATFORM_TOOLS_URLS["Linux"]]  # one download for adb + fastboot
    assert res["adb"].status == "installed" and res["fastboot"].status == "installed"
    adb = env.data_dir / "platform-tools" / "adb"
    assert os.access(adb, os.X_OK)
    assert os.readlink(env.bin_dir / "adb") == str(adb)
    assert not (env.data_dir / "platform-tools.zip").exists()  # download cleaned up
    assert not hd.blocking(list(res.values()), hd.BASE_TIERS)


def test_adb_already_on_path_is_not_shadowed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An adb of another version in front of the one in use would restart
    the adb server it reaches — only the missing fastboot gets linked."""
    env = _env(tmp_path, assume_yes=True)
    theirs = _exe(tmp_path / "theirs" / "adb")
    monkeypatch.setenv("PATH", f"{theirs.parent}{os.pathsep}{env.bin_dir}")
    def fetch(url: str, dest: Path) -> None:
        shutil.copy(_pt_zip(tmp_path / "pt.zip"), dest)

    env.fetch = fetch

    res = _by_name(hd.install_missing(hd.BASE_TIERS, env))

    assert res["adb"].status == "ok" and res["adb"].detail == str(theirs)
    assert res["fastboot"].status == "installed"
    assert not (env.bin_dir / "adb").exists()
    assert (env.bin_dir / "fastboot").is_symlink()


def test_download_failure_says_how_to_install_by_hand(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    env = _env(tmp_path, assume_yes=True, retry_delay=0)
    monkeypatch.setenv("PATH", str(env.bin_dir))
    tries: list[int] = []

    def fetch(url: str, dest: Path) -> None:
        tries.append(1)
        raise httpx.ConnectError("connection refused")

    env.fetch = fetch
    res = _by_name(hd.install_missing(hd.BASE_TIERS, env))

    adb = res["adb"]
    assert adb.status == "failed" and len(tries) == env.download_attempts
    assert "download failed after 3 tries" in adb.detail and hd.PLATFORM_TOOLS_URLS["Linux"] in adb.detail
    manual = "\n".join(adb.manual)
    for needle in ("--platform-tools-zip", "ALB_ADB_PATH", "HTTPS_PROXY", "ALB_PLATFORM_TOOLS_URL",
                   hd.PLATFORM_TOOLS_URLS["Linux"]):
        assert needle in manual
    assert not env.bin_dir.exists() or not any(env.bin_dir.iterdir())
    assert hd.blocking(list(res.values()), hd.BASE_TIERS) == [adb]


def test_download_is_tried_again_after_a_dropped_connection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    env = _env(tmp_path, assume_yes=True, retry_delay=0)
    monkeypatch.setenv("PATH", str(env.bin_dir))
    src = _pt_zip(tmp_path / "pt.zip")
    tries: list[int] = []

    def flaky(url: str, dest: Path) -> None:
        tries.append(1)
        if len(tries) < 3:
            raise httpx.RemoteProtocolError("peer closed connection without sending complete message body")
        shutil.copy(src, dest)

    env.fetch = flaky
    res = _by_name(hd.install_missing(("required",), env))
    assert len(tries) == 3 and res["adb"].status == "installed"


def test_download_waits_for_a_yes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    asked: list[str] = []

    def confirm(q: str) -> bool:
        asked.append(q)
        return False

    env = _env(tmp_path, confirm=confirm)
    monkeypatch.setenv("PATH", str(env.bin_dir))
    res = _by_name(hd.install_missing(("required",), env))

    assert res["adb"].status == "failed" and "not confirmed" in res["adb"].detail
    assert len(asked) == 1 and "license" in asked[0]  # _no_fetch would have raised


def test_own_zip_skips_the_download(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env = _env(tmp_path, platform_tools_zip=_pt_zip(tmp_path / "mine.zip"))
    monkeypatch.setenv("PATH", str(env.bin_dir))
    res = _by_name(hd.install_missing(("required",), env))
    assert res["adb"].status == "installed"
    assert (tmp_path / "mine.zip").exists()  # the user's own file is kept


@pytest.mark.parametrize("make", ["not-a-zip", "no-adb", "escapes"])
def test_bad_zip_fails_and_keeps_the_earlier_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, make: str,
) -> None:
    bad = tmp_path / "bad.zip"
    if make == "not-a-zip":
        bad.write_text("<html>proxy login page</html>")
    elif make == "no-adb":
        _pt_zip(bad, entries=("fastboot",))
    else:
        _pt_zip(bad)
        with zipfile.ZipFile(bad, "a") as zf:
            zf.writestr("platform-tools/../../evil", "x")
    env = _env(tmp_path, platform_tools_zip=bad)
    old = _exe(env.data_dir / "platform-tools" / "adb", "old")
    monkeypatch.setenv("PATH", str(env.bin_dir))

    res = _by_name(hd.install_missing(("required",), env))

    assert not list(tmp_path.rglob("evil"))  # nothing unpacked outside platform-tools/
    assert res["adb"].status == "failed"
    assert old.read_text() == "old"


# ─── Node / npm / Playwright ──────────────────────────────────────
@pytest.mark.parametrize(
    ("version", "ok"),
    [("v18.20.0", False), ("v20.18.3", False), ("v20.19.0", True), ("v21.7.0", False),
     ("v22.11.0", False), ("v22.12.0", True), ("v23.1.0", False), ("v24.0.0", True),
     ("v25.2.1", True), ("garbage", False)],
)
def test_node_version_range(version: str, ok: bool) -> None:
    assert hd.node_version_ok(version) is ok


def test_node_range_still_matches_the_lockfile() -> None:
    lock = (REPO / "web" / "package-lock.json").read_text()
    assert f'"node": "{hd.NODE_RANGE_TEXT}"' in lock


def test_playwright_test_is_pinned_exactly() -> None:
    pin = json.loads((REPO / "web" / "package.json").read_text())["devDependencies"]["@playwright/test"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", pin), pin


def _fake_checkout(tmp_path: Path, *, installed: bool = True) -> Path:
    repo = tmp_path / "repo"
    web = repo / "web"
    web.mkdir(parents=True)
    (repo / "pyproject.toml").write_text("")
    (web / "package.json").write_text(json.dumps({"devDependencies": {"@playwright/test": "1.59.1"}}))
    if installed:
        nm = web / "node_modules"
        (nm / "@playwright" / "test").mkdir(parents=True)
        (nm / "@playwright" / "test" / "package.json").write_text(json.dumps({"version": "1.59.1"}))
        (nm / "vitest").mkdir()
        (nm / "vitest" / "package.json").write_text("{}")
        (nm / "playwright-core").mkdir()
        (nm / "playwright-core" / "browsers.json").write_text(json.dumps({"browsers": [
            {"name": "chromium", "revision": "1217"},
            {"name": "chromium-headless-shell", "revision": "1217"},
            {"name": "firefox", "revision": "1511"},
        ]}))
    return repo


def test_dev_checks_read_the_checkout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))  # no node: its check runs nothing
    pw = tmp_path / "pw"
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(pw))
    for d in ("chromium-1217", "chromium_headless_shell-1217"):
        (pw / d).mkdir(parents=True)
        (pw / d / "INSTALLATION_COMPLETE").write_text("")
    env = _env(tmp_path, repo=_fake_checkout(tmp_path))
    res = _by_name(hd.check_all(("dev",), env))
    assert res["web-packages"].ok and res["playwright-browsers"].ok

    (pw / "chromium_headless_shell-1217" / "INSTALLATION_COMPLETE").unlink()
    res = _by_name(hd.check_all(("dev",), env))
    assert not res["playwright-browsers"].ok
    assert "chromium_headless_shell-1217" in res["playwright-browsers"].detail


def test_npm_failure_shows_its_output_and_stops_the_browser_step(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    _exe(bin_dir / "node")
    monkeypatch.setenv("PATH", str(bin_dir))
    ran: list[list[str]] = []

    def run(argv: Sequence[str], cwd: Path | None, timeout: float) -> tuple[int, str]:
        ran.append(list(argv))
        if argv[0] == "node":
            return 0, "v22.12.0"
        if list(argv) == ["npm", "ci"]:
            return 1, "npm ERR! code ETIMEDOUT\nnpm ERR! network request failed"
        return 0, ""

    env = _env(tmp_path, repo=_fake_checkout(tmp_path, installed=False), run=run)
    res = _by_name(hd.install_missing(("dev",), env))

    web = res["web-packages"]
    assert web.status == "failed" and "exited 1" in web.detail and "ETIMEDOUT" in web.detail
    assert any("npm config set registry" in m for m in web.manual)
    assert res["playwright-browsers"].status == "failed"
    assert "needs the web packages first" in res["playwright-browsers"].detail
    assert ["npx", "playwright", "install", "chromium"] not in ran
    assert hd.blocking(list(res.values()), hd.BASE_TIERS + hd.DEV_TIERS)


def test_npm_exit_0_that_installed_nothing_is_a_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Seen 2026-11-09: npm crashed ("Exit handler never called!") yet exited 0."""
    bin_dir = tmp_path / "bin"
    _exe(bin_dir / "node")
    monkeypatch.setenv("PATH", str(bin_dir))

    def run(argv: Sequence[str], cwd: Path | None, timeout: float) -> tuple[int, str]:
        if argv[0] == "node":
            return 0, "v22.12.0"
        return 0, "npm error Exit handler never called!"

    env = _env(tmp_path, repo=_fake_checkout(tmp_path, installed=False), run=run)
    web = _by_name(hd.install_missing(("dev",), env))["web-packages"]
    assert web.status == "failed"
    assert "exited 0 but web/node_modules not installed" in web.detail
    assert "Exit handler never called!" in web.detail


def test_no_node_means_npm_is_not_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    ran: list[str] = []

    def run(argv: Sequence[str], cwd: Path | None, timeout: float) -> tuple[int, str]:
        ran.append(Path(argv[0]).name)
        return 0, ""

    env = _env(tmp_path, repo=_fake_checkout(tmp_path, installed=False), run=run)
    res = _by_name(hd.install_missing(("dev",), env))
    assert res["node"].status == "missing"
    assert "needs Node.js" in res["web-packages"].detail
    assert "npm" not in ran and "npx" not in ran


def test_mksh_no_root_install(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tools = tmp_path / "tools"
    _exe(tools / "apt-get")
    _exe(tools / "dpkg-deb")
    env = _env(tmp_path)
    monkeypatch.setenv("PATH", f"{tools}{os.pathsep}{env.bin_dir}")

    def run(argv: Sequence[str], cwd: Path | None, timeout: float) -> tuple[int, str]:
        if argv[:2] == ["apt-get", "download"]:
            assert cwd is not None
            (cwd / "mksh_59c-9_amd64.deb").write_text("deb")
        elif argv[:2] == ["dpkg-deb", "-x"]:
            _exe(Path(argv[3]) / "bin" / "mksh")
        return 0, ""

    env.run = run
    res = _by_name(hd.install_missing(("dev-optional",), env))
    assert res["mksh"].status == "installed"
    assert os.readlink(env.bin_dir / "mksh") == str(env.data_dir / "mksh" / "bin" / "mksh")
    assert not (env.data_dir / ".mksh-download").exists()


# ─── what fails the run ───────────────────────────────────────────
def test_only_required_and_asked_for_dev_tools_block(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    env = _env(tmp_path)
    base = hd.check_all(hd.BASE_TIERS, env)
    assert [r.name for r in hd.blocking(base, hd.BASE_TIERS)] == ["adb"]  # optional ones never block
    both = hd.check_all(hd.BASE_TIERS + hd.DEV_TIERS, env)
    blocked = {r.name for r in hd.blocking(both, hd.BASE_TIERS + hd.DEV_TIERS)}
    assert "node" in blocked and "mksh" not in blocked and "playwright-cli" not in blocked


def test_every_dep_says_what_it_is_for_and_how_to_get_it(tmp_path: Path) -> None:
    env = _env(tmp_path)
    names = [d.name for d in hd.DEPS]
    assert len(names) == len(set(names))
    for d in hd.DEPS:
        assert d.purpose and d.manual(env), d.name


def test_install_doc_lists_every_dep() -> None:
    doc = (REPO / "docs" / "install.md").read_text(encoding="utf-8")
    missing = [d.name for d in hd.DEPS if f"`{d.name}`" not in doc]
    assert not missing, f"docs/install.md does not mention: {missing}"


# ─── CLI + doctor read the same list ──────────────────────────────
def test_cli_exit_code_follows_required_tools(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from alb.cli.main import app

    runner = CliRunner()
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    r = runner.invoke(app, ["setup", "deps", "--json"])
    assert r.exit_code == 1, r.output
    payload = json.loads(r.output)
    assert payload["ok"] is False
    assert _by_name([hd.DepResult(**x) for x in payload["results"]])["adb"].status == "missing"

    _exe(tmp_path / "bin" / "adb")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    r = runner.invoke(app, ["setup", "deps", "--json"])
    assert r.exit_code == 0, r.output


def test_doctor_binaries_layer_is_the_same_list(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from alb.capabilities.doctor import probe_binaries

    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    layer = probe_binaries()
    assert [c.name for c in layer.checks] == [d.name for d in hd.select(hd.BASE_TIERS)]
    status = {c.name: c.status for c in layer.checks}
    assert status["adb"] == "err" and status["fastboot"] == "skip" and status["ollama"] == "skip"
