/**
 * UART live stream hook (PR-C.b).
 *
 * Wraps a WebSocket connection to `/uart/stream` and exposes a small
 * imperative API for the xterm.js consumer:
 *
 *   const { state, error, connect, disconnect, onBytes } = useUartStream();
 *   onBytes(chunk => term.write(chunk));
 *   connect(deviceSerial);
 *
 * `state` transitions: idle → connecting → ready → ended/error → idle.
 * Reconnect is **not** automatic — UART boards may disappear (USB
 * unplug); we'd rather show "ended" and let the user click Connect
 * again than spam reconnect attempts at a missing /dev/ttyUSB*.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { wsUrl } from "../../lib/ws";

export type UartStreamState = "idle" | "connecting" | "ready" | "ended" | "error";

export interface UartStreamApi {
  state: UartStreamState;
  error: string | null;
  /** Open a fresh WS. If already connected, this disconnects first. */
  connect: (device?: string | null) => void;
  /** Close the WS cleanly (sends {type:'close'} then closes). */
  disconnect: () => void;
  /** Subscribe to incoming binary chunks. Returns an unsubscribe fn. */
  onBytes: (cb: (chunk: ArrayBuffer) => void) => () => void;
}

export function useUartStream(): UartStreamApi {
  const wsRef = useRef<WebSocket | null>(null);
  const subsRef = useRef<Set<(chunk: ArrayBuffer) => void>>(new Set());
  const [state, setState] = useState<UartStreamState>("idle");
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
    (device?: string | null) => {
      cleanup();
      setError(null);
      setState("connecting");

      const ws = new WebSocket(wsUrl("/uart/stream"));
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;

      ws.addEventListener("open", () => {
        // First-frame config (server timeout 1.5s, optional)
        try {
          ws.send(JSON.stringify(device ? { device } : {}));
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
              if (msg.reason === "init_failed" || msg.reason === "stream_error") {
                setState("error");
                setError(msg.error || msg.reason);
              } else {
                setState("ended");
              }
            }
          } catch {
            // non-JSON text frame — ignore
          }
          return;
        }
        // Binary UART chunk
        if (ev.data instanceof ArrayBuffer) {
          subsRef.current.forEach((cb) => cb(ev.data));
        }
      });

      ws.addEventListener("error", () => {
        setState("error");
        setError("WebSocket error");
      });

      ws.addEventListener("close", () => {
        // Only step state down if we weren't already in a terminal state.
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

  // Best-effort cleanup on unmount.
  useEffect(() => () => cleanup(), [cleanup]);

  return { state, error, connect, disconnect, onBytes };
}
