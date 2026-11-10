#!/usr/bin/env bash
# android-llm-bridge · install script
#
# Install the project into a user-local, isolated Python environment.
# Does NOT require root. Does NOT touch the system Python (/usr/bin/python3).
# Safe to run on a shared server — zero impact on other users.
#
# See scripts/uninstall.sh to undo everything.

set -euo pipefail

# Lock down PATH so the script always finds the same tools regardless of
# whatever the caller had in their environment. This is important on
# shared servers where user PATHs may include pyenv / conda / etc.
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${HOME}/.local/bin:${HOME}/.cargo/bin"
export PYTHONNOUSERSITE=1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ─── Colors ──────────────────────────────────────────────────────
if [[ -t 1 ]]; then
    RED='\033[31m'; GREEN='\033[32m'; YELLOW='\033[33m'; BLUE='\033[34m'; DIM='\033[2m'; NC='\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; BLUE=''; DIM=''; NC=''
fi

log_info() { printf "${BLUE}[INFO]${NC} %s\n" "$*"; }
log_ok()   { printf "${GREEN}[ OK ]${NC} %s\n" "$*"; }
log_warn() { printf "${YELLOW}[WARN]${NC} %s\n" "$*"; }
log_err()  { printf "${RED}[FAIL]${NC} %s\n" "$*" >&2; }
indent()   { sed 's/^/       /'; }

# ─── Args ────────────────────────────────────────────────────────
MODIFY_PATH=0
SKIP_SMOKE=0
WITH_DEV=0
ASSUME_YES=0
PYTHON_VERSION="3.11"

usage() {
    cat <<USAGE
Usage: $(basename "$0") [options]

Install android-llm-bridge into a user-local Python 3.11+ environment.
Requires no root.

Options:
  --with-uv-in-path    Append ~/.local/bin to your ~/.bashrc (or ~/.zshrc).
                       Only affects YOUR user; other users are untouched.
  --skip-smoke-test    Skip post-install smoke tests.
  --python VERSION     Python version uv should install (default: 3.11).
  --dev                Also install what running the tests needs: the npm
                       packages in web/ and Playwright's Chromium (needs
                       Node.js; the script says how to get it if missing).
  --yes, -y            Do not ask before downloading Android Platform-Tools.
  -h, --help           Show this help.

What this script does (nothing outside your home directory):
  1. Refuse to run as root.
  2. Install uv into ~/.local/bin (if not already present): the official
     installer first (astral.sh / github.com); if that fails, pip downloads
     the same program from PyPI. If both fail it prints why and how to fix.
     uv is one prebuilt program; nothing is compiled.
  3. Use a Python ${PYTHON_VERSION} this machine already has, or ask uv to
     download one into ~/.local/share/uv/python/.
  4. Run 'uv sync' in the project repo to create .venv/ with all deps.
  5. Run a pytest smoke test (tests/test_smoke.py).
  6. Check the outside tools pip cannot install ('alb setup deps --install'):
     adb / fastboot are downloaded into ~/.local/share/alb and linked into
     ~/.local/bin when missing; tools that need sudo (ssh, rsync, picocom,
     socat) or another machine (Ollama) are listed with how to install them.
  7. Print next-step hints.

Isolation guarantees:
  - System /usr/bin/python3 is NOT modified.
  - No sudo is ever invoked.
  - uv-managed Python lands in ~/.local/share/uv/python/ (user-only).
  - Project venv lands in ${REPO_ROOT}/.venv/ (project-only).
  - Other users on this machine are unaffected.
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --with-uv-in-path) MODIFY_PATH=1; shift ;;
        --skip-smoke-test) SKIP_SMOKE=1; shift ;;
        --python) PYTHON_VERSION="${2:?missing value for --python}"; shift 2 ;;
        --dev) WITH_DEV=1; shift ;;
        --yes|-y) ASSUME_YES=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) log_err "unknown option: $1"; echo; usage; exit 1 ;;
    esac
done

# ─── Step 0 · safety checks ──────────────────────────────────────
if [[ ${EUID} -eq 0 ]]; then
    log_err "Refusing to run as root. This installer is user-local by design."
    log_err "Re-run as a normal user."
    exit 1
fi

if [[ ! -f "${REPO_ROOT}/pyproject.toml" ]]; then
    log_err "pyproject.toml not found at ${REPO_ROOT}."
    log_err "Is ${SCRIPT_DIR} really a scripts/ dir inside the android-llm-bridge repo?"
    exit 1
fi

log_info "Installing into: ${REPO_ROOT}"
log_info "User: $(id -un) (uid=${EUID})"
log_info "Target Python: ${PYTHON_VERSION}"
echo

# ─── Step 1 · uv ─────────────────────────────────────────────────
# uv is the installer the rest of this script relies on: it fetches a
# Python 3.11 without root, builds .venv/ from uv.lock (the exact package
# versions the tests ran with) and runs alb inside it. It is one prebuilt
# program -- nothing gets compiled. Tried in this order:
#   1. a uv that is already installed
#   2. the official installer from astral.sh (it fetches the binary from
#      releases.astral.sh, then github.com)
#   3. pip, used only as a downloader: it fetches the same binary from PyPI
#      as a wheel; uv and uvx are copied out of it into ~/.local/bin, so no
#      Python's site-packages is touched (and distro pips that refuse
#      "pip install" outside a venv still allow "pip download")
UV_DIR="${HOME}/.local/bin"
UV_BIN=""
UV_WHY_OFFICIAL="not tried"
UV_WHY_PIP="not tried"
UV_NO_PIP=0
UV_TMP="$(mktemp -d)"
trap 'rm -rf "${UV_TMP}"' EXIT

# Last non-empty line of a log: what the tool itself said when it gave up.
last_line() { grep -v '^[[:space:]]*$' "$1" 2>/dev/null | tail -n 1 | sed 's/^[[:space:]]*//' | cut -c1-200; }

uv_via_official() {
    local script="${UV_TMP}/uv-installer.sh" log="${UV_TMP}/official.log" fetched=0
    log_info "Installing uv with the official installer (https://astral.sh/uv/install.sh)..."
    if command -v curl >/dev/null 2>&1; then
        curl -LsSf --connect-timeout 20 -o "${script}" https://astral.sh/uv/install.sh >"${log}" 2>&1 && fetched=1
    elif command -v wget >/dev/null 2>&1; then
        wget -nv --timeout=20 -O "${script}" https://astral.sh/uv/install.sh >"${log}" 2>&1 && fetched=1
    else
        UV_WHY_OFFICIAL="neither curl nor wget is installed"
        return 1
    fi
    if [[ ${fetched} -ne 1 ]]; then
        UV_WHY_OFFICIAL="could not download the installer from astral.sh: $(last_line "${log}")"
        return 1
    fi
    # UV_INSTALL_DIR pins the target; otherwise $XDG_BIN_HOME would win.
    if ! env INSTALLER_NO_MODIFY_PATH=1 UV_INSTALL_DIR="${UV_DIR}" sh "${script}" 2>&1 | tee "${log}" | indent; then
        # Its last line is boilerplate ("feel free to open an issue");
        # the download tool's own error line says what went wrong.
        local said
        said="$(grep -m1 -E '^(curl|wget): |failed to download' "${log}" || true)"
        [[ -n "${said}" ]] || said="$(last_line "${log}")"
        UV_WHY_OFFICIAL="the installer ran but could not fetch uv: ${said}"
        return 1
    fi
    if [[ ! -x "${UV_DIR}/uv" ]]; then
        UV_WHY_OFFICIAL="the installer finished but ${UV_DIR}/uv is not there"
        return 1
    fi
    UV_BIN="${UV_DIR}/uv"
}

uv_via_pip() {
    local py log="${UV_TMP}/pip.log" wheels="${UV_TMP}/wheel"
    py="$(command -v python3 || true)"
    if [[ -z "${py}" ]]; then
        UV_WHY_PIP="python3 is not installed"; UV_NO_PIP=1
        return 1
    fi
    if ! "${py}" -m pip --version >/dev/null 2>&1; then
        UV_WHY_PIP="${py} has no pip"; UV_NO_PIP=1
        return 1
    fi
    log_info "Trying pip instead: downloading uv from PyPI (${py} -m pip download uv)..."
    if ! "${py}" -m pip download uv --only-binary :all: --no-deps \
            --disable-pip-version-check -d "${wheels}" 2>&1 | tee "${log}" | indent; then
        local said net
        said="$(last_line "${log}")"
        # pip's own last line hides network trouble behind "No matching
        # distribution"; prefer a connection error when the log has one.
        net="$(grep -m1 -oE '(ProxyError|NewConnectionError|ConnectTimeout|ReadTimeout|SSLError|Temporary failure in name resolution)[^)]{0,120}' "${log}" || true)"
        if [[ -n "${net}" ]]; then
            said="${net}"
        elif grep -q 'No matching distribution' "${log}"; then
            said="${said} (pip says this when it cannot reach PyPI, and also when PyPI has no uv build for this system)"
        fi
        UV_WHY_PIP="pip could not download uv from PyPI: ${said}"
        return 1
    fi
    mkdir -p "${UV_DIR}"
    # A wheel is a zip; uv's carries the two programs as uv-<ver>.data/scripts/{uv,uvx}.
    if ! "${py}" - "${wheels}" "${UV_DIR}" >"${log}" 2>&1 <<'PY'
import glob, os, sys, zipfile
src_dir, dest = sys.argv[1], sys.argv[2]
found = glob.glob(os.path.join(src_dir, "uv-*.whl"))
if not found:
    sys.exit("pip finished but no uv-*.whl was downloaded")
copied = 0
with zipfile.ZipFile(found[0]) as z:
    for name in z.namelist():
        parts = name.split("/")
        if len(parts) == 3 and parts[0].endswith(".data") and parts[1] == "scripts" \
                and parts[2] in ("uv", "uvx"):
            target = os.path.join(dest, parts[2])
            with z.open(name) as src, open(target + ".tmp", "wb") as out:
                out.write(src.read())
            os.chmod(target + ".tmp", 0o755)
            os.replace(target + ".tmp", target)
            copied += 1
if not copied:
    sys.exit("the wheel has no uv program under *.data/scripts/")
print("copied %d program(s) from %s to %s" % (copied, os.path.basename(found[0]), dest))
PY
    then
        indent < "${log}"
        UV_WHY_PIP="downloaded the uv wheel but could not unpack it: $(last_line "${log}")"
        return 1
    fi
    indent < "${log}"
    if ! "${UV_DIR}/uv" --version >/dev/null 2>&1; then
        UV_WHY_PIP="unpacked ${UV_DIR}/uv but it does not run on this machine"
        return 1
    fi
    UV_BIN="${UV_DIR}/uv"
}

uv_triple() {
    case "$(uname -s)-$(uname -m)" in
        Linux-x86_64)               echo "x86_64-unknown-linux-gnu" ;;
        Linux-aarch64|Linux-arm64)  echo "aarch64-unknown-linux-gnu" ;;
        Darwin-arm64)               echo "aarch64-apple-darwin" ;;
        Darwin-x86_64)              echo "x86_64-apple-darwin" ;;
        *)                          echo "" ;;
    esac
}

uv_install_help() {
    local triple
    triple="$(uv_triple)"
    log_err "Could not install uv. The rest of this script uses it to set up Python and the project's packages."
    log_err "Why:"
    log_err "  official installer: ${UV_WHY_OFFICIAL}"
    log_err "  pip (PyPI):         ${UV_WHY_PIP}"
    log_err "uv is one prebuilt program; nothing needs to be compiled. Any one of these fixes it, then re-run ./scripts/install.sh:"
    log_err "  1. Behind a proxy:"
    log_err "       export HTTPS_PROXY=http://<proxy>:<port> HTTP_PROXY=http://<proxy>:<port>"
    if [[ ${UV_NO_PIP} -eq 1 ]]; then
        log_err "  2. Get pip, so the script can fetch uv from PyPI (or a PyPI mirror):"
        log_err "       sudo apt install python3-pip        # Debian / Ubuntu; other systems: their python3 pip package"
    else
        log_err "  2. PyPI reachable only through a mirror (the pip step uses it):"
        log_err "       export PIP_INDEX_URL=<PyPI mirror URL>"
    fi
    log_err "  3. github.com reachable only through a mirror (the official installer uses it):"
    log_err "       export UV_INSTALLER_GITHUB_BASE_URL=<mirror that serves https://github.com paths>"
    if [[ -n "${triple}" ]]; then
        log_err "  4. No network here: on another machine download"
        log_err "       https://github.com/astral-sh/uv/releases/latest/download/uv-${triple}.tar.gz"
        log_err "     copy it to this machine, then:"
        log_err "       tar -xzf uv-${triple}.tar.gz && mkdir -p ~/.local/bin && cp uv-${triple}/uv uv-${triple}/uvx ~/.local/bin/"
    else
        log_err "  4. No network here: download the uv archive for $(uname -s) $(uname -m) from"
        log_err "       https://github.com/astral-sh/uv/releases  and copy uv and uvx into ~/.local/bin/"
    fi
    log_err "Already have Python 3.11+? uv is optional: see docs/install.md, \"Installing without uv\"."
}

if [[ -x "${UV_DIR}/uv" ]]; then
    UV_BIN="${UV_DIR}/uv"
elif command -v uv >/dev/null 2>&1; then
    UV_BIN="$(command -v uv)"
fi

if [[ -n "${UV_BIN}" ]]; then
    log_ok "uv already installed: ${UV_BIN} ($(${UV_BIN} --version))"
else
    if ! uv_via_official; then
        log_warn "Official installer failed: ${UV_WHY_OFFICIAL}"
        if ! uv_via_pip; then
            uv_install_help
            exit 1
        fi
    fi
    log_ok "uv installed: ${UV_BIN} ($(${UV_BIN} --version))"
fi

# ─── Step 2 · (optional) persist PATH ────────────────────────────
if [[ ${MODIFY_PATH} -eq 1 ]]; then
    RC_FILE="${HOME}/.bashrc"
    # Prefer zshrc if the user is on zsh
    if [[ -n "${ZSH_VERSION:-}" || "${SHELL:-}" == */zsh ]]; then
        RC_FILE="${HOME}/.zshrc"
    fi
    LINE='export PATH="$HOME/.local/bin:$PATH"   # added by android-llm-bridge installer'
    if grep -qF 'android-llm-bridge installer' "${RC_FILE}" 2>/dev/null; then
        log_ok "PATH entry already present in ${RC_FILE}"
    else
        touch "${RC_FILE}"
        printf '\n%s\n' "${LINE}" >> "${RC_FILE}"
        log_ok "Appended PATH entry to ${RC_FILE}"
        log_info "Run 'source ${RC_FILE}' or re-open your shell to pick it up."
    fi
fi

# ─── Step 3 · Python ─────────────────────────────────────────────
# alb needs Python >= 3.11 (pyproject.toml). Use one this machine already
# has (an earlier uv download, or e.g. /usr/bin/python3.11); download only
# when there is none. --system skips the project's own .venv.
PY_FOUND="$("${UV_BIN}" python find --system "${PYTHON_VERSION}" 2>/dev/null || true)"
if [[ -n "${PY_FOUND}" ]]; then
    log_ok "Python ${PYTHON_VERSION} already on this machine: ${PY_FOUND} (nothing to download)"
else
    log_info "Asking uv to download Python ${PYTHON_VERSION} (prebuilt, from github.com/astral-sh/python-build-standalone)..."
    if ! "${UV_BIN}" python install "${PYTHON_VERSION}" 2>&1 | tee "${UV_TMP}/python.log" | indent; then
        log_err "Could not download Python ${PYTHON_VERSION}."
        log_err "Why: $(last_line "${UV_TMP}/python.log")"
        log_err "alb needs Python ${PYTHON_VERSION}+ and this machine has none. Any one of these fixes it, then re-run ./scripts/install.sh:"
        log_err "  1. Behind a proxy:"
        log_err "       export HTTPS_PROXY=http://<proxy>:<port> HTTP_PROXY=http://<proxy>:<port>"
        log_err "  2. github.com reachable only through a mirror:"
        log_err "       export UV_PYTHON_INSTALL_MIRROR=<mirror of https://github.com/astral-sh/python-build-standalone/releases/download>"
        log_err "  3. No network here: the output above shows the URL uv tried. Download that file on another machine,"
        log_err "     put it at <dir>/<the date part of the URL>/<file name, with %2B written as +>, then:"
        log_err "       export UV_PYTHON_INSTALL_MIRROR=file://<dir>"
        log_err "  4. Install Python ${PYTHON_VERSION} from your OS package manager; the next run finds it and skips the download."
        log_err "More: docs/install.md (troubleshooting)."
        exit 1
    fi
    log_ok "Python ${PYTHON_VERSION} ready (managed by uv, under ~/.local/share/uv/python/)"
fi

# ─── Step 4 · uv sync ────────────────────────────────────────────
cd "${REPO_ROOT}"
log_info "Running 'uv sync' (creates .venv/ and installs all project dependencies)..."
if ! "${UV_BIN}" sync 2>&1 | tee "${UV_TMP}/sync.log" | indent; then
    log_err "uv sync failed — the Python packages (typer, fastapi, pyserial, ...) did not install."
    log_err "Why: $(last_line "${UV_TMP}/sync.log")"
    log_err "Usual causes and fixes (packages already downloaded are cached, so a re-run picks up where this stopped):"
    log_err "  behind a proxy:     export HTTPS_PROXY=http://<proxy>:<port>   then re-run this script"
    log_err "  slow link, 'operation timed out': export UV_HTTP_TIMEOUT=120   then re-run (seconds per request)"
    log_err "  PyPI unreachable:   export UV_DEFAULT_INDEX=<a PyPI mirror URL> then re-run (older uv: UV_INDEX_URL)"
    log_err "  see the full error: cd ${REPO_ROOT} && ${UV_BIN} sync -v"
    log_err "More: docs/install.md (troubleshooting)."
    exit 1
fi
if [[ ! -d "${REPO_ROOT}/.venv" ]]; then
    log_err "uv sync finished but .venv/ was not created. Check the output above."
    exit 1
fi
log_ok ".venv/ ready at ${REPO_ROOT}/.venv"

# ─── Step 5 · smoke test ─────────────────────────────────────────
if [[ ${SKIP_SMOKE} -eq 0 ]]; then
    log_info "Running smoke tests..."
    if "${UV_BIN}" run pytest -q tests/test_smoke.py 2>&1 | indent; then
        log_ok "Smoke tests passed"
    else
        log_warn "Smoke tests failed. Install completed but please check the output above."
    fi
else
    log_info "Skipping smoke tests (--skip-smoke-test)"
fi

# ─── Step 6 · outside tools (adb, ssh, ... — pip cannot install these) ──
DEPS_ARGS=(--install)
[[ ${WITH_DEV} -eq 1 ]] && DEPS_ARGS+=(--dev)
[[ ${ASSUME_YES} -eq 1 ]] && DEPS_ARGS+=(--yes)
log_info "Checking outside tools: alb setup deps ${DEPS_ARGS[*]}"
DEPS_OK=1
"${UV_BIN}" run --project "${REPO_ROOT}" alb setup deps "${DEPS_ARGS[@]}" || DEPS_OK=0

# ─── Summary ─────────────────────────────────────────────────────
echo
if [[ ${DEPS_OK} -eq 1 ]]; then
    log_ok "android-llm-bridge installed successfully."
else
    log_warn "android-llm-bridge's Python part is installed, but a tool it needs is missing."
    log_warn "The list above says how to install each one. A download that broke off often"
    log_warn "works on the next try; to try the installs again, or to check after fixing by hand:"
    log_warn "  cd ${REPO_ROOT} && uv run alb setup deps --install$( [[ ${WITH_DEV} -eq 1 ]] && echo ' --dev')"
fi
echo
cat <<NEXT
${GREEN}Next steps:${NC}

  ${DIM}# Make sure ~/.local/bin is on your PATH (just for your shell):${NC}
  export PATH="\$HOME/.local/bin:\$PATH"

  ${DIM}# Try it out:${NC}
  cd ${REPO_ROOT}
  uv run alb describe                  # list all transports + capabilities
  uv run alb status                    # health check
  uv run alb devices                   # list connected devices (needs transport setup)

  ${DIM}# Use from Claude Code or Codex CLI (registers the MCP server once):${NC}
  claude mcp add alb -- uv run --project "${REPO_ROOT}" alb-mcp
  codex mcp add alb -- uv run --project "${REPO_ROOT}" alb-mcp

${DIM}Docs:      ${REPO_ROOT}/docs/
Uninstall: ${REPO_ROOT}/scripts/uninstall.sh${NC}
NEXT
[[ ${DEPS_OK} -eq 1 ]] || exit 1
