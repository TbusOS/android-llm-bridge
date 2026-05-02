# Functional Completeness Audit — 2026-05-02 ship

Scope: today's PR-A...PR-H + UART/Logcat/Shell/Files/Charts/Screenshot/UiDump
plus the 5 file endpoints and 3 WS endpoints. Not a code review — pure
"feature × happy/sad/edge/recover" gap matrix. Out-of-scope (already covered
by other audits): 5-axis code review, focus-trap / a11y / 3-state UI.

Legend: OK complete / PART partial / GAP missing.

## A. Inspect 8 tabs × 4 dimensions

| Tab | Happy | Sad (offline / fail) | Edge (huge / empty / perm) | Recover (retry / clear / reconnect) |
|---|---|---|---|---|
| **System Info** | OK refetch button + isFetching | OK isError + ok=false branch | PART empty meminfo→`—`, but no perm-denied hint when adb shell read-only fails | PART manual Refresh; **GAP** no auto-clear of stale data when serial changes during in-flight fetch |
| **Charts (WS metrics)** | OK Connect / live samples / pause | OK error state on WS error | **GAP** empty `samples=[]` shows nothing — no "waiting for first sample" hint; no clamp on extreme values (cpu_pct=999) | **GAP** no auto-reconnect on `ended`; pause+disconnect+reconnect resets buffer (acceptable) but `ended` from server-side streamer crash leaves user with stale chart, no toast |
| **UART Capture (REST)** | OK button → log + history | OK 5min HTTP cap + ok=false | PART duration capped at 300s; but **GAP** very large capture (10MB) is base64-style read-text in one shot; OOM on browser if log >50MB | OK retry by clicking again; **GAP** no per-capture delete |
| **UART Live (WS)** | OK ready→bytes; write toggle | OK init_failed / write_unsupported surfaced | OK 64KB write-frame cap (DEBT-026) with `write_dropped` ack | **GAP** no auto-reconnect; user must click again. ACCEPTABLE per design (USB unplug). But after `stream_error` the xterm keeps stale bytes — no "buffer cleared" affordance |
| **Logcat (WS)** | OK ready + binary stream | OK init_failed / unsupported_source | **GAP** filter spec invalid (e.g. `*:Q` unknown level) — server starts then logcat exits → looks like "no logs"; no validation feedback | **GAP** filter change requires manual disconnect+reconnect; no reconnect-on-filter-edit affordance |
| **Shell (PTY + HITL)** | OK xterm + HITL modal | OK NotImplementedError (serial PTY) → TRANSPORT_NO_PTY surfaced | **GAP** PTY spawn that succeeds but shell exits immediately (e.g. su denied) → terminal silently shows exit_code 1, no rationale. **GAP** HITL modal state survives WS disconnect — modal stranded if server dies mid-prompt | **GAP** disconnect during a pending HITL request leaves `pendingHitl` non-null on reconnect |
| **Screenshot** | OK button → png inline + download | OK ok=false branch | **GAP** very large screenshot (8K display ~30MB base64 = 40MB JSON) — no size check before render; browser may OOM. Server uses base64 path despite separate file response existing | OK click again to retry; **GAP** no history (v1 ack'd in code) — old shot dropped on re-capture |
| **UI Dump** | OK button + flat tree + filter | OK ok=false branch | OK 2k-node memo + deferred filter; **GAP** node text huge (>10KB single textview) inflates row, no truncation; no fallback if `top_activity` empty | **GAP** filter input no clear-button; no "0 of N matches" counter when filter empties list |
| **Files** | OK two-pane, debounced, Pull/Push, HITL | OK error string surfaced per pane | OK truncation flag + `_MAX_ENTRIES=2000`; **GAP** symlink loop on workspace pane — `iterdir` doesn't detect cycle (lstat fine, but `is_dir()` follows); **GAP** no preview of file content (must Pull then download) | OK invalidates query on success; **GAP** Pull/Push hung adb (no progress) — request runs forever, no cancel button |

## B. 5 alb-api endpoints × 4 dimensions

| Endpoint | Param-missing | Device offline | Path n/a / perm-denied | Big / concurrent / interrupt / timeout |
|---|---|---|---|---|
| **POST /devices/{s}/files/pull** | OK rejects bad remote | OK 200+ok=false | OK ls-fail surfaced via filesync error | **GAP** no asyncio.wait_for outer timeout — hung adb pulls block FastAPI worker indefinitely. **GAP** concurrent same-serial pull races on adb daemon; no per-serial lock |
| **POST /devices/{s}/files/push** | OK validates local + remote + sensitivity | OK build_transport try/except | OK HITL gate on /system /vendor etc; non-existent local rejected | Same as pull: **GAP** no outer timeout; **GAP** push of 4GB file holds the worker; no progress / cancel; **GAP** force=true with concurrent non-force re-issue creates 2 race-pushes |
| **GET /devices/{s}/files** | OK invalid path → ok=false | OK shell call wrapped | OK ls error returned with exit_code | OK 30s shell timeout + 2000-entry cap; **GAP** symlink to `/proc/self/fd` returns nonsense — no cycle detection; **GAP** clients spamming during adb retry create N parallel `ls` |
| **GET /workspace/files** | OK lstat (no symlink leak — DEBT MID 3 fix) | n/a | OK 400 on escape; missing → ok=false | OK `_MAX_ENTRIES`; **GAP** `target.is_dir()` after lstat will follow symlink → potential out-of-workspace stat through symlink-as-dir trick (lstat shows st_size 0 but the listing then runs iterdir on follow target, since `target` itself is `_resolve_workspace_path` resolved — actually safe; child entries don't enforce). **GAP** big workspace dir (50k files) → entire iterdir built before truncate cap |
| **GET /workspace/files/download/{path}** | OK 404 if not file | n/a | OK resolve+relative_to gate | **GAP** no Range header support — large pulls (>1GB) restart from 0 on browser disconnect; **GAP** no Content-Length cap — accidental download of 10GB image hammers RAM only mildly (FileResponse streams) but browser may; **GAP** no rate limit |

## C. 3 WS endpoints × 4 dimensions

| WS | Connection-fail | Mid disconnect | Big write / server crash | Cancel / heartbeat / reconnect |
|---|---|---|---|---|
| **/uart/stream** | OK init_failed close-frame | OK PR-C.c race fixed; single close frame | OK 64KB write cap; **GAP** server-side `link.writer.write` no backpressure — slow UART blocks `_recv_loop` indefinitely if drain queue full | **GAP** no heartbeat / no client ping; idle WS through proxies will be killed silently. **GAP** no server-side reconnect; client hook explicitly opts out (USB unplug rationale, OK) |
| **/terminal/ws** | OK PTY_SPAWN_FAILED frame | OK exit_code emitted | **GAP** no per-frame stdin size cap — paste of huge buffer floods PTY; **GAP** server crash → client gets close, but `pendingHitl` modal stays mounted and modal Approve becomes no-op (ws.send silent fail) | **GAP** no heartbeat; **GAP** resize sent before ready frame is ignored silently (race during connecting) |
| **/metrics/stream** | OK 1.5s config timeout | OK send_loop returns | **GAP** subscribed queue unbounded — slow client → memory grows on server; **GAP** server-side streamer crash propagates only as ws.close, no `closed` JSON frame (hook stays in `ended`, looks normal) | **GAP** no auto-reconnect on `ended`; **GAP** no heartbeat; **GAP** `set_interval` value not validated (negative / 0.001 / 1e9 all accepted) |

## HIGH (≤5) — fix soon, will cause user-visible weirdness or data loss

1. **Pull/Push no outer timeout** — hung adb (USB cable drop mid-transfer) freezes the FastAPI worker forever. Wrap `filesync_pull/push` in `asyncio.wait_for(..., timeout=600)` and surface `timeout` error. Also blocks subsequent same-serial requests.
2. **Concurrent same-serial push race** — Two browser tabs (or HITL-confirm + plain push race) hit `/files/push` for the same serial → two adb daemons write same path. Add a per-serial asyncio.Lock around filesync write ops (read can stay parallel).
3. **Metrics WS subscriber queue unbounded** — Slow client (frozen tab, paused JS) silently grows server RAM; no drop-old / max-queue-size. Cap at e.g. 256 samples and drop-oldest with a `dropped:N` notice frame.
4. **Shell HITL modal stranded on disconnect** — If WS dies while a HITL prompt is open, the modal Approve button silently fails (ws.send no-op) and user is stuck staring at it. ShellTab must clear `pendingHitl` on `state==='error'|'ended'`.
5. **Charts shows blank on `ended`** — Server-side streamer crash closes WS without a JSON `closed` frame, hook drops to `ended`, charts keep stale data with no reconnect prompt. Surface a "stream ended — reconnect" toast.

## MID (≤8) — should fix but tolerable

1. Logcat invalid filter spec → silent empty stream (no validation feedback).
2. UART live `stream_error` leaves stale bytes in xterm with no "clear & reconnect" affordance.
3. Workspace files: big iterdir built in full before truncate cap — slow on 50k-file dirs.
4. `/files/download` no Range header support — multi-GB downloads can't resume.
5. Shell PTY spawned-then-exits-immediately (`su` denied) gives `exit_code` only, no rationale string from stdout buffer.
6. Files tab: long-running Pull/Push has no Cancel button + no progress %.
7. Metrics `set_interval` accepts pathological values (negative, 1e9). Clamp to [0.1, 60] with control_ack reflecting actual.
8. WS endpoints lack heartbeat — proxy idle-killed connections appear hung to user with no ping/pong feedback.

## LOW (≤5) — nice to have

1. Screenshot: no history list; old shot dropped on each click. Mirror UART captures sidebar.
2. UI Dump filter: no "M of N matches" counter and no clear button.
3. Files tab: no inline preview for small text files (must Pull → Download → open).
4. UART capture: no per-capture delete from list.
5. Logcat: editing filter requires manual disconnect+reconnect; auto-reconnect on debounced filter edit would be friendlier.

Notes:
- Reviewed today's HEAD `5709037`. No tests run; static analysis only.
- Already-known by other audits: focus trap, a11y, CLS, loading-empty-error tri-state, code-reviewer 5-axis (resource leaks, error propagation, concurrency, test coverage, API stability), security audit MID 3 (lstat) — explicitly excluded above.

## Closure status (updated 2026-05-02)

- **HIGH**: 4/5 closed in `b33c1c4` + `53e984d` (early shift). #3 (metrics queue) was an auditor misread — `metrics.py:241` already caps the per-subscriber queue at `maxsize=20` with drop-oldest semantics; no fix needed.
- **MID**: 4/8 closed in `dbf5dca` (late shift):
  - MID-1 logcat invalid filter → `_validate_filter_spec` + `bad_filter` close-frame
  - MID-2 UART stale bytes → "Clear & reconnect" button (error/ended state)
  - MID-7 metrics set_interval → NaN reject + `clamped`/`requested_s` ack (triggered L-030)
  - MID-3 workspace iterdir → `os.scandir` + sort/truncate-then-stat
- **MID remaining (4)** → backlog: MID-4 Range header / MID-5 PTY exit rationale / MID-6 Pull/Push cancel+progress / MID-8 WS heartbeat
- **LOW (5)** → backlog (deferred — no user-visible weirdness)
