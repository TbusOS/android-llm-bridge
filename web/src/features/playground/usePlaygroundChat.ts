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

  // Cleanup on unmount — close any in-flight WS.
  useEffect(() => {
    return () => {
      clientRef.current?.close();
      clientRef.current = null;
    };
  }, []);

  const send = useCallback((req: PlaygroundRequest) => {
    // Drop any previous connection — the protocol is one-shot.
    clientRef.current?.close();
    setDelta("");
    setDone(null);
    setStatus("streaming");

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
          setDone(msg as DoneEvent);
          setStatus(msg.ok === false ? "error" : "done");
          unsub();
          client.close();
          clientRef.current = null;
        }
      } else if (ev.kind === "error" || ev.kind === "close") {
        // close before 'done' = unexpected disconnect
        if (status === "streaming") {
          setStatus("error");
        }
      }
    });
  }, [status]);

  const cancel = useCallback(() => {
    clientRef.current?.close();
    clientRef.current = null;
    if (status === "streaming") setStatus("idle");
  }, [status]);

  const reset = useCallback(() => {
    setDelta("");
    setDone(null);
    setStatus("idle");
  }, []);

  return { delta, done, status, send, cancel, reset };
}
