#!/usr/bin/env bash
# android-llm-bridge · uninstall script
#
# Remove the project's local Python environment and optionally the
# runtime artifacts, uv, and uv-managed Pythons. Completely user-local.
# Does NOT require root. Never touches system Python.
#
# By default this script is CONSERVATIVE — it only removes .venv/ and
# Python caches. Use flags to remove more.

set -euo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${HOME}/.local/bin"
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

# ─── Args ────────────────────────────────────────────────────────
FORCE=0
PURGE_WORKSPACE=0
REMOVE_UV=0
REMOVE_UV_PYTHON=0

usage() {
    cat <<USAGE
Usage: $(basename "$0") [options]

Uninstall android-llm-bridge's local artifacts. Does NOT require root.

By default this script removes ONLY:
  - ${REPO_ROOT}/.venv/
  - Python caches (__pycache__, .mypy_cache, .ruff_cache, .pytest_cache, htmlcov)

Options:
  --force              Skip interactive confirmation.

  --purge-workspace    Also delete ${REPO_ROOT}/workspace/* (but keep .gitkeep).
                       ${YELLOW}⚠ This removes collected logs, ANR traces, bugreports —${NC}
                       ${YELLOW}  i.e. debugging evidence you've gathered.${NC}

  --remove-uv          Delete ~/.local/bin/uv (and uvx).
                       ${YELLOW}⚠ Only do this if no other project on your account uses uv.${NC}

  --remove-uv-python   Delete ~/.local/share/uv/ entirely (uv cache + all
                       uv-managed Python installations).
                       ${YELLOW}⚠ Only if you won't use uv again.${NC}

  -h, --help           Show this help.

What this script NEVER touches:
  - System Python (/usr/bin/python3) — safe for shared servers.
  - ~/.bashrc / ~/.zshrc PATH lines — reported, but not auto-removed.
  - /etc, /usr, /opt — no root, no system changes.
  - Your project source code and .git history.
  - workspace/ contents (unless --purge-workspace).
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --force) FORCE=1; shift ;;
        --purge-workspace) PURGE_WORKSPACE=1; shift ;;
        --remove-uv) REMOVE_UV=1; shift ;;
        --remove-uv-python) REMOVE_UV_PYTHON=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) log_err "unknown option: $1"; echo; usage; exit 1 ;;
    esac
done

# ─── Safety checks ───────────────────────────────────────────────
if [[ ${EUID} -eq 0 ]]; then
    log_err "Refusing to run as root."
    exit 1
fi

if [[ ! -f "${REPO_ROOT}/pyproject.toml" ]]; then
    log_err "pyproject.toml not found at ${REPO_ROOT}. Is this the right repo?"
    exit 1
fi

# ─── Confirmation ────────────────────────────────────────────────
if [[ ${FORCE} -eq 0 ]]; then
    echo "About to uninstall from: ${REPO_ROOT}"
    echo
    echo "Will delete:"
    [[ -d "${REPO_ROOT}/.venv" ]] && echo "  • ${REPO_ROOT}/.venv/"
    echo "  • Python caches under ${REPO_ROOT} (__pycache__, .mypy_cache, ...)"
    [[ ${PURGE_WORKSPACE} -eq 1 && -d "${REPO_ROOT}/workspace" ]] && \
        echo "  • ${REPO_ROOT}/workspace/* (${RED}PURGE — all collected logs/ANRs/bugreports${NC})"
    [[ ${REMOVE_UV} -eq 1 && -f "${HOME}/.local/bin/uv" ]] && \
        echo "  • ~/.local/bin/uv and ~/.local/bin/uvx"
    [[ ${REMOVE_UV_PYTHON} -eq 1 && -d "${HOME}/.local/share/uv" ]] && \
        echo "  • ~/.local/share/uv/ (${RED}ALL uv-managed Pythons${NC})"
    echo
    read -rp "Continue? [y/N] " ans
    if [[ ! "${ans}" =~ ^[Yy]$ ]]; then
        log_info "Aborted. Nothing was changed."
        exit 0
    fi
fi

# ─── Step 1 · .venv ──────────────────────────────────────────────
if [[ -d "${REPO_ROOT}/.venv" ]]; then
    rm -rf "${REPO_ROOT}/.venv"
    log_ok "Removed ${REPO_ROOT}/.venv/"
else
    log_info ".venv/ not present — skipping"
fi

# ─── Step 2 · caches ─────────────────────────────────────────────
for cache in .mypy_cache .ruff_cache .pytest_cache htmlcov coverage.xml .coverage .uv_cache; do
    if [[ -e "${REPO_ROOT}/${cache}" ]]; then
        rm -rf "${REPO_ROOT}/${cache}"
        log_ok "Removed ${cache}"
    fi
done
# __pycache__ all the way down
if find "${REPO_ROOT}/src" "${REPO_ROOT}/tests" -type d -name __pycache__ 2>/dev/null | grep -q .; then
    find "${REPO_ROOT}/src" "${REPO_ROOT}/tests" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
    log_ok "Removed __pycache__/ directories"
fi

# ─── Step 3 · workspace (optional) ───────────────────────────────
if [[ ${PURGE_WORKSPACE} -eq 1 ]]; then
    if [[ -d "${REPO_ROOT}/workspace" ]]; then
        # Keep .gitkeep, delete everything else
        find "${REPO_ROOT}/workspace" -mindepth 1 -not -name '.gitkeep' -print0 2>/dev/null \
            | xargs -0 rm -rf 2>/dev/null || true
        log_ok "Purged workspace/ contents (kept .gitkeep)"
    else
        log_info "workspace/ not present — skipping"
    fi
fi

# ─── Step 4 · uv itself (optional) ───────────────────────────────
if [[ ${REMOVE_UV} -eq 1 ]]; then
    removed_any=0
    for bin in uv uvx; do
        if [[ -f "${HOME}/.local/bin/${bin}" ]]; then
            rm -f "${HOME}/.local/bin/${bin}"
            log_ok "Removed ~/.local/bin/${bin}"
            removed_any=1
        fi
    done
    [[ ${removed_any} -eq 0 ]] && log_info "No uv binary found in ~/.local/bin/"
fi

# ─── Step 5 · uv-managed Pythons (optional) ──────────────────────
if [[ ${REMOVE_UV_PYTHON} -eq 1 ]]; then
    if [[ -d "${HOME}/.local/share/uv" ]]; then
        rm -rf "${HOME}/.local/share/uv"
        log_ok "Removed ~/.local/share/uv/ (all uv-managed Pythons + cache)"
    fi
    if [[ -d "${HOME}/.cache/uv" ]]; then
        rm -rf "${HOME}/.cache/uv"
        log_ok "Removed ~/.cache/uv/"
    fi
fi

# ─── Step 6 · PATH notice ────────────────────────────────────────
for rc in "${HOME}/.bashrc" "${HOME}/.zshrc"; do
    if [[ -f "${rc}" ]] && grep -q 'android-llm-bridge installer' "${rc}" 2>/dev/null; then
        log_warn "${rc} still contains a PATH line added by the installer:"
        grep -n 'android-llm-bridge installer' "${rc}" | sed 's/^/       /'
        log_warn "Remove it manually if you no longer need ~/.local/bin on PATH."
    fi
done

# ─── Summary ─────────────────────────────────────────────────────
echo
log_ok "Uninstall complete."
cat <<SUMMARY

${DIM}What was NOT touched:${NC}
  - System Python (/usr/bin/python3)
  - /etc, /usr, /opt (no root, no system changes anywhere)
  - Your project source + .git
  - workspace/ (unless --purge-workspace)
  - uv itself (unless --remove-uv)
  - uv-managed Pythons (unless --remove-uv-python)

${DIM}To fully remove the repo too:${NC}
    rm -rf "${REPO_ROOT}"
SUMMARY
