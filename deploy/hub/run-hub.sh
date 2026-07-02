#!/usr/bin/env bash
# One-command hub launcher: loads hub.env (next to this script) and starts
# alb-api. Extra arguments are passed through.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/hub.env"

if [ ! -f "$ENV_FILE" ]; then
    echo "[run-hub] hub.env not found." >&2
    echo "[run-hub] Copy hub.env.example to hub.env and fill in ALB_AGENT_TOKEN" >&2
    echo "[run-hub] (+ serial COM/baud if you want the UART console):" >&2
    echo "[run-hub]     cp '$SCRIPT_DIR/hub.env.example' '$ENV_FILE'" >&2
    exit 1
fi

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

if [ "${ALB_AGENT_TOKEN:-}" = "change-me" ]; then
    echo "[run-hub] WARNING: ALB_AGENT_TOKEN is still 'change-me' — set a real" >&2
    echo "[run-hub] token in hub.env (generate one: openssl rand -hex 16)." >&2
fi

# Prefer an installed alb-api; fall back to `uv run` inside a repo checkout.
# Print which one runs — an old `pip install .` would otherwise silently
# shadow the checkout you are editing.
if command -v alb-api >/dev/null 2>&1; then
    echo "[run-hub] using $(command -v alb-api)"
    exec alb-api "$@"
fi
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
if [ -f "$REPO_ROOT/pyproject.toml" ] && command -v uv >/dev/null 2>&1; then
    echo "[run-hub] using uv run alb-api in $REPO_ROOT"
    cd "$REPO_ROOT"
    exec uv run alb-api "$@"
fi
echo "[run-hub] alb-api is not on PATH and no uv checkout was found." >&2
echo "[run-hub] Install the package (pip install .) or install uv." >&2
exit 1
