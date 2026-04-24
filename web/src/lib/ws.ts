/**
 * Lightweight WebSocket helper — wraps the browser WebSocket with
 * JSON-or-binary demux, automatic reconnect (exponential back-off up
 * to 30s), and a subscriber interface that tracks open/close.
 *
 * Each long-lived tab (Chat / Terminal / Metrics / Playground) owns
 * one instance. Short-lived query pages prefer fetch + useQuery.
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

export function connect(path: string, opts: Options = {}): WsClient {
  const { maxBackoffMs = 30_000, noReconnect = false } = opts;
  const url = wsUrl(path);
  const listeners = new Set<(ev: WsEvent) => void>();
  let ws: WebSocket | null = null;
  let closed = false;
  let reconnectAttempts = 0;
  let reconnectTimer: number | null = null;

  const emit = (ev: WsEvent) => listeners.forEach((l) => l(ev));

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
