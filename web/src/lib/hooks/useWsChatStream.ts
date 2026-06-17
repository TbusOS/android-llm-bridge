/**
 * useWsChatStream — shared lifecycle for one-shot streaming WS chat
 * protocols (Playground + Chat, and any future LLM stream endpoint).
 *
 * Design (AO-1 redesign · arch HIGH-2 fix):
 *
 * State is a **2-piece machine**:
 *   - `phase: "idle" | "streaming" | "settled"` (3 states)
 *   - `settled: WsChatSettled | null` (discriminated union when phase=settled)
 *
 * `WsChatSettled` carries everything the consumer needs to dispatch on
 * a terminal event, in ONE typed value:
 *
 *   { kind: "done" }
 *   { kind: "error", source: "server" | "ws-close" | "ws-error",
 *     code?: number, reasonText?: string }
 *   { kind: "cancelled" }
 *
 * Why discriminated union instead of (phase + reason + cause) tri-enum
 * (the pre-AO-1 design):
 *   - 3 enums (3 phase × 3 reason × 4 cause) = 36 nominal states for
 *     5 legal combinations. consumers wrote 4-way if/else mapping tables.
 *   - single union = TS narrows on `kind` switch · 0 mapping tables
 *   - 2nd consumer onwards can reuse the type without re-deriving rules
 *
 * Consumer pattern:
 * ```ts
 * const stream = useWsChatStream<Req>({
 *   path: "/foo/ws",
 *   onJson: (raw, { markSettled }) => {
 *     // parse raw, apply to consumer state
 *     if (isDoneMsg(raw)) markSettled({ kind: "done" });
 *   },
 *   onSettled: (info) => {
 *     switch (info.kind) {
 *       case "done": / * already wrote via onJson * / break;
 *       case "error": setError(info.source, info.reasonText); break;
 *       case "cancelled": markTurnCancelled(); break;
 *     }
 *   },
 * });
 * ```
 *
 * Race protection (unchanged from prior implementations):
 *   - unsubRef: subscription pointer cleared on every terminal path
 *   - cancelledRef: pending close events from a cancelled stream
 *     are swallowed (don't flip phase back to settled-with-error)
 *   - settledRef: extra cleanup after onSettled fires once; further
 *     events are ignored
 *   - myClient capture: a fast re-start before old socket's close
 *     arrives sees `clientRef.current !== myClient` and bails out
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { connect, type WsClient } from "../ws";

export type WsChatPhase = "idle" | "streaming" | "settled";

/**
 * Discriminated union describing why the stream settled. ONE type
 * carries everything consumers need to switch on.
 *
 * `error.source` distinguishes the failure path:
 *   - "server": consumer's onJson called markSettled({ kind: "error" })
 *     based on a server payload (e.g. `done.ok = false`)
 *   - "ws-close": socket close event before settled (network drop /
 *     server reset / proxy timeout). `code` + `reasonText` from the
 *     WebSocket CloseEvent.
 *   - "ws-error": socket error event before settled. `reasonText`
 *     from the browser-supplied error message.
 */
export type WsChatSettled =
  | { kind: "done" }
  | {
      kind: "error";
      source: "server" | "ws-close" | "ws-error";
      code?: number;
      reasonText?: string;
    }
  | { kind: "cancelled" };

export interface OnJsonHelpers {
  /** Terminate the stream from inside onJson. Pass the full settled
   *  variant; `onSettled` will fire with this exact object. */
  markSettled: (info: WsChatSettled) => void;
}

export interface UseWsChatStreamOptions {
  /** WS path passed verbatim to lib/ws.connect, e.g. "/chat/ws". */
  path: string;
  /** Per-message router. Consumer applies payload to its own state
   *  (setTurns / setDelta / setDone). Call `helpers.markSettled()`
   *  with a `WsChatSettled` variant to terminate the stream. The
   *  hook reads this callback via ref so it can change between
   *  renders without re-subscribing. */
  onJson?: (msg: unknown, helpers: OnJsonHelpers) => void;
  /** Fired exactly once when phase transitions to "settled", no
   *  matter what triggered it (onJson markSettled / cancel /
   *  ws-close / ws-error). The info value is the SAME object stored
   *  in `result.settled` — consumer can switch on `info.kind`. */
  onSettled?: (info: WsChatSettled) => void;
  /** Fired once after the WS opens but BEFORE the request payload is
   *  sent. Optional. */
  onOpen?: () => void;
}

export interface UseWsChatStreamResult<TReq> {
  phase: WsChatPhase;
  /** Set after phase → settled. Read alongside phase to dispatch on
   *  `info.kind`. Cleared by reset(). */
  settled: WsChatSettled | null;
  /** Open a fresh WS and send `req` on open. If a previous stream is
   *  still in flight, it is silently torn down first. */
  start: (req: TReq) => void;
  /** User-initiated cancel: close the socket, transition to settled
   *  with `kind: "cancelled"`. Suppresses any pending close-event
   *  from re-firing onSettled. No-op if nothing in flight. */
  cancel: () => void;
  /** Move phase back to "idle" without touching the socket (assumes
   *  the socket is already closed by terminal path). Idempotent —
   *  if a stream is somehow still live (consumer bug or unusual
   *  timing), tear it down first. */
  reset: () => void;
}

export function useWsChatStream<TReq>(
  opts: UseWsChatStreamOptions,
): UseWsChatStreamResult<TReq> {
  const { path } = opts;
  const [phase, setPhase] = useState<WsChatPhase>("idle");
  const [settled, setSettled] = useState<WsChatSettled | null>(null);

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
    (info: WsChatSettled) => {
      if (settledRef.current) return;
      settledRef.current = true;
      setSettled(info);
      setPhase("settled");
      // CR-7: teardown must run even if a consumer's onSettled throws —
      // otherwise the socket + subscription leak into the next start() /
      // unmount. finally keeps the close guaranteed; the callback error
      // still propagates (we don't swallow consumer bugs).
      try {
        onSettledRef.current?.(info);
      } finally {
        teardown();
      }
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
      // doesn't fire onSettled — this is a deliberate re-send, not
      // a disconnect.
      if (clientRef.current) {
        cancelledRef.current = true;
        teardown();
      }
      cancelledRef.current = false;
      settledRef.current = false;
      setSettled(null);
      setPhase("streaming");

      const client = connect(path, { noReconnect: true });
      clientRef.current = client;
      const myClient = client;

      const helpers: OnJsonHelpers = {
        markSettled: (info) => settle(info),
      };

      const unsub = client.subscribe((ev) => {
        // Defence-in-depth: a faster re-start() would replace
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
        } else if (ev.kind === "close") {
          settle({
            kind: "error",
            source: "ws-close",
            code: ev.code,
            reasonText: ev.reason,
          });
        } else if (ev.kind === "error") {
          settle({
            kind: "error",
            source: "ws-error",
            reasonText: ev.message,
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
    settle({ kind: "cancelled" });
  }, [settle]);

  const reset = useCallback(() => {
    if (clientRef.current) {
      cancelledRef.current = true;
      teardown();
    }
    cancelledRef.current = false;
    settledRef.current = false;
    setSettled(null);
    setPhase("idle");
  }, [teardown]);

  return { phase, settled, start, cancel, reset };
}
