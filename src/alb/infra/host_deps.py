"""Host tools alb needs that pip / uv cannot install — one list, three readers.

What this is: the outside programs alb (or its test suite) calls — adb,
ssh, Node.js, Playwright's browser and so on. For each: what it is for,
how to tell it is there, how a person installs it by hand, and, where
that is possible without root, an installer that does it for the user.

Why one list: three places answer "what is missing on this machine":
``scripts/install.sh`` (through ``alb setup deps --install``), ``alb setup
deps`` for someone who installed alb another way, and the binaries layer
of ``alb doctor``. Three hand-kept lists drift apart, and a fresh install
then misses a tool that doctor flags later.

Why not at ``pip install`` time: a wheel has no post-install hook, so
nothing can be fetched while pip / uv installs alb. install.sh is the
earliest point that can do it.

Tiers:
    required      alb's core path (adb) — missing makes ``alb setup deps`` exit 1
    optional      one feature each (ssh transport, serial helpers, model backend)
    dev           running the tests in a git checkout (``--dev``)
    dev-optional  nice for developers, never fails the check

Rules every installer keeps:
  * no sudo, nothing written outside the user's home: files go under
    ``~/.local/share/alb``, links into ``~/.local/bin``;
  * never put a second adb in front of one already on PATH — an adb
    client of another version asks the adb server it reaches to restart,
    and behind a tunnel that server belongs to someone else;
  * when a download or install fails, the result carries the manual steps.
"""

from __future__ import annotations

import os
import platform
import shutil
import stat
import subprocess
import time
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from alb.infra.config import load_config

Tier = Literal["required", "optional", "dev", "dev-optional"]
Status = Literal["ok", "installed", "missing", "failed"]

BASE_TIERS: tuple[Tier, ...] = ("required", "optional")
DEV_TIERS: tuple[Tier, ...] = ("dev", "dev-optional")

PLATFORM_TOOLS_URLS = {
    "Linux": "https://dl.google.com/android/repository/platform-tools-latest-linux.zip",
    "Darwin": "https://dl.google.com/android/repository/platform-tools-latest-darwin.zip",
}
PLATFORM_TOOLS_PAGE = "https://developer.android.com/tools/releases/platform-tools"

# Node versions the web build accepts — copied from the "engines" entry in
# web/package-lock.json (tests/infra/test_host_deps.py checks it is still
# there). Each pair: (major, lowest minor); the last one means "this major
# or newer".
NODE_RANGE_TEXT = "^20.19.0 || ^22.12.0 || >=24.0.0"
_NODE_CARET = ((20, 19), (22, 12))
_NODE_FLOOR = 24

Runner = Callable[[Sequence[str], "Path | None", float], tuple[int, str]]
Fetcher = Callable[[str, Path], None]


class InstallError(Exception):
    """An installer could not finish; the message says why."""


# ─── Environment ──────────────────────────────────────────────────
@dataclass
class HostEnv:
    """Where things live, plus the side-effecting calls tests replace."""

    home: Path
    system: str  # platform.system(): "Linux" / "Darwin"
    repo: Path | None  # git checkout of alb; None for a plain package install
    run: Runner  # quiet: checks such as `node --version`, `ldd`
    fetch: Fetcher
    run_loud: Runner | None = None  # installers (npm ci, ...): shows output as it comes
    assume_yes: bool = False
    download_attempts: int = 3
    retry_delay: float = 3.0  # seconds; the n-th retry waits n times this
    confirm: Callable[[str], bool] = field(default=lambda _q: False)
    platform_tools_zip: Path | None = None
    log: Callable[[str], None] = field(default=lambda _m: None)

    @property
    def data_dir(self) -> Path:
        return self.home / ".local" / "share" / "alb"

    @property
    def bin_dir(self) -> Path:
        return self.home / ".local" / "bin"

    def run_install(self, argv: Sequence[str], cwd: Path | None, timeout: float) -> tuple[int, str]:
        return (self.run_loud or self.run)(argv, cwd, timeout)

    def repo_path(self, *parts: str) -> str:
        base = str(self.repo) if self.repo else "<alb checkout>"
        return str(Path(base, *parts)) if parts else base


def find_repo() -> Path | None:
    """The git checkout this module runs from, or None (plain install)."""
    root = Path(__file__).resolve().parents[3]
    if (root / "pyproject.toml").is_file() and (root / "web" / "package.json").is_file():
        return root
    return None


def run_subprocess(
    argv: Sequence[str], cwd: Path | None, timeout: float,
    echo: Callable[[str], None] | None = None,
) -> tuple[int, str]:
    """Run a command; return (exit code, last 40 lines of its output).

    ``echo`` gets each output line as it arrives, so a person watching a
    multi-minute ``npm ci`` sees it move.
    """
    try:
        proc = subprocess.Popen(
            list(argv), cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace",
        )
    except FileNotFoundError:
        return 127, f"{argv[0]}: command not found"
    tail: list[str] = []
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            line = line.rstrip("\n")
            if echo:
                echo(line)
            tail = [*tail, line][-40:]
        rc = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        return 124, "\n".join([*tail, f"(timed out after {timeout:g}s)"])
    return rc, "\n".join(tail)


def http_fetch(url: str, dest: Path) -> None:
    """Download ``url`` to ``dest``. Honours HTTPS_PROXY / NO_PROXY."""
    import httpx

    part = dest.with_name(dest.name + ".part")
    with httpx.stream(
        "GET", url, follow_redirects=True, timeout=httpx.Timeout(60.0, connect=15.0),
    ) as r:
        r.raise_for_status()
        with part.open("wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    part.replace(dest)


def default_env(
    *, assume_yes: bool = False, confirm: Callable[[str], bool] | None = None,
    platform_tools_zip: Path | None = None, echo: Callable[[str], None] | None = None,
    log: Callable[[str], None] | None = None,
) -> HostEnv:
    def run(argv: Sequence[str], cwd: Path | None, timeout: float) -> tuple[int, str]:
        return run_subprocess(argv, cwd, timeout)

    def run_loud(argv: Sequence[str], cwd: Path | None, timeout: float) -> tuple[int, str]:
        return run_subprocess(argv, cwd, timeout, echo)

    return HostEnv(
        home=Path.home(),
        system=platform.system(),
        repo=find_repo(),
        run=run,
        fetch=http_fetch,
        run_loud=run_loud,
        assume_yes=assume_yes,
        confirm=confirm or (lambda _q: False),
        platform_tools_zip=platform_tools_zip,
        log=log or (lambda _m: None),
    )


# ─── Results ──────────────────────────────────────────────────────
@dataclass(frozen=True)
class DepState:
    ok: bool
    detail: str


@dataclass
class DepResult:
    name: str
    tier: Tier
    purpose: str
    status: Status
    detail: str
    manual: list[str] = field(default_factory=list)
    auto: bool = False  # `alb setup deps --install` has an installer for it

    @property
    def ok(self) -> bool:
        return self.status in ("ok", "installed")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "tier": self.tier, "purpose": self.purpose,
            "status": self.status, "detail": self.detail, "manual": self.manual,
            "auto": self.auto,
        }


@dataclass(frozen=True)
class HostDep:
    name: str
    tier: Tier
    purpose: str
    check: Callable[[HostEnv], DepState]
    manual: Callable[[HostEnv], list[str]]
    install: Callable[[HostEnv], str] | None = None
    # Deps that share one installer (adb + fastboot) run it once.
    installer_key: str | None = None


# ─── Checks ───────────────────────────────────────────────────────
def _which_all(*names: str) -> DepState:
    found = {n: shutil.which(n) for n in names}
    missing = [n for n, p in found.items() if not p]
    if missing:
        return DepState(False, f"not found on PATH: {', '.join(missing)}")
    return DepState(True, ", ".join(p for p in found.values() if p))


def _check_adb(_env: HostEnv) -> DepState:
    # Same lookup the transport does: ALB_ADB_PATH > [transport.adb] bin_path > PATH.
    bin_path = load_config().adb.bin_path
    path = shutil.which(bin_path)
    if path:
        return DepState(True, path)
    if os.sep in bin_path:
        return DepState(False, f"{bin_path}: not found or not executable "
                               "(set by ALB_ADB_PATH or [transport.adb] bin_path)")
    return DepState(False, f"{bin_path}: not found on PATH")


def _check_ollama(_env: HostEnv) -> DepState:
    url = os.environ.get("ALB_OLLAMA_URL")
    if url:
        return DepState(True, f"ALB_OLLAMA_URL={url} (not probed here)")
    path = shutil.which("ollama")
    if path:
        return DepState(True, path)
    return DepState(False, "no ollama on PATH and ALB_OLLAMA_URL unset")


def node_version_ok(version: str) -> bool:
    """True when ``vX.Y.Z`` satisfies :data:`NODE_RANGE_TEXT`."""
    try:
        major, minor = (int(x) for x in version.strip().lstrip("v").split(".")[:2])
    except ValueError:
        return False
    if major >= _NODE_FLOOR:
        return True
    return any(major == m and minor >= lo for m, lo in _NODE_CARET)


def _check_node(env: HostEnv) -> DepState:
    if not shutil.which("node"):
        return DepState(False, "node: not found on PATH")
    rc, out = env.run(["node", "--version"], None, 15)
    version = out.strip().splitlines()[-1] if out.strip() else ""
    if rc != 0 or not version:
        return DepState(False, f"node --version failed (exit {rc})")
    if not node_version_ok(version):
        return DepState(False, f"node {version}; the web build needs {NODE_RANGE_TEXT}")
    return DepState(True, f"node {version}")


def _read_json(path: Path) -> Any:
    import json

    return json.loads(path.read_text(encoding="utf-8"))


def _no_repo() -> DepState:
    return DepState(False, "needs a git checkout of alb (the test tools live in its web/ and .venv)")


def _check_web_packages(env: HostEnv) -> DepState:
    if env.repo is None:
        return _no_repo()
    web = env.repo / "web"
    wanted = _read_json(web / "package.json").get("devDependencies", {}).get("@playwright/test", "")
    pw = web / "node_modules" / "@playwright" / "test" / "package.json"
    if not pw.is_file():
        return DepState(False, "web/node_modules not installed (npm ci)")
    have = _read_json(pw).get("version", "")
    if have != wanted:
        return DepState(False, f"@playwright/test {have} installed, web/package.json pins {wanted}")
    if not (web / "node_modules" / "vitest" / "package.json").is_file():
        return DepState(False, "vitest missing from web/node_modules (npm ci)")
    return DepState(True, f"@playwright/test {have}, vitest")


def browsers_root(env: HostEnv) -> Path:
    """Where Playwright keeps its browsers (same rules Playwright uses)."""
    custom = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if custom == "0" and env.repo is not None:
        return env.repo / "web" / "node_modules" / "playwright-core" / ".local-browsers"
    if custom and custom != "0":
        return Path(custom).expanduser()
    if env.system == "Darwin":
        return env.home / "Library" / "Caches" / "ms-playwright"
    cache = os.environ.get("XDG_CACHE_HOME")
    return (Path(cache) if cache else env.home / ".cache") / "ms-playwright"


def _wanted_browsers(env: HostEnv) -> dict[str, str] | None:
    """{directory name: revision} for the Chromium builds web/ uses."""
    assert env.repo is not None
    manifest = env.repo / "web" / "node_modules" / "playwright-core" / "browsers.json"
    if not manifest.is_file():
        return None
    out = {}
    for b in _read_json(manifest).get("browsers", []):
        if b.get("name") in ("chromium", "chromium-headless-shell"):
            out[b["name"].replace("-", "_")] = str(b["revision"])
    return out


def _check_browsers(env: HostEnv) -> DepState:
    if env.repo is None:
        return _no_repo()
    wanted = _wanted_browsers(env)
    if wanted is None:
        return DepState(False, "install the web packages first (npm ci)")
    root = browsers_root(env)
    missing = [
        f"{name}-{rev}" for name, rev in wanted.items()
        if not (root / f"{name}-{rev}" / "INSTALLATION_COMPLETE").is_file()
    ]
    if missing:
        return DepState(False, f"not in {root}: {', '.join(missing)}")
    return DepState(True, f"{', '.join(f'{n}-{r}' for n, r in wanted.items())} in {root}")


def _check_browser_libs(env: HostEnv) -> DepState:
    if env.system != "Linux":
        return DepState(True, f"nothing to check on {env.system}")
    if env.repo is None:
        return _no_repo()
    wanted = _wanted_browsers(env)
    rev = (wanted or {}).get("chromium")
    chrome = None
    if rev:
        chrome = next(iter(sorted((browsers_root(env) / f"chromium-{rev}").glob("*/chrome"))), None)
    if chrome is None:
        return DepState(False, "install the browser first, then this is checked")
    rc, out = env.run(["ldd", str(chrome)], None, 30)
    libs = sorted({ln.split("=>")[0].strip() for ln in out.splitlines() if "not found" in ln})
    if libs:
        return DepState(False, f"chromium cannot load: {', '.join(libs)}")
    if rc != 0:
        return DepState(False, f"ldd {chrome} failed (exit {rc})")
    return DepState(True, f"all libraries of {chrome.name} found")


def _check_py_dev(env: HostEnv) -> DepState:
    if env.repo is None:
        return _no_repo()
    pytest = env.repo / ".venv" / "bin" / "pytest"
    return DepState(True, str(pytest)) if pytest.is_file() else DepState(False, f"{pytest} missing")


# ─── Installers ───────────────────────────────────────────────────
def _confirm_download(env: HostEnv, what: str, url: str, terms: str) -> None:
    if env.assume_yes:
        return
    question = f"Download {what} from {url}? Using it means accepting {terms}."
    if not env.confirm(question):
        raise InstallError("download not confirmed (run again with --yes to skip the question)")


def _unpack_zip(zip_path: Path, top: str, must_have: str, dest_parent: Path) -> Path:
    """Unpack a zip whose entries all sit under ``top/`` into ``dest_parent/top``.

    Unpacks next to the target first and swaps it in, so a failed unpack
    leaves an earlier install untouched. Rejects absolute paths and ``..``.
    """
    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as e:
        raise InstallError(f"{zip_path} is not a usable zip file: {e}") from e
    with zf:
        names = zf.namelist()
        if f"{top}/{must_have}" not in names:
            raise InstallError(f"{zip_path} has no {top}/{must_have}")
        staging = dest_parent / f".{top}.unpack"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        for info in zf.infolist():
            p = PurePosixPath(info.filename)
            if p.is_absolute() or ".." in p.parts or not p.parts or p.parts[0] != top:
                shutil.rmtree(staging, ignore_errors=True)
                raise InstallError(f"{zip_path}: unexpected entry {info.filename!r}")
            target = staging.joinpath(*p.parts)
            mode = info.external_attr >> 16
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if stat.S_ISLNK(mode):
                link = zf.read(info).decode()
                if link.startswith("/") or ".." in PurePosixPath(link).parts:
                    shutil.rmtree(staging, ignore_errors=True)
                    raise InstallError(f"{zip_path}: link {info.filename!r} points outside")
                target.symlink_to(link)
                continue
            with zf.open(info) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)
            if mode & 0o777:
                target.chmod(mode & 0o777)
    dest = dest_parent / top
    old = dest_parent / f".{top}.old"
    shutil.rmtree(old, ignore_errors=True)
    if dest.exists():
        dest.rename(old)
    (staging / top).rename(dest)
    shutil.rmtree(old, ignore_errors=True)
    shutil.rmtree(staging, ignore_errors=True)
    return dest


def _link_missing(env: HostEnv, tools: dict[str, Path]) -> list[str]:
    """Link each tool into ~/.local/bin unless that name already resolves.

    A name already on PATH is left alone (see the adb rule in the module
    docstring); an existing ~/.local/bin entry is replaced only when it is
    a link this module made (pointing into ~/.local/share/alb).
    """
    env.bin_dir.mkdir(parents=True, exist_ok=True)
    linked = []
    for name, target in tools.items():
        if shutil.which(name):
            continue
        link = env.bin_dir / name
        if link.is_symlink() and str(os.readlink(link)).startswith(str(env.data_dir)):
            link.unlink()
        elif link.exists() or link.is_symlink():
            continue
        link.symlink_to(target)
        linked.append(name)
    return linked


def _fetch_with_retries(env: HostEnv, url: str, dest: Path) -> None:
    """Download, trying again on failure: a proxy dropping the connection
    half-way is common and usually gone on the next try (2026-11-09: a
    fresh install got 6.8 of 9 MB, then "peer closed connection")."""
    last = "not tried"
    for attempt in range(1, env.download_attempts + 1):
        env.log(f"downloading {url}" + (f" (try {attempt}/{env.download_attempts})" if attempt > 1 else ""))
        try:
            env.fetch(url, dest)
            return
        except Exception as e:  # any download failure is handled the same way
            last = f"{type(e).__name__}: {e}"
            if attempt < env.download_attempts:
                env.log(f"download failed ({last}); trying again in {env.retry_delay * attempt:g}s")
                time.sleep(env.retry_delay * attempt)
    raise InstallError(f"download failed after {env.download_attempts} tries: {url}: {last}")


def _install_platform_tools(env: HostEnv) -> str:
    downloaded = None
    zip_path = env.platform_tools_zip
    if zip_path is None:
        url = os.environ.get("ALB_PLATFORM_TOOLS_URL") or PLATFORM_TOOLS_URLS.get(env.system)
        if not url:
            raise InstallError(f"no Platform-Tools download known for {env.system}")
        _confirm_download(
            env, "Android SDK Platform-Tools", url,
            f"Google's Android SDK license ({PLATFORM_TOOLS_PAGE})",
        )
        env.data_dir.mkdir(parents=True, exist_ok=True)
        downloaded = zip_path = env.data_dir / "platform-tools.zip"
        _fetch_with_retries(env, url, zip_path)
    try:
        dest = _unpack_zip(Path(zip_path).expanduser(), "platform-tools", "adb", env.data_dir)
    finally:
        if downloaded is not None:
            downloaded.unlink(missing_ok=True)
    for tool in ("adb", "fastboot"):
        if (dest / tool).is_file():
            (dest / tool).chmod((dest / tool).stat().st_mode | 0o755)
    linked = _link_missing(env, {t: dest / t for t in ("adb", "fastboot") if (dest / t).is_file()})
    note = f"unpacked into {dest}"
    if linked:
        note += f"; linked {', '.join(linked)} into {env.bin_dir}"
    return note


def _run_or_fail(
    env: HostEnv, argv: Sequence[str], cwd: Path | None, timeout: float,
    verify: Callable[[HostEnv], DepState] | None = None,
) -> None:
    """Run an install command; fail with the end of its output when it
    exits non-zero — or exits 0 but ``verify`` still finds nothing. Exit
    codes alone are not trusted: 2026-11-09 a fresh ``npm ci`` against an
    unreachable registry printed "Exit handler never called!" and exited 0.
    """
    cmd = " ".join(argv)
    env.log(f"running: {cmd}" + (f"  (in {cwd})" if cwd else ""))
    rc, out = env.run_install(argv, cwd, timeout)
    tail = "\n".join(out.splitlines()[-15:])
    if rc != 0:
        raise InstallError(f"`{cmd}` exited {rc}" + (f":\n{tail}" if tail else ""))
    if verify is not None:
        state = verify(env)
        if not state.ok:
            raise InstallError(
                f"`{cmd}` exited 0 but {state.detail}" + (f"; its last output:\n{tail}" if tail else "")
            )


def _install_web_packages(env: HostEnv) -> str:
    if env.repo is None:
        raise InstallError("needs a git checkout of alb")
    if not _check_node(env).ok:
        raise InstallError(f"needs Node.js {NODE_RANGE_TEXT} first")
    _run_or_fail(env, ["npm", "ci"], env.repo / "web", 1800, verify=_check_web_packages)
    return "npm ci done"


def _install_browsers(env: HostEnv) -> str:
    if env.repo is None:
        raise InstallError("needs a git checkout of alb")
    if not _check_web_packages(env).ok:
        raise InstallError("needs the web packages first (npm ci)")
    _run_or_fail(
        env, ["npx", "playwright", "install", "chromium"], env.repo / "web", 1800,
        verify=_check_browsers,
    )
    return f"installed into {browsers_root(env)}"


def _install_py_dev(env: HostEnv) -> str:
    if env.repo is None:
        raise InstallError("needs a git checkout of alb")
    uv = shutil.which("uv") or str(env.bin_dir / "uv")
    _run_or_fail(env, [uv, "sync"], env.repo, 1800, verify=_check_py_dev)
    return "uv sync done"


def _install_mksh(env: HostEnv) -> str:
    if env.system != "Linux" or not (shutil.which("apt-get") and shutil.which("dpkg-deb")):
        raise InstallError("the no-root install needs apt-get and dpkg-deb (Debian / Ubuntu)")
    work = env.data_dir / ".mksh-download"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        _run_or_fail(env, ["apt-get", "download", "mksh"], work, 300)
        debs = sorted(work.glob("mksh_*.deb"))
        if not debs:
            raise InstallError("apt-get download mksh produced no .deb")
        dest = env.data_dir / "mksh"
        shutil.rmtree(dest, ignore_errors=True)
        _run_or_fail(env, ["dpkg-deb", "-x", str(debs[0]), str(dest)], None, 120)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    binary = dest / "bin" / "mksh"
    if not binary.is_file():
        raise InstallError(f"{binary} missing after unpacking")
    linked = _link_missing(env, {"mksh": binary})
    return f"unpacked into {dest}" + (f"; linked mksh into {env.bin_dir}" if linked else "")


# ─── Manual steps ─────────────────────────────────────────────────
def _pkg(apt: str, dnf: str | None = None, brew: str | None = None) -> str:
    parts = [f"Debian/Ubuntu: sudo apt install {apt}"]
    if dnf:
        parts.append(f"Fedora: sudo dnf install {dnf}")
    if brew:
        parts.append(f"macOS: {brew}")
    return " · ".join(parts)


def _manual_platform_tools(env: HostEnv) -> list[str]:
    url = PLATFORM_TOOLS_URLS.get(env.system, PLATFORM_TOOLS_PAGE)
    return [
        f"download Android SDK Platform-Tools: {url}  (page: {PLATFORM_TOOLS_PAGE})",
        "then let alb unpack it: alb setup deps --install --platform-tools-zip <the zip>",
        "or unzip it anywhere and put platform-tools/ on PATH, "
        "or export ALB_ADB_PATH=<dir>/platform-tools/adb",
        "behind a proxy: export HTTPS_PROXY=http://<proxy>:<port> and run "
        "alb setup deps --install again; from a mirror: export ALB_PLATFORM_TOOLS_URL=<zip url>",
        "distro package (often an older adb): "
        + _pkg("adb fastboot", "android-tools", "brew install --cask android-platform-tools"),
    ]


def _manual_ollama(_env: HostEnv) -> list[str]:
    return [
        "install it on this machine or another one: https://ollama.com/download",
        "on another machine: export ALB_OLLAMA_URL=http://<host>:11434",
        "or use an OpenAI-compatible / Anthropic backend instead (docs/llm-integration.md)",
    ]


def _manual_node(_env: HostEnv) -> list[str]:
    return [
        f"the web build needs Node.js {NODE_RANGE_TEXT}",
        "without root, with nvm: https://github.com/nvm-sh/nvm#installing-and-updating "
        "then: nvm install 22",
        "or unpack the official tarball (https://nodejs.org/en/download) under ~/.local "
        "and put its bin/ on PATH",
    ]


def _manual_web_packages(env: HostEnv) -> list[str]:
    return [
        f"cd {env.repo_path('web')} && npm ci",
        "behind a proxy: npm config set proxy http://<proxy>:<port> "
        "&& npm config set https-proxy http://<proxy>:<port>",
        "from a registry mirror: npm config set registry <mirror url>",
    ]


def _manual_browsers(env: HostEnv) -> list[str]:
    return [
        f"cd {env.repo_path('web')} && npx playwright install chromium",
        "behind a proxy: export HTTPS_PROXY=http://<proxy>:<port>; "
        "from a mirror: export PLAYWRIGHT_DOWNLOAD_HOST=<mirror url>",
        "by hand: `npx playwright install chromium --dry-run` prints each zip's URL and "
        "install directory; unzip each into its directory, then create an empty "
        "INSTALLATION_COMPLETE file in it",
        "\"does not support chromium on <os>\": that Playwright no longer builds for this OS; "
        "PLAYWRIGHT_HOST_PLATFORM_OVERRIDE=ubuntu22.04-x64 fetches the generic Linux build "
        "(runs when the system glibc is new enough)",
    ]


def _manual_browser_libs(env: HostEnv) -> list[str]:
    return [
        f"cd {env.repo_path('web')} && sudo npx playwright install-deps chromium",
        "no sudo: ask whoever runs the machine to install the libraries listed above",
    ]


DEPS: tuple[HostDep, ...] = (
    HostDep(
        "adb", "required", "talk to boards over adb (alb's main transport)",
        _check_adb, _manual_platform_tools, _install_platform_tools, "platform-tools",
    ),
    HostDep(
        "fastboot", "optional",
        "flash a board plugged into this machine (bench flashing runs on the Windows side)",
        lambda _e: _which_all("fastboot"), _manual_platform_tools,
        _install_platform_tools, "platform-tools",
    ),
    HostDep(
        "ssh", "optional", "ssh transport (a board running sshd); provides ssh and scp",
        lambda _e: _which_all("ssh", "scp"),
        lambda _e: [_pkg("openssh-client", "openssh-clients", "built in")],
    ),
    HostDep(
        "rsync", "optional", "alb fs rsync over the ssh transport",
        lambda _e: _which_all("rsync"),
        lambda _e: [_pkg("rsync", "rsync", "brew install rsync")],
    ),
    HostDep(
        "picocom", "optional", "interactive serial console outside alb (alb works without it)",
        lambda _e: _which_all("picocom"),
        lambda _e: [_pkg("picocom", "picocom", "brew install picocom")],
    ),
    HostDep(
        "socat", "optional", "bridge a serial port to a pty / TCP by hand (alb works without it)",
        lambda _e: _which_all("socat"),
        lambda _e: [_pkg("socat", "socat", "brew install socat")],
    ),
    HostDep(
        "ollama", "optional",
        "model backend for the web assistant and alb chat (can run on another machine)",
        _check_ollama, _manual_ollama,
    ),
    HostDep(
        "node", "dev", "run the web tests and rebuild the web UI",
        _check_node, _manual_node,
    ),
    HostDep(
        "web-packages", "dev", "vitest and the pinned @playwright/test in web/node_modules",
        _check_web_packages, _manual_web_packages, _install_web_packages,
    ),
    HostDep(
        "playwright-browsers", "dev",
        "Chromium (full and headless shell) for the browser tests and render_compare",
        _check_browsers, _manual_browsers, _install_browsers,
    ),
    HostDep(
        "browser-libs", "dev", "system libraries Chromium needs to start (Linux)",
        _check_browser_libs, _manual_browser_libs,
    ),
    HostDep(
        "python-dev-tools", "dev", "pytest, ruff and mypy in the checkout's .venv",
        _check_py_dev,
        lambda e: [f"cd {e.repo_path()} && uv sync"],
        _install_py_dev,
    ),
    HostDep(
        "mksh", "dev-optional", "one pytest case runs Android's shell (skipped without it)",
        lambda _e: _which_all("mksh"),
        lambda _e: [
            _pkg("mksh"),
            "no root (Debian/Ubuntu): apt-get download mksh && "
            "dpkg-deb -x mksh_*.deb ~/.local/share/alb/mksh, then put "
            "~/.local/share/alb/mksh/bin on PATH",
        ],
        _install_mksh,
    ),
    HostDep(
        "playwright-cli", "dev-optional", "drive a browser from an AI coding agent",
        lambda _e: _which_all("playwright-cli"),
        lambda _e: [
            "npm install -g @playwright/cli  (with npm's prefix in your home, "
            "e.g. npm config set prefix ~/.local)",
            "skills for Claude Code: run `playwright-cli install --skills` from your home "
            "directory, not inside a repo — it writes .claude/skills/ into the current directory",
            "it brings its own, newer Playwright: on an OS that Playwright no longer builds "
            "for, fetch its browser with PLAYWRIGHT_HOST_PLATFORM_OVERRIDE and set "
            "browser.launchOptions.executablePath in ~/.playwright/cli.config.json",
        ],
    ),
)


# ─── Entry points ─────────────────────────────────────────────────
def select(tiers: Sequence[Tier]) -> list[HostDep]:
    return [d for d in DEPS if d.tier in tiers]


def _result(dep: HostDep, env: HostEnv, state: DepState, status: Status) -> DepResult:
    return DepResult(
        name=dep.name, tier=dep.tier, purpose=dep.purpose, status=status,
        detail=state.detail, manual=[] if state.ok else dep.manual(env),
        auto=dep.install is not None,
    )


def check_all(tiers: Sequence[Tier], env: HostEnv) -> list[DepResult]:
    out = []
    for dep in select(tiers):
        state = dep.check(env)
        out.append(_result(dep, env, state, "ok" if state.ok else "missing"))
    return out


def install_missing(tiers: Sequence[Tier], env: HostEnv) -> list[DepResult]:
    """Check each dep; run the installer of those missing; check again.

    Order follows :data:`DEPS`, so Node is looked at before the npm
    packages, and those before the browsers they bring.
    """
    done: dict[str, str | InstallError] = {}
    out = []
    for dep in select(tiers):
        state = dep.check(env)
        key = dep.installer_key or dep.name
        if state.ok or dep.install is None:
            # fastboot comes in with adb's installer: say so, not "was there".
            fresh = state.ok and key in done and not isinstance(done[key], InstallError)
            status: Status = "installed" if fresh else ("ok" if state.ok else "missing")
            out.append(_result(dep, env, state, status))
            continue
        if key not in done:
            env.log(f"installing {key} …")
            try:
                done[key] = dep.install(env)
            except InstallError as e:
                done[key] = e
        outcome = done[key]
        after = dep.check(env)
        if isinstance(outcome, InstallError):
            r = _result(dep, env, DepState(False, f"{outcome} (was: {state.detail})"), "failed")
        elif after.ok:
            r = _result(dep, env, DepState(True, f"{after.detail} — {outcome}"), "installed")
        else:
            hint = ""
            if env.bin_dir.is_dir() and str(env.bin_dir) not in os.environ.get("PATH", "").split(os.pathsep):
                hint = f"; {env.bin_dir} is not on PATH: export PATH=\"{env.bin_dir}:$PATH\""
            r = _result(dep, env, DepState(False, f"{outcome}, but still: {after.detail}{hint}"), "failed")
        out.append(r)
    return out


def blocking(results: Sequence[DepResult], tiers: Sequence[Tier]) -> list[DepResult]:
    """Results that should fail the run: required ones, plus dev ones when asked for."""
    hard = {"required"} | ({"dev"} if "dev" in tiers else set())
    return [r for r in results if r.tier in hard and not r.ok]
