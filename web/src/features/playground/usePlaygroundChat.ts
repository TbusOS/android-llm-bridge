/**
 * usePlaygroundChat — WS streaming chat for the Playground page.
 *
 * Thin wrapper around `useWsChatStream` (shared lifecycle for one-shot
 * WS chat protocols). This hook only owns the Playground-specific
 * payload shapes:
 *   - a streaming `delta` buffer for the in-flight assistant message
 *   - the terminal `done` event with metrics + finish_reason + error
 *
 * Message log is held by the parent (PlaygroundPage) so multiple sends
 * append cleanly; this hook is per-request.
 *
 * H1 fix (5/25 audit): close-before-done now produces a synthetic
 * `done` payload with code=WS_DISCONNECTED so the error UI renders
 * ("status === 'error' && done" pattern in PlaygroundPage) — without
 * the synthetic, the assistant bubble silently froze with no signal.
 * Cancel-then-late-close race is fixed inside useWsChatStream (it
 * suppresses the closing socket's close event after cancel).
 */
import { useCallback, useState } from "react";

import { useWsChatStream } from "../../lib/hooks/useWsChatStream";

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

interface ServerMessage {
  type?: string;
  delta?: string;
}

export function usePlaygroundChat() {
  const [delta, setDelta] = useState("");
  const [done, setDone] = useState<DoneEvent | null>(null);

  const stream = useWsChatStream<PlaygroundRequest>({
    path: "/playground/chat/ws",
    onJson: (raw) => {
      const msg = raw as ServerMessage & Partial<DoneEvent>;
      if (msg.type === "token" && typeof msg.delta === "string") {
        setDelta((d) => d + msg.delta);
        return;
      }
      if (msg.type === "done") {
        setDone(msg as DoneEvent);
        return msg.ok === false ? "error" : "done";
      }
    },
    onCloseBeforeDone: (info) => {
      // H1 fix: PlaygroundPage's error block renders on
      // `status === "error" && chat.done` — without a synthetic done
      // here, the user sees a truncated assistant bubble + idle input
      // with no signal that the connection dropped.
      const reasonText =
        info.reason || `WebSocket closed (code ${info.code ?? "?"})`;
      setDone({
        ok: false,
        content: "",
        finish_reason: "disconnected",
        model: "",
        backend: "",
        error: {
          code: "WS_DISCONNECTED",
          message: reasonText,
        },
      });
    },
  });

  const send = useCallback(
    (req: PlaygroundRequest) => {
      setDelta("");
      setDone(null);
      stream.start(req);
    },
    [stream],
  );

  const reset = useCallback(() => {
    setDelta("");
    setDone(null);
    stream.reset();
  }, [stream]);

  // Map the 5-phase hook state to the page's 4-state surface. cancelled
  // collapses to idle (PlaygroundPage doesn't need to distinguish — it
  // just wants the input usable again).
  const status: Status =
    stream.phase === "streaming"
      ? "streaming"
      : stream.phase === "done"
        ? "done"
        : stream.phase === "error"
          ? "error"
          : "idle";

  return { delta, done, status, send, cancel: stream.cancel, reset };
}
