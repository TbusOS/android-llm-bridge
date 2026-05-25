/**
 * useWsChatStream — shared lifecycle for one-shot streaming WS chat
 * protocols (Playground + Chat, and any future LLM stream endpoint).
 *
 * The shared shape:
 *   - open a fresh WS per request (noReconnect — protocol is single-
 *     shot, a closed socket means "this turn is over")
 *   - on open: send the request payload the consumer hands us
 *   - on json: consumer routes message → its own state, signals
 *     terminal phase via the return value ("done" / "error")
 *   - on close-before-done OR error: terminal phase = "error"; the
 *     consumer's `onCloseBeforeDone` callback fires so it can write
 *     a synthetic error payload into its own state (e.g. Playground
 *     needs a `done` object to drive its error UI)
 *   - cancel() → close + phase = "cancelled" + suppress the closing
 *     socket's `close` event (would otherwise flip to "error")
 *
 * Why a shared hook: usePlaygroundChat and useChatStream re-implemented
 * the same lifecycle independently. The 5/22 stale-closure fix for
 * Playground (gotDoneRef) was a per-hook patch that didn't reach Chat;
 * cancel-then-close was buggy in both (the stale subscriber on the old
 * socket would fire `close` AFTER cancel() set phase=idle, flipping it
 * to `error`). 5/25 arch HIGH-5 + code/ui HIGH-1 calls this out.
 *
 * Fix architecture (this hook):
 *   - unsubRef: subscription pointer, cleared on every terminal path
 *     (done / error / cancel / unmount). Stale subscribers from
 *     replaced sockets cannot fire late callbacks.
 *   - cancelledRef: user-initiated cancel suppresses the next close
 *     event so phase stays "cancelled" instead of flipping to "error".
 *   - terminatedRef: stream marked done/error by onJson; subsequent
 *     close events are ignored.
 *   - myClient capture in the subscribe callback: a quick start()
 *     re-open before the previous socket's close event lands is safe
 *     because the old callback sees `clientRef.current !== myClient`
 *     and bails out (defence-in-depth against the cancelledRef path).
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { connect, type WsClient } from "../ws";

export type WsChatPhase =
  | "idle"
  | "streaming"
  | "done"
  | "error"
  | "cancelled";

export interface WsChatCloseInfo {
  /** What kind of terminal event fired — close (server / network drop)
   *  or error (browser couldn't deliver more detail). */
  kind: "close" | "error";
  /** WebSocket close code if `kind === "close"`, else undefined. */
  code?: number;
  /** Server / browser-supplied reason string. May be empty. */
  reason?: string;
}

export interface UseWsChatStreamOptions {
  /** WS path passed verbatim to lib/ws.connect, e.g. "/chat/ws". */
  path: string;
  /** Per-message router. Consumer applies the payload to its own
   *  state (setTurns, setDelta, setDone, …). Return `"done"` or
   *  `"error"` to mark the stream as terminated; return `void` to
   *  keep streaming. Anything past a terminal signal is ignored.
   *
   *  The hook reads this callback through a ref, so it can change
   *  between renders without disturbing the active subscription. */
  onJson?: (msg: unknown) => "done" | "error" | void;
  /** Fired when the WS closes (or errors) before the stream was
   *  marked done via onJson AND before the consumer called cancel().
   *  Phase has already been flipped to "error" when this runs — use
   *  it to write a synthetic payload into consumer-side state
   *  (Playground needs a `done` object for its error UI). */
  onCloseBeforeDone?: (info: WsChatCloseInfo) => void;
  /** Fired once after the WS opens but BEFORE the request payload is
   *  sent. Optional. */
  onOpen?: () => void;
}

export interface UseWsChatStreamResult<TReq> {
  phase: WsChatPhase;
  /** Open a fresh WS and send `req` on open. If a previous stream is
   *  still in flight, it is silently torn down first (the protocol is
   *  one-shot — a second start() implies "abandon and re-send"). */
  start: (req: TReq) => void;
  /** User-initiated cancel: close the socket, set phase to "cancelled",
   *  and suppress any pending close-event firing onCloseBeforeDone.
   *  No-op if no stream is in flight. */
  cancel: () => void;
  /** Move phase back to "idle" without touching the socket (assumes
   *  the socket is already closed by done / error / cancel). Use after
   *  consumer has consumed a terminal payload. */
  reset: () => void;
}

export function useWsChatStream<TReq>(
  opts: UseWsChatStreamOptions,
): UseWsChatStreamResult<TReq> {
  const { path } = opts;
  const [phase, setPhase] = useState<WsChatPhase>("idle");

  const clientRef = useRef<WsClient | null>(null);
  const unsubRef = useRef<(() => void) | null>(null);
  const terminatedRef = useRef(false);
  const cancelledRef = useRef(false);

  // Hold the latest callbacks in refs so they can change between
  // renders without re-creating start() / re-subscribing.
  const onJsonRef = useRef(opts.onJson);
  const onCloseBeforeDoneRef = useRef(opts.onCloseBeforeDone);
  const onOpenRef = useRef(opts.onOpen);
  useEffect(() => {
    onJsonRef.current = opts.onJson;
    onCloseBeforeDoneRef.current = opts.onCloseBeforeDone;
    onOpenRef.current = opts.onOpen;
  }, [opts.onJson, opts.onCloseBeforeDone, opts.onOpen]);

  const teardown = useCallback(() => {
    unsubRef.current?.();
    unsubRef.current = null;
    clientRef.current?.close();
    clientRef.current = null;
  }, []);

  // Cleanup on unmount: close any in-flight socket so a late close /
  // message can't setState on an unmounted hook.
  useEffect(() => {
    return () => {
      // Suppress the close-event side effect — unmount is not an error.
      cancelledRef.current = true;
      teardown();
    };
  }, [teardown]);

  const start = useCallback(
    (req: TReq) => {
      // Drop any in-flight stream. Mark cancelledRef so the OLD
      // socket's close event (which arrives async after close())
      // doesn't fire onCloseBeforeDone — this is a deliberate
      // re-send, not a disconnect.
      if (clientRef.current) {
        cancelledRef.current = true;
        teardown();
      }
      cancelledRef.current = false;
      terminatedRef.current = false;
      setPhase("streaming");

      const client = connect(path, { noReconnect: true });
      clientRef.current = client;
      const myClient = client;

      const unsub = client.subscribe((ev) => {
        // Defence-in-depth: an even faster re-start() would replace
        // clientRef before this listener runs. Bail out if the slot
        // no longer points at us.
        if (clientRef.current !== myClient) return;
        // Terminal states swallow further events.
        if (terminatedRef.current) return;
        if (cancelledRef.current) return;

        if (ev.kind === "open") {
          onOpenRef.current?.();
          client.send(req as unknown as object);
        } else if (ev.kind === "json") {
          const decision = onJsonRef.current?.(ev.data);
          if (decision === "done" || decision === "error") {
            terminatedRef.current = true;
            setPhase(decision);
            teardown();
          }
        } else if (ev.kind === "close" || ev.kind === "error") {
          // close-before-done = unexpected disconnect.
          terminatedRef.current = true;
          setPhase("error");
          onCloseBeforeDoneRef.current?.({
            kind: ev.kind,
            code: ev.kind === "close" ? ev.code : undefined,
            reason: ev.kind === "close" ? ev.reason : undefined,
          });
          // The socket is already closing — don't call client.close()
          // again. But DO drop the subscription so nothing else fires.
          unsubRef.current?.();
          unsubRef.current = null;
          clientRef.current = null;
        }
      });
      unsubRef.current = unsub;
    },
    [path, teardown],
  );

  const cancel = useCallback(() => {
    if (terminatedRef.current) return;
    if (!clientRef.current) {
      // Nothing in flight — still safe to bring phase to "cancelled"
      // if a consumer wants the visual feedback; but "idle" is more
      // truthful when nothing was happening. Stay idle.
      return;
    }
    cancelledRef.current = true;
    setPhase("cancelled");
    teardown();
  }, [teardown]);

  const reset = useCallback(() => {
    cancelledRef.current = false;
    terminatedRef.current = false;
    setPhase("idle");
  }, []);

  return { phase, start, cancel, reset };
}
