#!/usr/bin/env bash
# Start a headless Chrome (or Edge) on the bench Windows machine and forward
# its DevTools port to this host, so the browser checks (func_e2e.mjs,
# button_sweep.mjs) run on Windows: Windows fonts and scaling, Windows
# networking, the hub opened over plain http on the LAN.
#
# usage:
#   web/scripts/win_browser.sh [--edge] [local-port]   # stays in the foreground
#   web/scripts/win_browser.sh --stop                  # only stop a test browser left behind
# env:
#   ALB_BENCH_SSH_HOST  ssh alias of the bench host (the one
#                       `alb bench agent-start` uses, set in hub.env)
# then, from another shell:
#   ALB_CDP_URL=http://127.0.0.1:9333 ALB_BASE=http://<hub-lan-address>:8765/app \
#     node web/scripts/func_e2e.mjs
#
# Chrome by default. Headless Edge hangs the NEXT DevTools connection after a
# run that downloads a file and then opens another page: Edge leaves a hidden
# page with no renderer in its default context (alongside its
# edge://downloads/hub page), Playwright's connectOverCDP attaches to it and
# waits for answers that never come. A download alone does not do it. Chrome
# has no such page: the same func_e2e on one Chrome, run after run, connects
# at once (bench, 2026-11-02). --edge is there to look at the pages in Edge;
# restart this script between Edge runs.
#
# The browser gets a throw-away profile (%TEMP%\alb-e2e-chrome or
# alb-e2e-edge), never the user's own. It does NOT end with the ssh session —
# Start-Process lets it outlive it (2026-10-28: 7 processes were still there
# after the session closed) — so this script stops it on exit, picking
# processes by that profile path in their command line: the user's own
# browser windows are never touched.
set -euo pipefail

HOST="${ALB_BENCH_SSH_HOST:?set ALB_BENCH_SSH_HOST to the bench ssh alias}"
REMOTE_PORT=9333

remote_ps() {
  # -EncodedCommand works whichever default shell the bench's sshd has
  # (cmd.exe or PowerShell) and survives any quoting in the script.
  local enc
  enc=$(printf '%s' "$1" | iconv -f utf-8 -t utf-16le | base64 -w0)
  ssh -o BatchMode=yes "$HOST" "powershell -NoProfile -NonInteractive -EncodedCommand ${enc}" 2>/dev/null | tr -d '\r' | grep -v -e '^#< CLIXML' -e '^<Objs' || true
}

STOP_PS=$(cat <<'PSEOF'
$mine = @(Get-CimInstance Win32_Process | Where-Object {
  ($_.Name -eq 'chrome.exe' -and $_.CommandLine -like '*alb-e2e-chrome*') -or
  ($_.Name -eq 'msedge.exe' -and $_.CommandLine -like '*alb-e2e-edge*') })
$mine | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
"stopped test browser processes: " + $mine.Count
PSEOF
)

if [ "${1:-}" = "--stop" ]; then
  remote_ps "$STOP_PS"
  exit 0
fi
KIND=chrome
if [ "${1:-}" = "--edge" ]; then
  KIND=edge
  shift
fi
PORT="${1:-9333}"

if [ "$KIND" = edge ]; then
  FIND_PS=$(cat <<'PSEOF'
$e = Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'
PSEOF
)
else
  FIND_PS=$(cat <<'PSEOF'
$e = @("$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
       "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
       "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe") |
     Where-Object { Test-Path $_ } | Select-Object -First 1
PSEOF
)
fi

# Start-Process -Wait keeps this session open while the browser runs, so the
# port forward lives exactly as long as the browser is in use.
START_PS=$(cat <<'PSEOF'
@@FIND@@
if (-not $e -or -not (Test-Path $e)) { "no @@KIND@@ on the bench host"; exit 3 }
$d = Join-Path $env:TEMP 'alb-e2e-@@KIND@@'
Start-Process -Wait -FilePath $e -ArgumentList '--headless=new','--remote-debugging-port=@@PORT@@',"--user-data-dir=$d",'--no-first-run','--no-default-browser-check','about:blank'
PSEOF
)
# quoted replacements: bash 5.2 would otherwise treat & in them specially
START_PS=${START_PS//@@FIND@@/"$FIND_PS"}
START_PS=${START_PS//@@KIND@@/"$KIND"}
START_PS=${START_PS//@@PORT@@/"$REMOTE_PORT"}

remote_ps "$STOP_PS"   # a run that was killed may have left one behind
trap 'remote_ps "$STOP_PS"' EXIT
enc=$(printf '%s' "$START_PS" | iconv -f utf-8 -t utf-16le | base64 -w0)
ssh -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
  -L "127.0.0.1:${PORT}:127.0.0.1:${REMOTE_PORT}" "$HOST" \
  "powershell -NoProfile -NonInteractive -EncodedCommand ${enc}"
