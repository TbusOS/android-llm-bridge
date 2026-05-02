/**
 * UART live stream hook (PR-C.b/c).
 *
 * Wraps a WebSocket connection to `/uart/stream` and exposes a small
 * imperative API for the xterm.js consumer:
 *
 *   const { state, error, connect, disconnect, onBytes, sendBytes } = useUartStream();
 *   onBytes(chunk => term.write(chunk));
 *   connect(deviceSerial, { write: true });   // PR-C.c bidirectional
 *   sendBytes(new Uint8Array([0x03]));        // poke u-boot Ctrl-C
 *
 * `state` transitions: idle → connecting → ready → ended/error → idle.
 * Reconnect is **not** automatic — UART boards may disappear (USB
 * unplug); we'd rather show "ended" and let the user click Connect
 * again than spam reconnect attempts at a missing /dev/ttyUSB*.
 *
 * Bidirectional mode (PR-C.c) is opt-in: pass `{write:true}` to connect.
 * sendBytes is a no-op when write mode is off (server would drop the
 * frames anyway, and the UI shouldn't appear to swallow input).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { wsUrl } from "../../lib/ws";

export type UartStreamState = "idle" | "connecting" | "ready" | "ended" | "error";

export interface UartStreamApi {
  state: UartStreamState;
  error: string | null;
  /** Whether bidirectional write mode was negotiated on the active WS. */
  writeEnabled: boolean;
  /** Open a fresh WS. If already connected, this disconnects first.
   * Pass `{write:true}` for PR-C.c bidirectional mode. */
  connect: (device?: string | null, opts?: { write?: boolean }) => void;
  /** Close the WS cleanly (sends {type:'close'} then closes). */
  disconnect: () => void;
  /** Subscribe to incoming binary chunks. Returns an unsubscribe fn. */
  onBytes: (cb: (chunk: ArrayBuffer) => void) => () => void;
  /** Write raw bytes to the UART. No-op when writeEnabled=false. */
  sendBytes: (data: Uint8Array | ArrayBuffer) => void;
}

export function useUartStream(): UartStreamApi {
  const wsRef = useRef<WebSocket | null>(null);
  const subsRef = useRef<Set<(chunk: ArrayBuffer) => void>>(new Set());
  const [state, setState] = useState<UartStreamState>("idle");
  const [error, setError] = useState<string | null>(null);
  const [writeEnabled, setWriteEnabled] = useState(false);

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
    setWriteEnabled(false);
  }, [cleanup]);

  const connect = useCallback(
    (device?: string | null, opts?: { write?: boolean }) => {
      cleanup();
      setError(null);
      setState("connecting");
      setWriteEnabled(false);
      const wantWrite = !!opts?.write;

      const ws = new WebSocket(wsUrl("/uart/stream"));
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;

      ws.addEventListener("open", () => {
        // First-frame config (server timeout 1.5s, optional)
        try {
          const cfg: Record<string, unknown> = {};
          if (device) cfg.device = device;
          if (wantWrite) cfg.write = true;
          ws.send(JSON.stringify(cfg));
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
              setWriteEnabled(!!msg.write);
            } else if (msg.type === "closed") {
              if (
                msg.reason === "init_failed" ||
                msg.reason === "stream_error" ||
                msg.reason === "write_unsupported" ||
                msg.reason === "write_error"
              ) {
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

  const sendBytes = useCallback((data: Uint8Array | ArrayBuffer) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    try {
      ws.send(data);
    } catch {
      // ignore — error event handler will surface state
    }
  }, []);

  // Best-effort cleanup on unmount.
  useEffect(() => () => cleanup(), [cleanup]);

  return {
    state,
    error,
    writeEnabled,
    connect,
    disconnect,
    onBytes,
    sendBytes,
  };
}
