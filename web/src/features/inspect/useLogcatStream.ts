/**
 * adb logcat live stream hook (PR-D).
 *
 * Sibling of useUartStream — same WS state machine, different URL +
 * accepts an optional filter spec ("*:E", "MyApp:V *:S", etc) plus
 * a `tags` shortcut that the server flattens to the same.
 *
 *   const { state, error, connect, disconnect, onBytes } = useLogcatStream();
 *   onBytes(chunk => term.write(chunk));
 *   connect({ device: '7bcb...', filter: '*:E' });
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { wsUrl } from "../../lib/ws";

export type LogcatStreamState =
  | "idle"
  | "connecting"
  | "ready"
  | "ended"
  | "error";

export interface LogcatConnectArgs {
  device?: string | null;
  filter?: string | null;
  tags?: string[] | null;
}

export interface LogcatStreamApi {
  state: LogcatStreamState;
  error: string | null;
  connect: (args?: LogcatConnectArgs) => void;
  disconnect: () => void;
  onBytes: (cb: (chunk: ArrayBuffer) => void) => () => void;
}

export function useLogcatStream(): LogcatStreamApi {
  const wsRef = useRef<WebSocket | null>(null);
  const subsRef = useRef<Set<(chunk: ArrayBuffer) => void>>(new Set());
  const [state, setState] = useState<LogcatStreamState>("idle");
  const [error, setError] = useState<string | null>(null);

  const cleanup = useCallback(() => {
    const ws = wsRef.current;
    wsRef.current = null;
    if (ws && ws.readyState <= WebSocket.OPEN) {
      try {
        ws.send(JSON.stringify({ type: "close" }));
      } catch {
        // ignore
      }
      try {
        ws.close();
      } catch {
        // ignore
      }
    }
  }, []);

  const disconnect = useCallback(() => {
    cleanup();
    setState("idle");
    setError(null);
  }, [cleanup]);

  const connect = useCallback(
    (args?: LogcatConnectArgs) => {
      cleanup();
      setError(null);
      setState("connecting");

      const ws = new WebSocket(wsUrl("/logcat/stream"));
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;

      ws.addEventListener("open", () => {
        const config: Record<string, unknown> = {};
        if (args?.device) config.device = args.device;
        if (args?.filter) config.filter = args.filter;
        if (args?.tags && args.tags.length > 0) config.tags = args.tags;
        try {
          ws.send(JSON.stringify(config));
        } catch {
          // ignore
        }
      });

      ws.addEventListener("message", (ev) => {
        if (typeof ev.data === "string") {
          try {
            const msg = JSON.parse(ev.data);
            if (msg.type === "ready") {
              setState("ready");
            } else if (msg.type === "closed") {
              if (
                msg.reason === "init_failed" ||
                msg.reason === "stream_error" ||
                msg.reason === "unsupported_source" ||
                msg.reason === "bad_filter"
              ) {
                setState("error");
                setError(msg.error || msg.reason);
              } else {
                setState("ended");
              }
            }
          } catch {
            // ignore non-JSON text frames
          }
          return;
        }
        if (ev.data instanceof ArrayBuffer) {
          subsRef.current.forEach((cb) => cb(ev.data));
        }
      });

      ws.addEventListener("error", () => {
        setState("error");
        setError("WebSocket error");
      });

      ws.addEventListener("close", () => {
        setState((s) => (s === "ready" || s === "connecting" ? "ended" : s));
        if (wsRef.current === ws) {
          wsRef.current = null;
        }
      });
    },
    [cleanup],
  );

  const onBytes = useCallback((cb: (chunk: ArrayBuffer) => void) => {
    subsRef.current.add(cb);
    return () => {
      subsRef.current.delete(cb);
    };
  }, []);

  useEffect(() => () => cleanup(), [cleanup]);

  return { state, error, connect, disconnect, onBytes };
}
