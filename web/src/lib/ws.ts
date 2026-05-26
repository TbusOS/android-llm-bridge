/**
 * Lightweight WebSocket helper — wraps the browser WebSocket with
 * JSON-or-binary demux, automatic reconnect (exponential back-off up
 * to 30s), and a subscriber interface that tracks open/close.
 *
 * Two modes (DEBT-047 · AP-1):
 *
 *   1. **Solo mode** (no `shareKey`): every `connect()` mints its own
 *      underlying WebSocket. Used by `useWsChatStream` for one-shot
 *      per-turn chat sockets that MUST NOT share state with another
 *      tab / another turn — sharing would cross-pollute LLM token
 *      streams.
 *
 *   2. **Pooled mode** (`shareKey` provided): two callers passing the
 *      same `(path, shareKey)` get **view clients** backed by ONE
 *      underlying socket. Refcount tracks live views; when the last
 *      view `.close()`s, the underlying socket really closes and the
 *      pool entry is dropped.
 *
 *      Late joiners (subscribe after the underlying socket has already
 *      opened and received its first snapshot) are queued a microtask
 *      that synthesizes an `{kind:"open"}` event followed by the
 *      cached snapshot (if fresh — see STALE_SNAPSHOT_MS). The
 *      consumer's open handler fires `client.send(config)`, which is
 *      then deduped per-epoch (ADR-046) so the server doesn't re-
 *      broadcast an identical config. AS-2 (DEBT-065): if the cache
 *      is too old, the microtask skips the replay AND clears the
 *      dedup tracker so the late joiner's send DOES reach the server
 *      and triggers a fresh snapshot broadcast.
 *
 *      Used by `useAuditStream` where 2+ tabs / sub-pages open the
 *      same `/audit/stream` with identical (minutes, includeMetrics).
 *
 * **Test-hook naming convention** (AS-3 / sec LOW-1 + arch LOW-4):
 *   Module-level mutable state used ONLY for tests is exposed via
 *   `__xxxForTests` (double-underscore prefix + `ForTests` suffix).
 *   Examples: `__resetPoolForTests`, `__setListenerErrorHandlerForTests`,
 *   `__setNowProviderForTests`. The double-underscore marks the
 *   surface as not-stable-API; the `ForTests` suffix makes
 *   bundler grep / code review trivially spot misuse. Prod callers
 *   touching any `__*ForTests` export = failed code review.
 *
 * **Listener contract** (AS-3 / code LOW-3):
 *   Subscribers MUST be synchronous functions. Async listeners
 *   (`async (ev) => ...`) that throw bypass the synchronous
 *   try/catch and surface as unhandled promise rejections (NOT
 *   routed through `listenerErrorHandler`). If you need async work,
 *   schedule it inside the listener via `queueMicrotask` / `setTimeout`
 *   and handle its rejection there.
 */

export type WsEvent =
  | { kind: "open" }
  | { kind: "close"; code: number; reason: string }
  | { kind: "error"; message: string }
  | { kind: "json"; data: unknown }
  | { kind: "binary"; data: ArrayBuffer };

export interface WsClient {
  send(data: string | ArrayBufferLike | Blob | object): void;
  close(code?: number): void;
  subscribe(listener: (ev: WsEvent) => void): () => void;
  get readyState(): number;
}

interface Options {
  /** Backoff ceiling in ms (default 30_000). */
  maxBackoffMs?: number;
  /** Disable auto-reconnect (default false — we do reconnect). */
  noReconnect?: boolean;
  /** Pool dedup key (DEBT-047 · AP-1 implemented).
   *
   *  Two callers passing the SAME `(path, shareKey)` share one socket.
   *  Pass DIFFERENT `shareKey` (or omit) when configs diverge — e.g.
   *  `useAuditStream({includeMetrics: false})` MUST NOT share with
   *  `useAuditStream({includeMetrics: true})` per ADR-022. Omit
   *  entirely for must-not-share callers (e.g. per-turn chat streams).
   *
   *  SECURITY (AR-4 / sec LOW): shareKey is held verbatim in module-
   *  global Map keys AND closed-over inside view send-dedup state for
   *  the lifetime of the pool entry. AS-3 (sec LOW-2): the SAME
   *  warning applies to `send(payload)` — text-shape payloads are
   *  serialized to string and stored in `lastSentPayload.payload`
   *  for the current epoch (cleared on reconnect or first different
   *  payload). DO NOT pass tokens, session IDs, user IDs, or other
   *  PII / secrets in EITHER `shareKey` OR `send()` payloads — hash
   *  them or omit shareKey entirely. Today's only caller
   *  (`useAuditStream`) serializes `{minutes, includeMetrics}` for
   *  both, which are pure UI config, no PII.
   *
   *  Wire-format-style contract: changing the serialization shape of
   *  shareKey silently re-keys existing callers. `useAuditStream.
   *  shareKey.test.ts` pins the exact byte sequence — bump that spec
   *  when intentionally changing. */
  shareKey?: string;
  /** Cached-snapshot age ceiling (ms) for late-joiner replay (AT-3 /
   *  5/26 第六轮 MID-1 · DEBT-065 follow-up). When the late-joiner
   *  microtask checks the pool entry's cached snapshot, anything
   *  older than this value is treated as stale: the cache is not
   *  replayed AND the view's next text-shape send bypasses dedup
   *  exactly once, forcing the server to broadcast a fresh snapshot.
   *
   *  Default: 5 min. Long-idle tabs (e.g. an `AuditPage` running in
   *  the background for an hour) may set this higher to avoid an
   *  unnecessary server round-trip on resume; high-freshness streams
   *  (chat / metrics) may set it lower. `0` is a legal value meaning
   *  "treat cache as always stale" — every late joiner forces fresh.
   *
   *  Validation (AU-1 / 5/26 第七轮 code MID-1): non-finite (`NaN` /
   *  `Infinity`) and negative values fall back to the default with a
   *  console.warn — they each degrade dedup in a different silent way
   *  (NaN → cache never stale; negative → cache always stale → dedup
   *  effectively disabled) and almost certainly indicate a caller bug.
   *
   *  The first caller of `(path, shareKey)` to create the pool entry
   *  wins the value — subsequent callers' staleSnapshotMs is ignored.
   *  This matches the "first-caller wins" semantics of every other
   *  pool-level value option (see ADR-047).
   *
   *  Reactivity caveat (AV-3 / 5/26 第八轮 code MID-4): a caller
   *  that puts `staleSnapshotMs` in a React `useEffect` deps array
   *  expecting "swap value → entry rebuilds with new ceiling" gets
   *  no-op behaviour while other views still hold the same pool
   *  entry. The cleanup decrements refCount; if it doesn't reach 0
   *  (another view alive), the entry survives with its locked-in
   *  staleSnapshotMs. Only the SOLE-consumer path triggers a real
   *  rebuild. Don't expose this option as user-tunable UI state. */
  staleSnapshotMs?: number;
}

/** Build an absolute ws:// / wss:// URL for a path relative to the
 *  current origin. In dev Vite proxies the path; in prod FastAPI
 *  serves it on the same origin. */
export function wsUrl(path: string): string {
  const { protocol, host } = window.location;
  const wsProto = protocol === "https:" ? "wss:" : "ws:";
  const cleanPath = path.startsWith("/") ? path : `/${path}`;
  return `${wsProto}//${host}${cleanPath}`;
}

/** A single underlying socket + every view that subscribes to it. */
interface PoolEntry {
  /** The real, non-pooled client we delegate sends/close to. */
  client: WsClient;
  /** Pool key — kept on the entry so the underlying client's listener
   *  can `pool.delete(key)` without recomputing. */
  key: string;
  /** Active view count. The underlying socket stays open while > 0,
   *  closes (and the entry is dropped) at 0. */
  refCount: number;
  /** Listeners across all views. */
  subscribers: Set<(ev: WsEvent) => void>;
  /** Most recent snapshot-shaped JSON event from the server, cached
   *  so late joiners can converge without a full server round-trip.
   *  Detection is duck-typed (`data?.type === "snapshot"`) so the WS
   *  helper stays protocol-agnostic — only paths that ACTUALLY emit
   *  `{type:"snapshot"}` benefit. */
  cachedSnapshot: WsEvent | null;
  /** Wall-clock ms when `cachedSnapshot` was last updated. Used by the
   *  late-joiner replay path to refuse stale snapshots — pre-AR-1 the
   *  server would re-send a fresh snapshot in response to the late
   *  joiner's redundant config send; AR-1 dedup drops that send, so
   *  the cache is now the ONLY bootstrap. AS-2 (5/26 第五轮 arch
   *  HIGH-1 / DEBT-065 MID): if `Date.now() - cachedSnapshotAt`
   *  exceeds the per-entry `staleSnapshotMs`, the microtask replay
   *  skips the cached snapshot AND sets the late joiner's per-view
   *  `forceNextSendFresh` flag (AT-2) so its open-handler config
   *  send bypasses dedup and forces a fresh server-side snapshot.
   *  0 = never set. */
  cachedSnapshotAt: number;
  /** Caller-configurable stale-snapshot age ceiling for this entry
   *  (AT-3 / Options.staleSnapshotMs). Captured from the FIRST
   *  caller to mint the pool entry; subsequent callers' values are
   *  ignored (matches the existing first-caller-wins semantics of
   *  pool-level options like maxBackoffMs / noReconnect). */
  staleSnapshotMs: number;
  /** Incremented every time the underlying socket re-opens (initial
   *  open + every auto-reconnect). Drives per-epoch payload dedup so
   *  the same config message sent by N view-listeners in response to
   *  a single underlying "open" reaches the server exactly once.
   *
   *  AR-1 (5/26 第四轮 ui-f HIGH-1 fix): without this, N views with
   *  same shareKey each saw "open" via fan-out, each re-sent its config
   *  in its open handler, server received N identical config messages
   *  and rebroadcast N snapshots, fanned back out to N listeners =
   *  N² setState. Pool-level dedup collapses it to 1 send / 1 snapshot
   *  / N setState. */
  currentEpoch: number;
  /** Last text-shape payload sent in `currentEpoch`. View.send() drops
   *  any subsequent identical payload from the SAME epoch — different
   *  payloads pass through, and a new epoch (reconnect) resets the
   *  tracker so config gets re-sent on the new underlying socket.
   *  Binary payloads bypass dedup entirely (rare, content-addressed
   *  hashing would need a hash that doesn't trigger main-thread jank). */
  lastSentPayload: { epoch: number; payload: string } | null;
}

const pool = new Map<string, PoolEntry>();

/** Default listener-error handler: re-throw on a microtask so dev-tools
 *  / window.onerror still see the stack but the synchronous fan-out
 *  loop isn't truncated. Tests stub this via `__setListenerErrorHandlerForTests`
 *  to capture errors without crashing the test runner. */
let listenerErrorHandler: (e: unknown) => void = (e) => {
  queueMicrotask(() => {
    throw e;
  });
};

/** Test hook: swap the listener-error handler. Returns the prior
 *  handler so tests can restore it in afterEach. */
export function __setListenerErrorHandlerForTests(
  handler: (e: unknown) => void,
): (e: unknown) => void {
  const prev = listenerErrorHandler;
  listenerErrorHandler = handler;
  return prev;
}

/** Default cached-snapshot age limit before the late-joiner microtask
 *  skips the replay and forces a fresh server round-trip. AS-2 /
 *  DEBT-065 MID: AR-1 send dedup means the server doesn't auto-re-
 *  send a snapshot on the late joiner's redundant config send, so
 *  cache age needs an explicit ceiling — otherwise a tab opened 6h
 *  after the pool entry bootstrapped would render 6h-old timeline
 *  state for the first few frames. 5 min is the common tab-idle
 *  threshold. AT-3 (5/26 第六轮 MID-1): callers can override per-
 *  entry via `Options.staleSnapshotMs` for streams with different
 *  freshness budgets. AV-5 (5/26 第八轮 code LOW): declared BEFORE
 *  `sanitizeStaleSnapshotMs` (the only consumer) so module init can
 *  never hit a TDZ — pure ordering hygiene, no functional change. */
const DEFAULT_STALE_SNAPSHOT_MS = 5 * 60 * 1000;

/** Coerce a caller-supplied `staleSnapshotMs` to a sane value.
 *  AU-1 (5/26 第七轮 code MID-1 / sec LOW): the field is typed
 *  `number?` so callers can pass anything numeric (negative, `NaN`,
 *  `Infinity`). Each value degrades dedup differently:
 *
 *    - `NaN`  → `now - at > NaN` is always false → cache never stale
 *      → late joiner force flag never fires → original AS-2 fix
 *      silently regresses
 *    - `< 0`  → cache always considered stale → every late joiner
 *      forces a fresh send → dedup is effectively disabled
 *    - `Infinity` → cache never stale (same outcome as NaN but at
 *      least intentional-looking from the call site)
 *
 *  Coerce non-finite-or-negative inputs to the default + warn — this
 *  always fires (dev, test, AND prod). AV-4 (5/26 第八轮 code MID-5):
 *  earlier the comment claimed "Pure prod silently uses the default"
 *  but the implementation never gated the warn. Aligning the comment
 *  to the actual behaviour: a caller-bug signal is worth a single
 *  prod console line; the entry will be created exactly once per
 *  (path, shareKey), so this is at most one warn per pool entry per
 *  page-load — not a spammy stream.
 *  `0` is a legal "always treat cache as stale" value and passes. */
function sanitizeStaleSnapshotMs(raw: number | undefined): number {
  if (raw === undefined) return DEFAULT_STALE_SNAPSHOT_MS;
  if (Number.isFinite(raw) && raw >= 0) return raw;
  if (typeof console !== "undefined" && console.warn) {
    console.warn(
      `lib/ws: ignoring invalid staleSnapshotMs=${String(raw)} ` +
        `(must be a finite, non-negative number); using default ` +
        `${DEFAULT_STALE_SNAPSHOT_MS} ms`,
    );
  }
  return DEFAULT_STALE_SNAPSHOT_MS;
}

/** Wall-clock provider — separate function so tests can swap it via
 *  `__setNowProviderForTests` without monkey-patching Date.now globally
 *  (which leaks across test files). */
let nowMs: () => number = () => Date.now();

/** Test hook: swap the wall-clock provider for time-dependent specs
 *  (e.g. the cachedSnapshot age check). Returns the prior provider
 *  so tests can restore it in afterEach. */
export function __setNowProviderForTests(
  provider: () => number,
): () => number {
  const prev = nowMs;
  nowMs = provider;
  return prev;
}

/** Build a pool entry around a fresh underlying client. The entry's
 *  fan-out listener is installed exactly once per underlying socket;
 *  views are just Set entries. */
function createPoolEntry(
  path: string,
  options: Omit<Options, "shareKey">,
  key: string,
): PoolEntry {
  const underlying = soloConnect(path, options);
  const entry: PoolEntry = {
    client: underlying,
    key,
    refCount: 0,
    subscribers: new Set(),
    cachedSnapshot: null,
    cachedSnapshotAt: 0,
    staleSnapshotMs: sanitizeStaleSnapshotMs(options.staleSnapshotMs),
    currentEpoch: 0,
    lastSentPayload: null,
  };
  underlying.subscribe((ev) => {
    // Bump epoch on every real "open" (initial connect + each
    // auto-reconnect). Subsequent identical sends in this epoch get
    // deduped at the view layer; new payloads always pass through.
    if (ev.kind === "open") {
      entry.currentEpoch += 1;
    }
    // Cache the latest snapshot for late-joiner replay. The server
    // contract is `{type:"snapshot", events, since, until}` — anything
    // else is a delta and shouldn't replace the cached snapshot.
    if (ev.kind === "json" && isSnapshotPayload(ev.data)) {
      entry.cachedSnapshot = ev;
      entry.cachedSnapshotAt = nowMs();
    }
    // Snapshot the listener set before iterating: a listener handler
    // that calls .close() (refCount → 0) deletes its own subscription
    // mid-iteration, and a sibling handler that opens a new view re-
    // adds to the set. Both would corrupt a live iteration.
    // AR-3 (5/26 第四轮 code MID-1): each listener call is wrapped in
    // a try/catch so a throwing listener (consumer bug · React state
    // update during render · whatever) doesn't truncate the fan-out
    // and starve sibling views of the event. The error is re-thrown
    // via queueMicrotask so dev-tools / window.onerror still see the
    // stack trace, but the synchronous fan-out completes first.
    const snapshot = Array.from(entry.subscribers);
    for (const l of snapshot) {
      try {
        l(ev);
      } catch (e) {
        listenerErrorHandler(e);
      }
    }
  });
  return entry;
}

function isSnapshotPayload(data: unknown): boolean {
  return (
    typeof data === "object" &&
    data !== null &&
    (data as { type?: unknown }).type === "snapshot"
  );
}

/** Build a view that delegates to an existing pool entry. The view's
 *  close() decrements refCount and tears down the underlying socket
 *  at 0. */
function makeView(entry: PoolEntry): WsClient {
  entry.refCount += 1;
  let viewClosed = false;
  // AT-2 (5/26 第六轮 code HIGH-2 + arch HIGH-1 fix): per-view
  // "force the next send to bypass dedup" flag. Replaces the prior
  // cross-view side-effect where the stale-snapshot path mutated
  // entry.lastSentPayload — that broke the dedup invariant for ALL
  // sibling views (a force-fresh signal from view-A made view-B's
  // next identical send also reach the server). Now the signal is
  // local: only the view that observed the stale cache will skip
  // dedup ONCE, then resume normal behaviour on subsequent sends.
  let forceNextSendFresh = false;
  return {
    send(data) {
      if (viewClosed) return;
      // Binary frames are content-addressed at the application layer;
      // dedup would need a hash + a payload-equality contract the WS
      // helper has no opinion on. Pass them through unfiltered.
      // AS-3 (code LOW-4): SharedArrayBuffer is part of the
      // ArrayBufferLike type the send() signature accepts, and is
      // NOT covered by `instanceof ArrayBuffer` or
      // `ArrayBuffer.isView`. Probe via typeof to avoid ReferenceError
      // in environments that disable SAB (post-Spectre browsers
      // require `crossOriginIsolated`).
      const isBinary =
        data instanceof ArrayBuffer ||
        ArrayBuffer.isView(data) ||
        data instanceof Blob ||
        (typeof SharedArrayBuffer !== "undefined" &&
          data instanceof SharedArrayBuffer);
      if (isBinary) {
        entry.client.send(data);
        return;
      }
      // Text-shape dedup: same payload within the same epoch reaches
      // the underlying socket exactly once. N views responding to one
      // "open" event with identical config collapse to one server-
      // bound message (AR-1 fix for ui-f HIGH-1 reconnect storm).
      const payload = typeof data === "string" ? data : JSON.stringify(data);
      const tracker = entry.lastSentPayload;
      const isDuplicate =
        tracker !== null &&
        tracker.epoch === entry.currentEpoch &&
        tracker.payload === payload;
      if (isDuplicate && !forceNextSendFresh) {
        return;
      }
      // Consume the force flag on the first send through this view —
      // it's a one-shot signal, not a sticky mode. Update the entry-
      // wide tracker so subsequent dedup decisions (by THIS view AND
      // sibling views) honour what was actually sent to the server.
      forceNextSendFresh = false;
      entry.lastSentPayload = { epoch: entry.currentEpoch, payload };
      entry.client.send(data);
    },
    close() {
      if (viewClosed) return;
      viewClosed = true;
      entry.refCount -= 1;
      if (entry.refCount <= 0) {
        pool.delete(entry.key);
        entry.client.close();
      }
    },
    subscribe(listener) {
      if (viewClosed) return () => {};
      entry.subscribers.add(listener);
      // Late-joiner convergence: if the underlying socket is already
      // open by the time this view subscribes, the next real "open"
      // event won't fire for this listener. Schedule a microtask that
      // synthesizes the events the listener would have seen had it
      // been there from the start: open, then the cached snapshot
      // (if any).
      //
      // queueMicrotask (not setTimeout) so the listener sees open
      // BEFORE any tick-scheduled state updates from sibling code.
      // Guard against the view closing or the listener unsubscribing
      // during the microtask gap — both are legal user actions.
      if (entry.client.readyState === WebSocket.OPEN) {
        // AS-2 (5/26 DEBT-065 MID fix): refuse to replay a snapshot
        // older than STALE_SNAPSHOT_MS. Stale cache + live deltas
        // would render "snapshot from hours ago + few latest events"
        // until the next real server-side snapshot arrives.
        //
        // AT-2 (5/26 第六轮 code/arch HIGH-2 fix): when skipping the
        // stale cache, set the per-VIEW `forceNextSendFresh` flag so
        // THIS view's open-handler config send bypasses dedup and
        // forces the server to broadcast a fresh snapshot. The prior
        // implementation mutated entry-level `lastSentPayload`, which
        // also stripped sibling views' dedup tracker → next time any
        // view re-sent the same config, it would reach the wire,
        // multiplying late-join sends N× and re-introducing the
        // reconnect-storm shape AR-1 was meant to kill.
        const cached = entry.cachedSnapshot;
        if (cached === null) {
          // Nothing to replay, nothing to force.
          queueMicrotask(() => {
            if (viewClosed) return;
            if (!entry.subscribers.has(listener)) return;
            listener({ kind: "open" });
          });
        } else {
          const stale =
            nowMs() - entry.cachedSnapshotAt > entry.staleSnapshotMs;
          if (stale) forceNextSendFresh = true;
          const cachedForReplay = stale ? null : cached;
          queueMicrotask(() => {
            if (viewClosed) return;
            if (!entry.subscribers.has(listener)) return;
            listener({ kind: "open" });
            if (viewClosed) return;
            if (!entry.subscribers.has(listener)) return;
            if (cachedForReplay !== null) listener(cachedForReplay);
          });
        }
      }
      return () => {
        entry.subscribers.delete(listener);
      };
    },
    get readyState() {
      if (viewClosed) return WebSocket.CLOSED;
      return entry.client.readyState;
    },
  };
}

/** Test-only: forget all pool state. Called from spec teardown so one
 *  test's pool entries don't leak into the next. Not exported via the
 *  package barrel — direct import only. */
export function __resetPoolForTests(): void {
  for (const entry of pool.values()) entry.client.close();
  pool.clear();
}

export function connect(path: string, opts: Options = {}): WsClient {
  if (opts.shareKey === undefined) {
    // Solo mode — preserve every pre-DEBT-047 caller's semantics.
    return soloConnect(path, opts);
  }
  const key = `${path}|${opts.shareKey}`;
  let entry = pool.get(key);
  if (entry === undefined) {
    // Strip shareKey before handing options to soloConnect — the
    // underlying client doesn't need (or know about) the pool key.
    const { shareKey: _unused, ...rest } = opts;
    void _unused;
    entry = createPoolEntry(path, rest, key);
    pool.set(key, entry);
  }
  return makeView(entry);
}

/** The pre-DEBT-047 implementation, factored out so connect() can pick
 *  it directly when shareKey is omitted AND so pool entries can wrap
 *  exactly one of these per underlying socket. */
function soloConnect(path: string, opts: Omit<Options, "shareKey">): WsClient {
  const { maxBackoffMs = 30_000, noReconnect = false } = opts;
  const url = wsUrl(path);
  const listeners = new Set<(ev: WsEvent) => void>();
  let ws: WebSocket | null = null;
  let closed = false;
  let reconnectAttempts = 0;
  let reconnectTimer: number | null = null;

  const emit = (ev: WsEvent) => {
    // Snapshot listeners before iterating: a listener may unsubscribe
    // (or trigger code that does) during dispatch. AR-3 (5/26 code
    // MID-1): wrap each listener so a throw doesn't truncate fan-out.
    // The error goes to `listenerErrorHandler` (microtask rethrow by
    // default, capturable by tests).
    const snapshot = Array.from(listeners);
    for (const l of snapshot) {
      try {
        l(ev);
      } catch (e) {
        listenerErrorHandler(e);
      }
    }
  };

  const open = () => {
    ws = new WebSocket(url);
    ws.binaryType = "arraybuffer";
    ws.addEventListener("open", () => {
      reconnectAttempts = 0;
      emit({ kind: "open" });
    });
    ws.addEventListener("message", (e) => {
      if (typeof e.data === "string") {
        try {
          emit({ kind: "json", data: JSON.parse(e.data) });
        } catch {
          emit({ kind: "json", data: e.data });
        }
      } else if (e.data instanceof ArrayBuffer) {
        emit({ kind: "binary", data: e.data });
      } else if (e.data instanceof Blob) {
        e.data
          .arrayBuffer()
          .then((buf) => emit({ kind: "binary", data: buf }));
      }
    });
    ws.addEventListener("error", () => {
      emit({ kind: "error", message: "websocket error" });
    });
    ws.addEventListener("close", (e) => {
      emit({ kind: "close", code: e.code, reason: e.reason });
      ws = null;
      if (!closed && !noReconnect) scheduleReconnect();
    });
  };

  const scheduleReconnect = () => {
    if (reconnectTimer !== null || closed) return;
    const delay = Math.min(
      maxBackoffMs,
      250 * 2 ** reconnectAttempts + Math.random() * 250,
    );
    reconnectAttempts += 1;
    reconnectTimer = window.setTimeout(() => {
      reconnectTimer = null;
      if (!closed) open();
    }, delay);
  };

  open();

  return {
    send(data) {
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      if (
        typeof data === "string" ||
        data instanceof ArrayBuffer ||
        ArrayBuffer.isView(data) ||
        data instanceof Blob
      ) {
        ws.send(data as string | ArrayBufferLike | Blob);
      } else {
        ws.send(JSON.stringify(data));
      }
    },
    close(code = 1000) {
      closed = true;
      if (reconnectTimer !== null) {
        window.clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      if (ws) ws.close(code);
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    get readyState() {
      return ws?.readyState ?? WebSocket.CLOSED;
    },
  };
}
