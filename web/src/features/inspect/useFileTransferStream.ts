/**
 * useFileTransferStream — WS-based push/pull with progress + cancel
 * (MID-6 step 3/5).
 *
 * Wraps `/devices/{serial}/files/push/stream` and `.../pull/stream`.
 * Backend protocol (commit 90):
 *
 *   C → S  config first-frame  {local?, remote, force?}
 *   S → C  ready               {direction, local, remote}
 *   S → C  progress * N        {percent, bytes_transferred, file}
 *   C → S  cancel control      {type:"cancel"}
 *   S → C  closed (terminal)   {reason, ok, bytes_transferred,
 *                               duration_ms, error?}
 *
 * Replaces the old `useFileTransfers` mutations: callers get live
 * progress + a cancel button instead of a spinner that hides progress
 * for multi-GB transfers.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { wsUrl } from "../../lib/ws";

export type TransferState =
  | "idle"
  | "connecting"
  | "running"
  | "done"
  | "cancelled"
  | "error"
  | "needs_confirm"; // sensitive_path returned, caller may retry with force

export interface TransferProgress {
  percent: number | null;
  bytes_transferred: number;
  file: string | null;
}

export interface TransferResult {
  reason: string;
  ok: boolean;
  bytes_transferred: number;
  duration_ms: number;
  error: string | null;
  /** Direction the result corresponds to — caller uses to render the
   * right success/failure label without re-tracking the request. */
  direction: "push" | "pull";
  /** Final remote path for push, or final local path for pull. */
  remote: string;
  local: string;
}

export interface StartArgs {
  serial: string;
  direction: "push" | "pull";
  /** Workspace-relative for push; defaults under devices/<serial>/pulls
   * for pull when omitted. */
  local?: string;
  /** Device path. */
  remote: string;
  /** Push only — bypasses the sensitive-prefix HITL gate. */
  force?: boolean;
}

export interface UseFileTransferStream {
  state: TransferState;
  progress: TransferProgress | null;
  result: TransferResult | null;
  error: string | null;
  /** Start a new transfer. Aborts any in-flight one. */
  start: (args: StartArgs) => void;
  /** Cancel the in-flight transfer (sends cancel control frame). */
  cancel: () => void;
  /** Reset state to idle. */
  reset: () => void;
}

export function useFileTransferStream(): UseFileTransferStream {
  const wsRef = useRef<WebSocket | null>(null);
  // Track the args of the in-flight transfer so the result can carry
  // direction / local / remote without the caller re-tracking them.
  const inflightRef = useRef<StartArgs | null>(null);
  const [state, setState] = useState<TransferState>("idle");
  const [progress, setProgress] = useState<TransferProgress | null>(null);
  const [result, setResult] = useState<TransferResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const cleanup = useCallback(() => {
    const ws = wsRef.current;
    wsRef.current = null;
    if (ws && ws.readyState <= WebSocket.OPEN) {
      try {
        ws.close();
      } catch {
        // ignore
      }
    }
  }, []);

  // Tear down the WS on unmount so a fast tab switch / close doesn't
  // leave the server pumping events into the void (it would terminate
  // adb in its own finally, but this is cleaner).
  useEffect(() => () => cleanup(), [cleanup]);

  const reset = useCallback(() => {
    cleanup();
    setState("idle");
    setProgress(null);
    setResult(null);
    setError(null);
    inflightRef.current = null;
  }, [cleanup]);

  const cancel = useCallback(() => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      try {
        ws.send(JSON.stringify({ type: "cancel" }));
      } catch {
        // ignore
      }
    }
    // Don't flip state here — wait for the server's `closed` frame so
    // the UI shows the authoritative result (may still be a terminal
    // `done` if cancel raced with the final byte).
  }, []);

  const start = useCallback(
    (args: StartArgs) => {
      // Abort any in-flight transfer cleanly first.
      cleanup();
      setProgress(null);
      setResult(null);
      setError(null);
      setState("connecting");
      inflightRef.current = args;

      const path = `/devices/${encodeURIComponent(args.serial)}/files/${args.direction}/stream`;
      const ws = new WebSocket(wsUrl(path));
      wsRef.current = ws;

      ws.addEventListener("open", () => {
        const cfg: Record<string, unknown> = { remote: args.remote };
        if (args.local !== undefined) cfg.local = args.local;
        if (args.force) cfg.force = true;
        try {
          ws.send(JSON.stringify(cfg));
        } catch {
          // ignore — close handler will catch the broken socket
        }
      });

      ws.addEventListener("message", (ev) => {
        if (typeof ev.data !== "string") return;
        let msg: Record<string, unknown>;
        try {
          msg = JSON.parse(ev.data);
        } catch {
          return;
        }
        const t = msg.type as string | undefined;
        if (t === "ready") {
          setState("running");
          return;
        }
        if (t === "progress") {
          setProgress({
            percent:
              typeof msg.percent === "number" ? (msg.percent as number) : null,
            bytes_transferred:
              typeof msg.bytes_transferred === "number"
                ? (msg.bytes_transferred as number)
                : 0,
            file: typeof msg.file === "string" ? (msg.file as string) : null,
          });
          return;
        }
        if (t === "closed") {
          const reason = (msg.reason as string) || "ended";
          const ok = !!msg.ok;
          const bt =
            typeof msg.bytes_transferred === "number"
              ? (msg.bytes_transferred as number)
              : 0;
          const dur =
            typeof msg.duration_ms === "number" ? (msg.duration_ms as number) : 0;
          const err = typeof msg.error === "string" ? (msg.error as string) : null;

          const inflight = inflightRef.current;
          if (inflight) {
            setResult({
              reason,
              ok,
              bytes_transferred: bt,
              duration_ms: dur,
              error: err,
              direction: inflight.direction,
              remote: inflight.remote,
              local: inflight.local || "",
            });
          }

          if (reason === "done" && ok) {
            setState("done");
          } else if (reason === "cancelled") {
            setState("cancelled");
          } else if (reason === "sensitive_path") {
            // HITL gate — caller should pop a confirm modal then
            // call start() again with force=true.
            setState("needs_confirm");
            setError(err);
          } else {
            setState("error");
            setError(err || reason);
          }
          // Server already closed (or will close right after); release
          // our handle so the next start() doesn't see a stale ref.
          if (wsRef.current === ws) wsRef.current = null;
        }
      });

      ws.addEventListener("error", () => {
        // Browser-level WS error (DNS / TCP / handshake). Server-side
        // protocol errors come through `closed` with a reason instead.
        setState("error");
        setError("WebSocket error");
      });

      ws.addEventListener("close", () => {
        if (wsRef.current === ws) wsRef.current = null;
        // If the socket closed without a `closed` frame (proxy drop /
        // server crash), step the UI to error. Don't step from a
        // terminal state ("done"/"cancelled"/"error") — those wins.
        setState((s) =>
          s === "connecting" || s === "running" ? "error" : s,
        );
        setError((e) => e ?? "connection closed unexpectedly");
      });
    },
    [cleanup],
  );

  return { state, progress, result, error, start, cancel, reset };
}
