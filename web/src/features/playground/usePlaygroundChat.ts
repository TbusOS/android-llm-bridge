/**
 * usePlaygroundChat — WS streaming chat for the Playground page.
 *
 * Owns:
 *   - the websocket lifecycle (open per send, close on done — the
 *     server's protocol is one-shot per request, not a long-lived
 *     channel like Audit)
 *   - a streaming buffer for the in-flight assistant message
 *   - the metrics + finish_reason from the terminal `done` event
 *
 * Message log is held by the parent (PlaygroundPage) so multiple
 * sends append cleanly; this hook is per-request.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { connect, type WsClient } from "../../lib/ws";

export interface PlaygroundRequest {
  backend: string;
  model: string | null;
  base_url?: string | null;
  messages: Array<{ role: "user" | "assistant" | "system"; content: string }>;
  system?: string;
  temperature?: number;
  top_p?: number;
  num_predict?: number;
  stop?: string[];
  seed?: number;
}

export interface DoneEvent {
  ok: boolean;
  content: string;
  thinking?: string;
  finish_reason: string;
  model: string;
  backend: string;
  metrics?: Record<string, unknown> | null;
  error?: { code: string; message: string; suggestion?: string } | null;
}

type Status = "idle" | "streaming" | "done" | "error";

export function usePlaygroundChat() {
  const [delta, setDelta] = useState("");
  const [done, setDone] = useState<DoneEvent | null>(null);
  const [status, setStatus] = useState<Status>("idle");
  const clientRef = useRef<WsClient | null>(null);
  // Ref tracker for "we already received the `done` terminal event".
  // The subscribe callback captures the `status` closure at send-time
  // (idle / done), not the current React state, so testing
  // `if (status === "streaming") setStatus("error")` always sees the
  // stale snapshot and never fires on close-before-done. Ref reads are
  // always live, so we flip it true on done and gate the error branch
  // on its NEGATION instead.
  const gotDoneRef = useRef(false);

  // Cleanup on unmount — close any in-flight WS.
  useEffect(() => {
    return () => {
      clientRef.current?.close();
      clientRef.current = null;
    };
  }, []);

  // send is stable across renders — no `status` in deps because we now
  // use `gotDoneRef` instead of reading state inside the closure.
  // Without this, every status flip rebuilt `send`, which cascaded
  // through React.memo'd children if any consumer ever wraps the hook.
  const send = useCallback((req: PlaygroundRequest) => {
    // Drop any previous connection — the protocol is one-shot.
    clientRef.current?.close();
    setDelta("");
    setDone(null);
    setStatus("streaming");
    gotDoneRef.current = false;

    const client = connect("/playground/chat/ws", { noReconnect: true });
    clientRef.current = client;

    const unsub = client.subscribe((ev) => {
      if (ev.kind === "open") {
        client.send(req);
      } else if (ev.kind === "json") {
        const msg = ev.data as { type?: string; delta?: string } & Partial<DoneEvent>;
        if (msg.type === "token" && typeof msg.delta === "string") {
          setDelta((d) => d + msg.delta);
        } else if (msg.type === "done") {
          gotDoneRef.current = true;
          setDone(msg as DoneEvent);
          setStatus(msg.ok === false ? "error" : "done");
          unsub();
          client.close();
          clientRef.current = null;
        }
      } else if (ev.kind === "error" || ev.kind === "close") {
        // close-before-done = unexpected disconnect → surface as error.
        // We read the ref (live) instead of `status` (stale closure).
        if (!gotDoneRef.current) {
          setStatus("error");
        }
      }
    });
  }, []);

  const cancel = useCallback(() => {
    clientRef.current?.close();
    clientRef.current = null;
    // cancel is fine reading status because it's only called from a
    // user click during the live render — closure is current.
    if (!gotDoneRef.current) setStatus("idle");
  }, []);

  const reset = useCallback(() => {
    setDelta("");
    setDone(null);
    setStatus("idle");
    gotDoneRef.current = false;
  }, []);

  return { delta, done, status, send, cancel, reset };
}
