/**
 * adb shell PTY session hook (PR-E).
 *
 * Bidirectional WS to `/terminal/ws` — typing in xterm flows to the
 * shell stdin, shell stdout flows back into xterm.
 *
 * Differs from useUartStream / useLogcatStream (read-only) in that the
 * client `sendBytes` API exists for keystrokes, plus `sendResize` to
 * keep the server-side PTY in sync with the visible window.
 *
 * Protocol (terminal_route.py):
 *   C → S first JSON (1.5 s timeout): {device, rows, cols, read_only}
 *   S → C ready JSON: {type:'ready', device, transport, session_id}
 *   C ↔ S binary: stdin / stdout bytes
 *   C → S {type:'resize', rows, cols}
 *   S → C {type:'hitl_request', command, rule, reason}
 *   C → S {type:'hitl_response', approve, allow_session}
 *   S → C {type:'closed', exit_code, error?}
 *
 * PR-E.v2: HITL prompts surface via `onHitl` subscribers — the UI
 * (ShellTab) opens a modal and replies via `respondHitl`. Default
 * fallback is auto-deny (preserves v1 behaviour for tests / consumers
 * that never wire onHitl). The hook auto-denies only if NO subscriber
 * is registered when a request arrives, so the shell never hangs.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { wsUrl } from "../../lib/ws";

export type TerminalSessionState =
  | "idle"
  | "connecting"
  | "ready"
  | "ended"
  | "error";

export interface TerminalConnectArgs {
  device?: string | null;
  rows?: number;
  cols?: number;
  readOnly?: boolean;
}

export interface HitlRequest {
  command: string;
  rule: string;
  reason: string;
  /** Whatever else the server includes — surfaced verbatim to the modal. */
  raw: Record<string, unknown>;
}

export interface TerminalSessionApi {
  state: TerminalSessionState;
  error: string | null;
  exitCode: number | null;
  connect: (args?: TerminalConnectArgs) => void;
  disconnect: () => void;
  sendBytes: (data: Uint8Array | string) => void;
  sendResize: (rows: number, cols: number) => void;
  onBytes: (cb: (chunk: ArrayBuffer) => void) => () => void;
  /** Subscribe to HITL prompts. Returns unsubscribe. If NO subscriber
   * is registered when one arrives the hook auto-denies (no hang). */
  onHitl: (cb: (req: HitlRequest) => void) => () => void;
  /** Reply to the most recent HITL request. */
  respondHitl: (approve: boolean, allowSession: boolean) => void;
}

export function useTerminalSession(): TerminalSessionApi {
  const wsRef = useRef<WebSocket | null>(null);
  const subsRef = useRef<Set<(chunk: ArrayBuffer) => void>>(new Set());
  const hitlSubsRef = useRef<Set<(req: HitlRequest) => void>>(new Set());
  const [state, setState] = useState<TerminalSessionState>("idle");
  const [error, setError] = useState<string | null>(null);
  const [exitCode, setExitCode] = useState<number | null>(null);

  const cleanup = useCallback(() => {
    const ws = wsRef.current;
    wsRef.current = null;
    if (ws && ws.readyState <= WebSocket.OPEN) {
      try {
        ws.send(JSON.stringify({ type: "control", action: "close" }));
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
    setExitCode(null);
  }, [cleanup]);

  const connect = useCallback(
    (args?: TerminalConnectArgs) => {
      cleanup();
      setError(null);
      setExitCode(null);
      setState("connecting");

      const ws = new WebSocket(wsUrl("/terminal/ws"));
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;

      ws.addEventListener("open", () => {
        const config: Record<string, unknown> = {
          rows: args?.rows ?? 30,
          cols: args?.cols ?? 100,
          read_only: !!args?.readOnly,
        };
        if (args?.device) config.device = args.device;
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
              if (typeof msg.exit_code === "number") {
                setExitCode(msg.exit_code);
              }
              if (msg.error) {
                setState("error");
                setError(
                  typeof msg.error === "string"
                    ? msg.error
                    : msg.error.message || JSON.stringify(msg.error),
                );
              } else {
                setState("ended");
              }
            } else if (msg.type === "hitl_request") {
              const subs = hitlSubsRef.current;
              if (subs.size > 0) {
                // PR-E.v2: hand the request to subscribers (ShellTab
                // opens the modal). Modal is responsible for calling
                // respondHitl — we don't auto-deny here, otherwise
                // a subscriber that takes a beat to render would race
                // the auto-deny and the modal would surface a stale
                // request the server already answered.
                const req: HitlRequest = {
                  command: String(msg.command ?? ""),
                  rule: String(msg.rule ?? ""),
                  reason: String(msg.reason ?? ""),
                  raw: msg,
                };
                subs.forEach((cb) => cb(req));
              } else {
                // No subscriber — fall back to v1 auto-deny so a
                // headless / test consumer never hangs the shell.
                console.warn("HITL prompt (no subscriber, auto-denied):", msg);
                try {
                  ws.send(
                    JSON.stringify({
                      type: "hitl_response",
                      approve: false,
                      allow_session: false,
                    }),
                  );
                } catch {
                  // ignore
                }
              }
            }
          } catch {
            // ignore non-JSON text
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

  const sendBytes = useCallback((data: Uint8Array | string) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    try {
      if (typeof data === "string") {
        ws.send(new TextEncoder().encode(data));
      } else {
        // Browser WebSocket binary frame requires ArrayBuffer or Blob;
        // a Uint8Array view is accepted.
        ws.send(data);
      }
    } catch {
      // ignore
    }
  }, []);

  const sendResize = useCallback((rows: number, cols: number) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    try {
      ws.send(JSON.stringify({ type: "resize", rows, cols }));
    } catch {
      // ignore
    }
  }, []);

  const onBytes = useCallback((cb: (chunk: ArrayBuffer) => void) => {
    subsRef.current.add(cb);
    return () => {
      subsRef.current.delete(cb);
    };
  }, []);

  const onHitl = useCallback((cb: (req: HitlRequest) => void) => {
    hitlSubsRef.current.add(cb);
    return () => {
      hitlSubsRef.current.delete(cb);
    };
  }, []);

  const respondHitl = useCallback(
    (approve: boolean, allowSession: boolean) => {
      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      try {
        ws.send(
          JSON.stringify({
            type: "hitl_response",
            approve,
            allow_session: allowSession,
          }),
        );
      } catch {
        // ignore
      }
    },
    [],
  );

  useEffect(() => () => cleanup(), [cleanup]);

  return {
    state,
    error,
    exitCode,
    connect,
    disconnect,
    sendBytes,
    sendResize,
    onBytes,
    onHitl,
    respondHitl,
  };
}
