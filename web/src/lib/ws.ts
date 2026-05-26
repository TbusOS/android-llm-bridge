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
 *      cached snapshot — so their state machine converges without
 *      waiting for the next real event. The consumer's open handler
 *      still fires `client.send(config)`, which round-trips through
 *      the server and broadcasts a fresh snapshot to all views; that
 *      is a small known redundancy on the late-join path (rare event,
 *      structural sharing absorbs most of the React cost).
 *
 *      Used by `useAuditStream` where 2+ tabs / sub-pages open the
 *      same `/audit/stream` with identical (minutes, includeMetrics).
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
   *  Wire-format-style contract: changing the serialization shape of
   *  shareKey silently re-keys existing callers. `useAuditStream.
   *  shareKey.test.ts` pins the exact byte sequence — bump that spec
   *  and DEBT-047 when intentionally changing. */
  shareKey?: string;
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
}

const pool = new Map<string, PoolEntry>();

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
  };
  underlying.subscribe((ev) => {
    // Cache the latest snapshot for late-joiner replay. The server
    // contract is `{type:"snapshot", events, since, until}` — anything
    // else is a delta and shouldn't replace the cached snapshot.
    if (ev.kind === "json" && isSnapshotPayload(ev.data)) {
      entry.cachedSnapshot = ev;
    }
    // Snapshot the listener set before iterating: a listener handler
    // that calls .close() (refCount → 0) deletes its own subscription
    // mid-iteration, and a sibling handler that opens a new view re-
    // adds to the set. Both would corrupt a live iteration.
    const snapshot = Array.from(entry.subscribers);
    for (const l of snapshot) l(ev);
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
  return {
    send(data) {
      if (viewClosed) return;
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
        const cached = entry.cachedSnapshot;
        queueMicrotask(() => {
          if (viewClosed) return;
          if (!entry.subscribers.has(listener)) return;
          listener({ kind: "open" });
          if (viewClosed) return;
          if (!entry.subscribers.has(listener)) return;
          if (cached !== null) listener(cached);
        });
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
    // (or trigger code that does) during dispatch.
    const snapshot = Array.from(listeners);
    for (const l of snapshot) l(ev);
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
