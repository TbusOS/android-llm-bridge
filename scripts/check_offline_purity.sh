#!/usr/bin/env bash
# Verify the built Web UI and docs/ make zero external HTTP requests at
# runtime. Catches accidental Google Fonts @import / CDN <script>.
#
# Run in CI on every web-related PR:
#   ./scripts/check_offline_purity.sh
#
# Exit 0 = clean, exit 1 = an external reference leaked in.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

TARGETS=(docs/app)
# Only check docs/app (the served Web UI). docs/index.html preview
# and docs/webui-preview.html are in-repo previews that still rely on
# Google Fonts for the GitHub Pages marketing surface; we leave them
# alone for now. The SERVED UI must be pure.

# Hostnames that must never appear in the offline bundle.
PATTERN='fonts\.googleapis\.com|fonts\.gstatic\.com|cdn\.jsdelivr\.net|unpkg\.com|cdnjs\.cloudflare\.com|cdn\.skypack\.dev|esm\.sh'

fail=0
for t in "${TARGETS[@]}"; do
  if [ ! -e "$t" ]; then continue; fi
  matches=$(grep -rEIn --include='*.html' --include='*.css' --include='*.js' \
              --include='*.mjs' --include='*.cjs' "$PATTERN" "$t" || true)
  if [ -n "$matches" ]; then
    echo "✗ External CDN references in $t :"
    echo "$matches"
    fail=1
  fi
done

# Also fail on Google Fonts @import (catches fonts.css regeneration mistakes).
if grep -rEIn --include='*.css' "@import +url\(.+(googleapis|gstatic)" "${TARGETS[@]}" 2>/dev/null; then
  echo "✗ @import of Google Fonts found — vendor them locally."
  fail=1
fi

if [ "$fail" -eq 0 ]; then
  echo "offline-purity: OK ($(find "${TARGETS[@]}" -name '*.html' -o -name '*.css' -o -name '*.js' 2>/dev/null | wc -l) files checked)"
fi

exit $fail
