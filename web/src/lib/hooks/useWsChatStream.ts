/**
 * useWsChatStream — shared lifecycle for one-shot streaming WS chat
 * protocols (Playground + Chat, and any future LLM stream endpoint).
 *
 * The shared shape:
 *   - open a fresh WS per request (noReconnect — protocol is single-
 *     shot, a closed socket means "this turn is over")
 *   - on open: send the request payload the consumer hands us
 *   - on json: consumer routes message → its own state, optionally
 *     calls helpers.markSettled() to terminate the stream
 *   - on close-before-settled OR error: phase = "settled" with
 *     reason="error", consumer's onSettled callback fires so it can
 *     write a synthetic error payload into its own state
 *   - cancel() → close + phase = "settled" with reason="cancelled",
 *     suppresses the closing socket's `close` event
 *
 * API design (AL-1 redesign · arch HIGH-1/4 fix):
 *
 * Phase enum is **3 states** not 5:
 *   - "idle"      — no in-flight request
 *   - "streaming" — request sent, awaiting json messages
 *   - "settled"   — terminal (consumer reads `settledReason` to know
 *                   why: "done" / "error" / "cancelled")
 *
 * Why 3 + reason enum, not 5 phases: both pre-existing consumers
 * collapsed done/error/cancelled into their own UI state — none
 * needed all 5. A reason enum captures intent without forcing
 * consumers to write mapping code, and a new consumer only needs
 * to handle 3 phases + 3 reason cases. Empirically this is the
 * minimum surface (see L-051 sec audit: the previous 5-phase
 * design forced both consumers to duplicate synthetic-done /
 * cancel-then-close fallback).
 *
 * Callback model is **single onSettled enum** not dual callbacks:
 *   - onJson(msg, { markSettled }) for routing payload + optional
 *     terminal marker
 *   - onSettled(info) ALWAYS fires once when phase → settled, no
 *     matter what caused it (markSettled / cancel / socket-close
 *     / socket-error). Info carries `reason` + `cause` so consumer
 *     can write a synthetic payload (or skip if reason=done).
 *
 * Race protection (unchanged):
 *   - unsubRef: subscription pointer cleared on every terminal path
 *   - cancelledRef: pending close events from a cancelled stream are
 *     swallowed (don't flip phase back to "settled with error")
 *   - settledRef: extra cleanup after onSettled fires once; further
 *     events are ignored
 *   - myClient capture: a fast re-start before old socket's close
 *     arrives sees `clientRef.current !== myClient` and bails out
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { connect, type WsClient } from "../ws";

export type WsChatPhase = "idle" | "streaming" | "settled";

export type WsChatSettledReason = "done" | "error" | "cancelled";

export interface WsChatSettledInfo {
  /** What concluded the stream. */
  reason: WsChatSettledReason;
  /** Lower-level cause. "server-done" = onJson markSettled with reason
   *  "done"/"error" / "user-cancel" = consumer called cancel() /
   *  "ws-close" = socket dropped before consumer settled /
   *  "ws-error" = socket error event before consumer settled. */
  cause: "server-done" | "user-cancel" | "ws-close" | "ws-error";
  /** WebSocket close code if `cause === "ws-close"`, else undefined. */
  code?: number;
  /** Server / browser-supplied reason string. May be empty. */
  reasonText?: string;
}

export interface OnJsonHelpers {
  /** Terminate the stream from inside onJson. The phase will flip to
   *  "settled" and onSettled will fire with cause="server-done". */
  markSettled: (reason: "done" | "error") => void;
}

export interface UseWsChatStreamOptions {
  /** WS path passed verbatim to lib/ws.connect, e.g. "/chat/ws". */
  path: string;
  /** Per-message router. Consumer applies payload to its own state
   *  (setTurns / setDelta / setDone). Call `helpers.markSettled()`
   *  to terminate the stream — useful when the message itself signals
   *  the end (a "done" type, etc). The hook reads this callback via
   *  ref so it can change between renders without re-subscribing. */
  onJson?: (msg: unknown, helpers: OnJsonHelpers) => void;
  /** Fired exactly once when phase transitions to "settled", no
   *  matter what triggered it. Info.reason distinguishes
   *  done / error / cancelled. Use to write a synthetic payload
   *  for error/cancelled (consumer's UI may need a `done` object). */
  onSettled?: (info: WsChatSettledInfo) => void;
  /** Fired once after the WS opens but BEFORE the request payload is
   *  sent. Optional. */
  onOpen?: () => void;
}

export interface UseWsChatStreamResult<TReq> {
  phase: WsChatPhase;
  /** Set after phase → settled. Read alongside phase to dispatch on
   *  reason. Cleared by reset(). */
  settledReason: WsChatSettledReason | null;
  /** Open a fresh WS and send `req` on open. If a previous stream is
   *  still in flight, it is silently torn down first. */
  start: (req: TReq) => void;
  /** User-initiated cancel: close the socket, transition to settled
   *  with reason="cancelled". Suppresses any pending close-event from
   *  re-firing onSettled. No-op if nothing in flight. */
  cancel: () => void;
  /** Move phase back to "idle" without touching the socket (assumes
   *  the socket is already closed by terminal path). */
  reset: () => void;
}

export function useWsChatStream<TReq>(
  opts: UseWsChatStreamOptions,
): UseWsChatStreamResult<TReq> {
  const { path } = opts;
  const [phase, setPhase] = useState<WsChatPhase>("idle");
  const [settledReason, setSettledReason] =
    useState<WsChatSettledReason | null>(null);

  const clientRef = useRef<WsClient | null>(null);
  const unsubRef = useRef<(() => void) | null>(null);
  const settledRef = useRef(false);
  const cancelledRef = useRef(false);

  // Hold the latest callbacks in refs so they can change between
  // renders without re-creating start() / re-subscribing.
  const onJsonRef = useRef(opts.onJson);
  const onSettledRef = useRef(opts.onSettled);
  const onOpenRef = useRef(opts.onOpen);
  useEffect(() => {
    onJsonRef.current = opts.onJson;
    onSettledRef.current = opts.onSettled;
    onOpenRef.current = opts.onOpen;
  }, [opts.onJson, opts.onSettled, opts.onOpen]);

  const teardown = useCallback(() => {
    unsubRef.current?.();
    unsubRef.current = null;
    clientRef.current?.close();
    clientRef.current = null;
  }, []);

  /** Single terminal entry point. All paths to settled go through
   *  here so onSettled fires exactly once + flags are set in a
   *  consistent order. */
  const settle = useCallback(
    (info: WsChatSettledInfo) => {
      if (settledRef.current) return;
      settledRef.current = true;
      setSettledReason(info.reason);
      setPhase("settled");
      onSettledRef.current?.(info);
      teardown();
    },
    [teardown],
  );

  // Cleanup on unmount: close any in-flight socket so a late close /
  // message can't setState on an unmounted hook. Suppress side
  // effects — unmount is not an error.
  useEffect(() => {
    return () => {
      cancelledRef.current = true;
      settledRef.current = true;
      teardown();
    };
  }, [teardown]);

  const start = useCallback(
    (req: TReq) => {
      // Drop any in-flight stream. Mark cancelledRef so the OLD
      // socket's close event (which arrives async after close())
      // doesn't fire onSettled — this is a deliberate re-send, not a
      // disconnect.
      if (clientRef.current) {
        cancelledRef.current = true;
        teardown();
      }
      cancelledRef.current = false;
      settledRef.current = false;
      setSettledReason(null);
      setPhase("streaming");

      const client = connect(path, { noReconnect: true });
      clientRef.current = client;
      const myClient = client;

      const helpers: OnJsonHelpers = {
        markSettled: (reason) => {
          settle({ reason, cause: "server-done" });
        },
      };

      const unsub = client.subscribe((ev) => {
        // Defence-in-depth: an even faster re-start() would replace
        // clientRef before this listener runs. Bail out if the slot
        // no longer points at us.
        if (clientRef.current !== myClient) return;
        if (settledRef.current) return;
        if (cancelledRef.current) return;

        if (ev.kind === "open") {
          onOpenRef.current?.();
          client.send(req as unknown as object);
        } else if (ev.kind === "json") {
          onJsonRef.current?.(ev.data, helpers);
        } else if (ev.kind === "close" || ev.kind === "error") {
          // close-before-settled = unexpected disconnect.
          settle({
            reason: "error",
            cause: ev.kind === "close" ? "ws-close" : "ws-error",
            code: ev.kind === "close" ? ev.code : undefined,
            reasonText: ev.kind === "close" ? ev.reason : ev.message,
          });
        }
      });
      unsubRef.current = unsub;
    },
    [path, settle, teardown],
  );

  const cancel = useCallback(() => {
    if (settledRef.current) return;
    if (!clientRef.current) return;
    cancelledRef.current = true;
    settle({ reason: "cancelled", cause: "user-cancel" });
  }, [settle]);

  const reset = useCallback(() => {
    // reset is idempotent: if a stream is somehow still live (consumer
    // bug or unusual timing), tear it down first instead of silently
    // leaving the socket dangling. The reason is null in idle.
    if (clientRef.current) {
      cancelledRef.current = true;
      teardown();
    }
    cancelledRef.current = false;
    settledRef.current = false;
    setSettledReason(null);
    setPhase("idle");
  }, [teardown]);

  return { phase, settledReason, start, cancel, reset };
}
