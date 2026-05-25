/**
 * usePlaygroundChat — WS streaming chat for the Playground page.
 *
 * Thin wrapper around `useWsChatStream`. Owns Playground-specific
 * payload shapes (delta buffer + DoneEvent), maps the underlying
 * `phase + settled` machine to the page's 4-state surface.
 *
 * H1 fix preserved: ws-close / ws-error paths write a synthetic
 * `done` with code=WS_DISCONNECTED so the
 * `status === "error" && done` UI block always renders something.
 *
 * AO-1 redesign: the prior version had 4 if/else mapping `phase` +
 * `cause` to Status; now `switch (stream.settled?.kind)` covers all
 * paths in one place.
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

/** Clamp reasonText to a sane length. WebSocket spec limits close
 *  reason to 123 bytes but custom clients / proxies can produce
 *  arbitrary content; trim before showing in UI to avoid layout
 *  blow-up. Uses Array.from to slice on code-points so surrogate
 *  pairs aren't split (sec MID-13 + code LOW). */
function clampReason(raw: string, max = 200): string {
  const codePoints = Array.from(raw);
  if (codePoints.length <= max) return raw;
  return codePoints.slice(0, max).join("") + "…";
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
        // Server-source done — pass the full discriminated variant so
        // onSettled below correctly skips the synthetic-done branch.
        markSettled(
          msg.ok === false
            ? { kind: "error", source: "server" }
            : { kind: "done" },
        );
      }
    },
    onSettled: (info) => {
      // server-done already wrote real done via onJson. cancelled
      // folds to idle externally (no synthetic needed). Only ws-close
      // / ws-error need a synthetic done so the error UI renders.
      if (info.kind === "error" && info.source !== "server") {
        const reasonText = clampReason(
          info.reasonText || `WebSocket closed (code ${info.code ?? "?"})`,
        );
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

  // Map the 3-phase hook + settled discriminated union to the page's
  // 4-state surface. cancelled folds to idle (PlaygroundPage doesn't
  // distinguish — it just wants the input usable again).
  let status: Status;
  if (stream.phase === "streaming") {
    status = "streaming";
  } else if (stream.phase === "settled" && stream.settled) {
    switch (stream.settled.kind) {
      case "done":
        status = "done";
        break;
      case "error":
        status = "error";
        break;
      case "cancelled":
        status = "idle";
        break;
    }
  } else {
    status = "idle";
  }

  return {
    delta,
    done,
    status,
    /** Exposed for PlaygroundPage's partial-delta stash logic
     *  (cancel pushes chat.delta into log). Keep in sync with
     *  stream.settled.kind. */
    settled: stream.settled,
    send,
    cancel: stream.cancel,
    reset,
  };
}
