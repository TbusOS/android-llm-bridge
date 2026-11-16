#!/usr/bin/env bash
# Sensitive-word guard for staged changes, the working tree, or given files.
# Used by the pre-commit hooks (files and commit message) and by CI.
#
# Exit codes:
#   0  no leaks
#   1  leaks found (details printed)
#   2  bad usage, or --require-private and the private word list is missing
#
# Modes:
#   (default)          scan STAGED files (pre-commit mode)
#   --all              scan the whole working tree, tracked and untracked (CI mode)
#   --file PATH...     scan the given files, e.g. a commit message (commit-msg hook)
#   --self-test        check the patterns against the built-in sample lines
#                      (SELF_TEST_CASES below); scans no files
#
# Options (before the mode):
#   --require-private  fail with exit 2 if the private word list is not found
#
# Two kinds of patterns:
#
#   1. Generic patterns, written below. They describe a KIND of leak (a
#      checkout of this repository under somebody's home directory) and hold
#      no real value, so they can live in this public file.
#   2. The private word list: the real identifiers that must never be
#      published (company names, internal handles, internal network
#      addresses, host names...). Writing them here would publish them, so
#      they live in a file outside this repository, one extended regex per
#      line, '#' starts a comment:
#        $DHANDS_SENSITIVE_WORDS_FILE
#        default: ${XDG_CONFIG_HOME:-~/.config}/devicehands/sensitive-words.txt
#      Maintainers keep it in their private repository. Without it only the
#      generic patterns run (this is the case in public CI), and the script
#      says so on stderr every time.
#
# Environment:
#   DHANDS_SENSITIVE_WORDS_FILE  path of the private word list
#   DHANDS_SENSITIVE_EXTRA       colon-separated extra patterns (rare)

set -euo pipefail

MODE="staged"
REQUIRE_PRIVATE=0
ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --require-private) REQUIRE_PRIVATE=1; shift ;;
    --all) MODE="all"; shift ;;
    --self-test) MODE="selftest"; shift ;;
    --file) MODE="file"; shift; ARGS=("$@"); break ;;
    *) echo "usage: $0 [--require-private] [--all | --file PATH... | --self-test]" >&2; exit 2 ;;
  esac
done
if [[ "$MODE" == "file" && ${#ARGS[@]} -eq 0 ]]; then
  echo "--file needs at least one path" >&2
  exit 2
fi

# 1. Generic patterns (ERE, matched case-insensitively). No real values here.
PATTERNS=(
  # A checkout of this repository (current or former name) under someone's
  # home, directly or below up to three parent folders such as
  # ~/claude-tools/ or ~/code/github/. Linux and macOS home roots.
  '/(home|Users)/[a-z][a-z0-9_.-]*/([A-Za-z0-9._-]+/){0,3}(android-llm-bridge|devicehands)([^A-Za-z0-9._-]|$)'
  # Tool folders that only exist on a developer's own machine.
  '/home/[a-z][a-z0-9_-]*/(adb-via-|\.claude)'
)

# 2. Private word list.
load_private_list() {
  local file="$1"
  local line
  PRIVATE_COUNT=0
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -z "${line//[[:space:]]/}" || "$line" == \#* ]] && continue
    PATTERNS+=("$line")
    PRIVATE_COUNT=$((PRIVATE_COUNT + 1))
  done < "$file"
}

PRIVATE_FILE="${DHANDS_SENSITIVE_WORDS_FILE:-${XDG_CONFIG_HOME:-$HOME/.config}/devicehands/sensitive-words.txt}"
PRIVATE_COUNT=0

if [[ -n "${DHANDS_SENSITIVE_EXTRA:-}" ]]; then
  IFS=':' read -r -a EXTRA <<< "$DHANDS_SENSITIVE_EXTRA"
  PATTERNS+=("${EXTRA[@]}")
fi

# --self-test: each case is "hit|<line>" (some pattern must match) or
# "miss|<line>" (no pattern may match). Reverse cases matter as much as the
# hits: a pattern that matches everything passes every "hit" case.
# The private list is replaced by a temporary one holding a made-up word, so
# the test proves the list is read without knowing any real value.
SELF_TEST_CASES=(
  'hit|/home/someone/android-llm-bridge/src/x.py'
  'hit|/home/someone/devicehands'
  'hit|/home/someone/claude-tools/devicehands/README.md'
  'hit|cd /Users/someone/code/github/devicehands && make'
  'hit|/home/someone/.claude/settings.json'
  'miss|~/claude-tools/devicehands/README.md'
  'miss|/opt/tools/devicehands/bin/dhands'
  'miss|docs/devicehands-design.md'
  'miss|/home/someone/projects/devicehandsx/README.md'
  'hit|connected to zzq-private-host-zzq:11434'
  'hit|ZZQ-PRIVATE-HOST-ZZQ in upper case'
  'miss|zzq-private-hostzzq without the dash'
  'miss|192.0.2.10 is a documentation address'
)

if [[ "$MODE" == "selftest" ]]; then
  tmp="$(mktemp)"
  trap 'rm -f "$tmp"' EXIT
  printf '# sample private list\n\nzzq-private-host-zzq\n' > "$tmp"
  load_private_list "$tmp"
  if [[ $PRIVATE_COUNT -ne 1 ]]; then
    echo "FAIL private list: read $PRIVATE_COUNT patterns, want 1"
    exit 1
  fi
  passed=0
  total=${#SELF_TEST_CASES[@]}
  for case in "${SELF_TEST_CASES[@]}"; do
    want="${case%%|*}"
    line="${case#*|}"
    got="miss"
    for pat in "${PATTERNS[@]}"; do
      if printf '%s\n' "$line" | grep -qEi -- "$pat"; then got="hit"; break; fi
    done
    if [[ "$got" == "$want" ]]; then
      passed=$((passed + 1))
    else
      echo "FAIL want=$want got=$got: $line"
    fi
  done
  echo "self-test: $passed/$total passed"
  [[ $passed -eq $total ]] && exit 0 || exit 1
fi

if [[ -f "$PRIVATE_FILE" ]]; then
  load_private_list "$PRIVATE_FILE"
  echo "sensitive-words: private list loaded, $PRIVATE_COUNT patterns" >&2
else
  echo "sensitive-words: private list not found at $PRIVATE_FILE; only generic patterns checked" >&2
  if [[ $REQUIRE_PRIVATE -eq 1 ]]; then exit 2; fi
fi

# Files that may legitimately match. Keep this short: each entry hides a file
# from the real-word check too.
ALLOWED=(
  'scripts/check_sensitive_words.sh'  # its sample lines are fake on purpose
)

is_allowed() {
  local path="$1"
  for glob in "${ALLOWED[@]}"; do
    if [[ "$path" == $glob ]]; then return 0; fi
  done
  return 1
}

FILES=()
case "$MODE" in
  staged)
    while IFS= read -r -d '' f; do FILES+=("$f"); done \
      < <(git diff --cached --name-only --diff-filter=ACMR -z) ;;
  all)
    # tracked + untracked (minus ignored) — "--all" must see brand-new files
    # too, or a pre-commit review of untracked work gets a false all-clear.
    while IFS= read -r -d '' f; do FILES+=("$f"); done \
      < <(git ls-files -z --cached --others --exclude-standard) ;;
  file)
    FILES=("${ARGS[@]}") ;;
esac

if [[ ${#FILES[@]} -eq 0 ]]; then
  exit 0
fi

FAILED=0

scan_file() {
  local path="$1"
  if [[ "$MODE" != "file" ]] && is_allowed "$path"; then return 0; fi
  [[ ! -f "$path" ]] && return 0       # deleted / moved
  # Skip binaries
  if file --mime "$path" | grep -q 'charset=binary'; then return 0; fi

  for pat in "${PATTERNS[@]}"; do
    if matches=$(grep -nEi -- "$pat" "$path" 2>/dev/null); then
      echo "---"
      echo "[sensitive] $path"
      echo "  pattern: $pat"
      echo "$matches" | sed 's/^/    /'
      FAILED=1
    fi
  done
}

for f in "${FILES[@]}"; do
  scan_file "$f"
done

if [[ $FAILED -eq 1 ]]; then
  echo ""
  echo "Sensitive-word guard: one or more patterns found above."
  echo "See CLAUDE.md § 1 for the policy and generic alternatives."
  exit 1
fi

exit 0
