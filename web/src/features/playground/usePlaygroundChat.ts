/**
 * usePlaygroundChat — WS streaming chat for the Playground page.
 *
 * Thin wrapper around `useWsChatStream` (shared lifecycle for one-shot
 * WS chat protocols). This hook only owns the Playground-specific
 * payload shapes:
 *   - a streaming `delta` buffer for the in-flight assistant message
 *   - the terminal `done` event with metrics + finish_reason + error
 *
 * Maps the underlying 3-phase hook (`idle | streaming | settled`) to
 * the 4-state surface PlaygroundPage already uses
 * (`idle | streaming | done | error`). Cancelled folds to idle —
 * PlaygroundPage doesn't need to distinguish, it just wants the input
 * usable again.
 *
 * H1 fix (5/25 audit) preserved: close-before-settled / cancel paths
 * write a synthetic `done` with code=WS_DISCONNECTED so the
 * `status === "error" && done` UI block always renders something. The
 * synthetic write moved into the `onSettled` callback after AL-1's
 * API redesign — single terminal entry instead of dual paths.
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
    onJson: (raw, { markSettled }) => {
      const msg = raw as ServerMessage & Partial<DoneEvent>;
      if (msg.type === "token" && typeof msg.delta === "string") {
        setDelta((d) => d + msg.delta);
        return;
      }
      if (msg.type === "done") {
        setDone(msg as DoneEvent);
        markSettled(msg.ok === false ? "error" : "done");
      }
    },
    onSettled: (info) => {
      // Only need synthetic done payload for the disconnect paths.
      // server-done already wrote a real done via onJson above.
      // user-cancel + ws-close + ws-error all benefit from the
      // synthetic so PlaygroundPage's `status === "error" && done`
      // block can render *something*. For cancelled we fold to idle
      // (status mapping below), so no synthetic is needed — but
      // ws-close / ws-error need WS_DISCONNECTED so the user sees
      // why the bubble froze.
      if (info.cause === "ws-close" || info.cause === "ws-error") {
        // 5/25 AM-2 (sec MID-13): close.reason is server-supplied · a
        // custom-built proxy / debug tool can put arbitrary bytes
        // there. Clamp the rendered message so it doesn't blow up
        // layout. (Browser already enforces 123-byte limit on real
        // WebSocket close frames; this is defence-in-depth for
        // synthetic / hostile producers.)
        const raw =
          info.reasonText || `WebSocket closed (code ${info.code ?? "?"})`;
        const reasonText =
          raw.length > 200 ? `${raw.slice(0, 200)}…` : raw;
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
      }
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

  // Map the 3-phase hook + settledReason to the page's 4-state surface.
  // cancelled collapses to idle (PlaygroundPage doesn't distinguish —
  // it just wants the input usable again). done.ok=false → error too.
  let status: Status;
  if (stream.phase === "streaming") {
    status = "streaming";
  } else if (stream.phase === "settled" && stream.settledReason === "done") {
    status = "done";
  } else if (stream.phase === "settled" && stream.settledReason === "error") {
    status = "error";
  } else {
    status = "idle";
  }

  return { delta, done, status, send, cancel: stream.cancel, reset };
}
