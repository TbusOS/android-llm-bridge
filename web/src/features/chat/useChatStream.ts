/**
 * useChatStream — owns the WS /chat/ws connection and the turn list.
 *
 * One hook instance per Chat page mount. Exposes:
 *   - turns / sessionId / isStreaming
 *   - send / cancel / retry / reset
 *
 * Streaming model: fresh WebSocket per turn (chat is one
 * request/stream/done cycle). Auto-reconnect disabled. Race
 * protection lives in `lib/hooks/useWsChatStream`.
 *
 * AO-1 redesign: switched to the discriminated-union `WsChatSettled`
 * — turn side-effects branch on a single `switch (info.kind)` instead
 * of split callbacks. cancel() is one-shot via `stream.cancel` (the
 * hook's onSettled path will mark the turn).
 */
import { useCallback, useRef, useState } from "react";

import {
  useWsChatStream,
  type WsChatSettled,
} from "../../lib/hooks/useWsChatStream";
import type {
  ChatRequestPayload,
  ChatTurn,
  StreamEvent,
  ToolCallEntry,
} from "./types";

function newId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

interface UseChatStreamArgs {
  backend: string;
  model: string | null;
  tools?: boolean;
  maxTurns?: number;
}

export interface UseChatStreamResult {
  turns: ChatTurn[];
  sessionId: string | null;
  isStreaming: boolean;
  send: (message: string) => void;
  cancel: () => void;
  retry: () => void;
  reset: () => void;
}

/** Clamp reasonText (server-supplied close.reason) by code-points,
 *  not UTF-16 units — surrogate pairs aren't split. */
function clampReason(raw: string, max = 200): string {
  const codePoints = Array.from(raw);
  if (codePoints.length <= max) return raw;
  return codePoints.slice(0, max).join("") + "…";
}

export function useChatStream(args: UseChatStreamArgs): UseChatStreamResult {
  const { backend, model, tools = true, maxTurns = 8 } = args;

  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);

  // Mutable refs so handlers don't capture stale closures
  const activeAssistantIdRef = useRef<string | null>(null);
  const sessionIdRef = useRef<string | null>(null);

  const updateTurn = useCallback(
    (id: string, mut: (t: ChatTurn) => ChatTurn) => {
      setTurns((prev) => prev.map((t) => (t.id === id ? mut(t) : t)));
    },
    [],
  );

  const handleEvent = useCallback(
    (
      ev: StreamEvent,
      helpers: { markSettled: (info: WsChatSettled) => void },
    ): void => {
      const aid = activeAssistantIdRef.current;
      if (!aid) return;

      if (ev.type === "token") {
        updateTurn(aid, (t) => ({
          ...t,
          status: "streaming",
          content: t.content + ev.delta,
        }));
        return;
      }

      if (ev.type === "tool_call_start") {
        const entry: ToolCallEntry = {
          id: ev.id,
          name: ev.name,
          arguments: ev.arguments,
          status: "running",
          startedAt: Date.now(),
        };
        updateTurn(aid, (t) => ({
          ...t,
          status: "streaming",
          toolCalls: [...t.toolCalls, entry],
        }));
        return;
      }

      if (ev.type === "tool_call_end") {
        updateTurn(aid, (t) => ({
          ...t,
          toolCalls: t.toolCalls.map((tc) =>
            tc.id === ev.id
              ? { ...tc, result: ev.result, status: "done", endedAt: Date.now() }
              : tc,
          ),
        }));
        return;
      }

      if (ev.type === "done") {
        if (ev.session_id) {
          sessionIdRef.current = ev.session_id;
          setSessionId(ev.session_id);
        }
        updateTurn(aid, (t) => ({
          ...t,
          status: ev.ok ? "done" : "error",
          content: ev.ok ? ev.data ?? t.content : t.content,
          error: ev.error,
          artifacts: ev.artifacts ?? [],
          timingMs: ev.timing_ms,
          model: ev.model ?? t.model,
        }));
        activeAssistantIdRef.current = null;
        helpers.markSettled(
          ev.ok
            ? { kind: "done" }
            : { kind: "error", source: "server" },
        );
      }
    },
    [updateTurn],
  );

  const stream = useWsChatStream<ChatRequestPayload>({
    path: "/chat/ws",
    onJson: (raw, helpers) => handleEvent(raw as StreamEvent, helpers),
    onSettled: (info) => {
      // Single terminal dispatch via discriminated union switch.
      // server-source done/error already updated the turn via
      // handleEvent above (activeAssistant is null at this point).
      const aid = activeAssistantIdRef.current;

      switch (info.kind) {
        case "done":
          return;
        case "error":
          if (info.source === "server") return;
          if (!aid) return;
          activeAssistantIdRef.current = null;
          updateTurn(aid, (t) => ({
            ...t,
            status:
              t.status === "pending" || t.status === "streaming"
                ? "error"
                : t.status,
            error: t.error ?? {
              code: "CONNECTION_LOST",
              message: clampReason(
                info.reasonText ||
                  `socket closed (code ${info.code ?? "?"})`,
              ),
            },
          }));
          return;
        case "cancelled":
          if (!aid) return;
          activeAssistantIdRef.current = null;
          updateTurn(aid, (t) => ({
            ...t,
            status: "cancelled",
            error: { code: "CANCELLED", message: "已取消" },
          }));
          return;
      }
    },
  });

  const startStream = useCallback(
    (userPrompt: string) => {
      if (stream.phase === "streaming") return;

      const userTurn: ChatTurn = {
        id: newId(),
        role: "user",
        content: userPrompt,
        toolCalls: [],
        artifacts: [],
        status: "done",
      };
      const assistantTurn: ChatTurn = {
        id: newId(),
        role: "assistant",
        content: "",
        toolCalls: [],
        artifacts: [],
        status: "pending",
        sourcePrompt: userPrompt,
      };
      activeAssistantIdRef.current = assistantTurn.id;
      setTurns((prev) => [...prev, userTurn, assistantTurn]);

      const payload: ChatRequestPayload = {
        message: userPrompt,
        session_id: sessionIdRef.current,
        backend,
        model,
        tools,
        max_turns: maxTurns,
      };
      stream.start(payload);
    },
    [backend, model, tools, maxTurns, stream],
  );

  const send = useCallback(
    (message: string) => {
      const trimmed = message.trim();
      if (!trimmed) return;
      startStream(trimmed);
    },
    [startStream],
  );

  // cancel() is one-shot: the hook's onSettled callback (cause=
  // user-cancel) handles the turn-side updateTurn.
  const cancel = useCallback(() => {
    stream.cancel();
  }, [stream]);

  const retry = useCallback(() => {
    if (stream.phase === "streaming") return;
    for (let i = turns.length - 1; i >= 0; i--) {
      const t = turns[i];
      if (t && t.role === "user") {
        startStream(t.content);
        return;
      }
    }
  }, [turns, stream.phase, startStream]);

  const reset = useCallback(() => {
    activeAssistantIdRef.current = null;
    sessionIdRef.current = null;
    setTurns([]);
    setSessionId(null);
    stream.reset();
  }, [stream]);

  return {
    turns,
    sessionId,
    isStreaming: stream.phase === "streaming",
    send,
    cancel,
    retry,
    reset,
  };
}
