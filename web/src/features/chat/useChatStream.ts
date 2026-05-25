/**
 * useChatStream — owns the WS /chat/ws connection and the turn list.
 *
 * One hook instance per Chat page mount. The hook exposes:
 *   - turns:       the rendered conversation
 *   - sessionId:   server-assigned session identifier (persisted in store)
 *   - isStreaming: true while a turn is in flight
 *   - send(text):  start a new turn (no-op while streaming)
 *   - cancel():    close the active WS, mark current turn cancelled
 *   - retry():     re-send the most recent user prompt
 *   - reset():     clear local state (does not delete the server session)
 *
 * Streaming model: we open a fresh WebSocket per turn (chat is a
 * request/stream/done cycle). Auto-reconnect is disabled — a closed
 * socket means "this turn is over". The lifecycle (open / settled /
 * close-before-settled / cancel race suppression) lives in
 * `lib/hooks/useWsChatStream`; this hook only owns turn-list
 * projection.
 *
 * AL-1 redesign: uses the unified `onSettled` callback (replaces the
 * pre-existing `onCloseBeforeDone` split). Turn state side-effects on
 * settle (mark error on ws-close / mark cancelled on user-cancel)
 * live in this one callback, no duplication with cancel() body.
 */
import { useCallback, useRef, useState } from "react";

import { useWsChatStream } from "../../lib/hooks/useWsChatStream";
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
      helpers: { markSettled: (reason: "done" | "error") => void },
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
        helpers.markSettled(ev.ok ? "done" : "error");
      }
    },
    [updateTurn],
  );

  const stream = useWsChatStream<ChatRequestPayload>({
    path: "/chat/ws",
    onJson: (raw, helpers) => handleEvent(raw as StreamEvent, helpers),
    onSettled: (info) => {
      // Single terminal entry point — handles every reason. Server-done
      // already updated the turn via handleEvent above (activeAssistant
      // is null), so we no-op for reason="done"/"error" with cause=
      // "server-done". ws-close / ws-error / user-cancel still have a
      // turn in flight — write the appropriate terminal status.
      if (info.cause === "server-done") return;

      const aid = activeAssistantIdRef.current;
      if (!aid) return;
      activeAssistantIdRef.current = null;

      if (info.cause === "user-cancel") {
        updateTurn(aid, (t) => ({
          ...t,
          status: "cancelled",
          error: { code: "CANCELLED", message: "已取消" },
        }));
        return;
      }

      // ws-close / ws-error → CONNECTION_LOST.
      // sec MID-13: clamp server-supplied reason at 200 chars so
      // hostile / over-long reason text doesn't blow up turn UI.
      const rawReason =
        info.reasonText || `socket closed (code ${info.code ?? "?"})`;
      const safeReason =
        rawReason.length > 200 ? `${rawReason.slice(0, 200)}…` : rawReason;
      updateTurn(aid, (t) => ({
        ...t,
        status:
          t.status === "pending" || t.status === "streaming"
            ? "error"
            : t.status,
        error: t.error ?? {
          code: "CONNECTION_LOST",
          message: safeReason,
        },
      }));
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

  // cancel() is single-call now: onSettled callback in the hook will
  // be invoked with cause="user-cancel" and the turn-side updateTurn
  // happens there. No need for two-step (turn mark + stream.cancel)
  // dance the previous version had.
  const cancel = useCallback(() => {
    stream.cancel();
  }, [stream]);

  const retry = useCallback(() => {
    if (stream.phase === "streaming") return;
    // Find the most recent user turn — it carries the prompt to retry
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
