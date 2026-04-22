#!/usr/bin/env bash
# Sensitive-word guard for staged changes.
# Used both by the pre-commit hook and by CI.
#
# Exit codes:
#   0  no leaks
#   1  leaks found (details printed)
#   2  bad usage
#
# Scope:
#   - by default scans STAGED files (pre-commit mode)
#   - pass --all to scan the entire working tree (CI mode)
#
# Environment overrides:
#   ALB_SENSITIVE_EXTRA  = colon-separated extra patterns to check (rare)

set -euo pipefail

MODE="staged"
if [[ "${1:-}" == "--all" ]]; then
  MODE="all"
  shift
fi

# Pattern list. Each row = ERE regex. Keep case-insensitive — add (?i) inlined as -i flag below.
PATTERNS=(
  # Company / brand
  'pax(sz)?(\.com)?'
  'com\.pax'
  # SoC / vendor-customer specific
  'rk3576'
  'rk[-_ ]?sdk'
  'rockchip[-_ ]sdk'
  # Short internal handle (word-bounded so it doesn't match the public
  # github handle skyzhangbinghua which is a legitimate attribution string)
  '\bzhangbh\b'
  # Home-dir leaks
  '/home/zhangbh'
  '/home/[a-z][a-z0-9_-]*/(android-llm-bridge|adb-via-|\.claude)'
  # Internal RFC1918 IPs belonging to known private networks
  '10\.0\.25\.[0-9]{1,3}'
  '172\.16\.2\.[0-9]{1,3}'
)

if [[ -n "${ALB_SENSITIVE_EXTRA:-}" ]]; then
  IFS=':' read -r -a EXTRA <<< "$ALB_SENSITIVE_EXTRA"
  PATTERNS+=("${EXTRA[@]}")
fi

# Files that are legitimately allowed to mention these words.
# Keep this short. Each entry is a glob applied to the path.
ALLOWED=(
  'CLAUDE.md'              # defines the rule itself
  'CONTRIBUTING.md'         # same
  'scripts/check_sensitive_words.sh'  # this script
  '.pre-commit-config.yaml'
  '.gitignore'
)

is_allowed() {
  local path="$1"
  for glob in "${ALLOWED[@]}"; do
    if [[ "$path" == $glob ]]; then return 0; fi
  done
  return 1
}

# Collect file list.
FILES=()
if [[ "$MODE" == "staged" ]]; then
  while IFS= read -r -d '' f; do FILES+=("$f"); done \
    < <(git diff --cached --name-only --diff-filter=ACMR -z)
else
  while IFS= read -r -d '' f; do FILES+=("$f"); done \
    < <(git ls-files -z)
fi

if [[ ${#FILES[@]} -eq 0 ]]; then
  exit 0
fi

FAILED=0

scan_file() {
  local path="$1"
  if is_allowed "$path"; then return 0; fi
  [[ ! -f "$path" ]] && return 0       # deleted / moved
  # Skip binaries
  if file --mime "$path" | grep -q 'charset=binary'; then return 0; fi

  for pat in "${PATTERNS[@]}"; do
    # Case-insensitive grep (-i). Show filename + line number + offending line.
    if matches=$(grep -nEi -- "$pat" "$path" 2>/dev/null); then
      echo "---"
      echo "[sensitive] $path"
      echo "  pattern: $pat"
      echo "$matches" | sed 's/^/    /'
      FAILED=1
    fi
  done
}

# Also scan the commit message itself when run in staged mode AND a merge
# message file is pending (pre-commit's COMMIT_EDITMSG isn't available here,
# so we leave this to commit-msg hook — see .pre-commit-config.yaml).

for f in "${FILES[@]}"; do
  scan_file "$f"
done

if [[ $FAILED -eq 1 ]]; then
  echo ""
  echo "Sensitive-word guard: one or more patterns found above."
  echo "See CLAUDE.md § \"Banned words\" for the policy and generic alternatives."
  exit 1
fi

exit 0
