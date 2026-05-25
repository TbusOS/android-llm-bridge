/**
 * useChatStream — turn-projection specs.
 *
 * The lifecycle race / settlement contract lives in
 * useWsChatStream.test.ts (protocol layer). This file focuses on the
 * Chat-specific projection: token append / tool_call_start+end /
 * done with session_id / close-before-done turn marking / cancel
 * turn marking.
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { WsClient, WsEvent } from "../../lib/ws";
import { useChatStream } from "./useChatStream";

const { mockConnect, currentClients } = vi.hoisted(() => {
  const currentClients: Array<{
    listeners: Set<(ev: WsEvent) => void>;
    sentMessages: unknown[];
    closed: boolean;
  }> = [];
  const mockConnect = vi.fn((_path: string): WsClient => {
    const entry = {
      listeners: new Set<(ev: WsEvent) => void>(),
      sentMessages: [] as unknown[],
      closed: false,
    };
    currentClients.push(entry);
    return {
      send(data) {
        entry.sentMessages.push(data);
      },
      close() {
        entry.closed = true;
      },
      subscribe(listener) {
        entry.listeners.add(listener);
        return () => entry.listeners.delete(listener);
      },
      get readyState() {
        return entry.closed ? 3 : 1;
      },
    };
  });
  return { mockConnect, currentClients };
});

vi.mock("../../lib/ws", () => ({ connect: mockConnect }));

function emit(idx: number, ev: WsEvent) {
  const entry = currentClients[idx];
  if (!entry) throw new Error(`no client at index ${idx}`);
  for (const l of Array.from(entry.listeners)) l(ev);
}

beforeEach(() => {
  mockConnect.mockClear();
  currentClients.length = 0;
});

afterEach(() => {
  currentClients.length = 0;
});

describe("useChatStream", () => {
  it("initial state · empty turns · not streaming", () => {
    const { result } = renderHook(() =>
      useChatStream({ backend: "ollama", model: "qwen2.5" }),
    );
    expect(result.current.turns).toEqual([]);
    expect(result.current.isStreaming).toBe(false);
    expect(result.current.sessionId).toBeNull();
  });

  it("send → 2 turns (user + assistant placeholder) → isStreaming=true", () => {
    const { result } = renderHook(() =>
      useChatStream({ backend: "ollama", model: "qwen2.5" }),
    );
    act(() => result.current.send("hi"));
    expect(result.current.turns).toHaveLength(2);
    expect(result.current.turns[0]?.role).toBe("user");
    expect(result.current.turns[0]?.content).toBe("hi");
    expect(result.current.turns[1]?.role).toBe("assistant");
    expect(result.current.turns[1]?.status).toBe("pending");
    expect(result.current.isStreaming).toBe(true);
    expect(mockConnect).toHaveBeenCalledWith("/chat/ws", { noReconnect: true });
  });

  it("token events append to assistant content + status → streaming", () => {
    const { result } = renderHook(() =>
      useChatStream({ backend: "ollama", model: null }),
    );
    act(() => result.current.send("hi"));
    act(() => emit(0, { kind: "open" }));
    act(() => emit(0, { kind: "json", data: { type: "token", delta: "Hel" } }));
    act(() => emit(0, { kind: "json", data: { type: "token", delta: "lo" } }));

    const ass = result.current.turns[1];
    expect(ass?.content).toBe("Hello");
    expect(ass?.status).toBe("streaming");
  });

  it("tool_call_start adds entry · tool_call_end marks done with result", () => {
    const { result } = renderHook(() =>
      useChatStream({ backend: "ollama", model: null, tools: true }),
    );
    act(() => result.current.send("call a tool"));
    act(() => emit(0, { kind: "open" }));
    act(() =>
      emit(0, {
        kind: "json",
        data: {
          type: "tool_call_start",
          id: "tc-1",
          name: "alb_status",
          arguments: { foo: 1 },
        },
      }),
    );

    let ass = result.current.turns[1];
    expect(ass?.toolCalls).toHaveLength(1);
    expect(ass?.toolCalls[0]).toMatchObject({
      id: "tc-1",
      name: "alb_status",
      status: "running",
    });

    act(() =>
      emit(0, {
        kind: "json",
        data: {
          type: "tool_call_end",
          id: "tc-1",
          name: "alb_status",
          result: { ok: true },
        },
      }),
    );

    ass = result.current.turns[1];
    expect(ass?.toolCalls[0]?.status).toBe("done");
    expect(ass?.toolCalls[0]?.result).toEqual({ ok: true });
  });

  it("done(ok=true, session_id) → turn done + sessionId set + isStreaming=false", () => {
    const { result } = renderHook(() =>
      useChatStream({ backend: "ollama", model: "qwen" }),
    );
    act(() => result.current.send("hi"));
    act(() => emit(0, { kind: "open" }));
    act(() => emit(0, { kind: "json", data: { type: "token", delta: "Hi" } }));
    act(() =>
      emit(0, {
        kind: "json",
        data: {
          type: "done",
          ok: true,
          data: "Hi there",
          session_id: "sess-xyz",
          model: "qwen2.5",
          timing_ms: 1234,
        },
      }),
    );

    expect(result.current.isStreaming).toBe(false);
    expect(result.current.sessionId).toBe("sess-xyz");
    const ass = result.current.turns[1];
    expect(ass?.status).toBe("done");
    expect(ass?.content).toBe("Hi there"); // server-supplied final overrides streamed delta
    expect(ass?.timingMs).toBe(1234);
    expect(ass?.model).toBe("qwen2.5");
  });

  it("done(ok=false, error) → turn error + isStreaming=false", () => {
    const { result } = renderHook(() =>
      useChatStream({ backend: "ollama", model: null }),
    );
    act(() => result.current.send("hi"));
    act(() => emit(0, { kind: "open" }));
    act(() =>
      emit(0, {
        kind: "json",
        data: {
          type: "done",
          ok: false,
          error: { code: "BACKEND_DOWN", message: "ollama unreachable" },
        },
      }),
    );

    expect(result.current.isStreaming).toBe(false);
    const ass = result.current.turns[1];
    expect(ass?.status).toBe("error");
    expect(ass?.error).toMatchObject({ code: "BACKEND_DOWN" });
  });

  it("close-before-done marks turn CONNECTION_LOST (regression for AL-1 onSettled path)", () => {
    const { result } = renderHook(() =>
      useChatStream({ backend: "ollama", model: null }),
    );
    act(() => result.current.send("hi"));
    act(() => emit(0, { kind: "open" }));
    act(() =>
      emit(0, { kind: "json", data: { type: "token", delta: "partial" } }),
    );

    act(() => emit(0, { kind: "close", code: 1006, reason: "abnormal" }));

    expect(result.current.isStreaming).toBe(false);
    const ass = result.current.turns[1];
    expect(ass?.status).toBe("error");
    expect(ass?.error?.code).toBe("CONNECTION_LOST");
    expect(ass?.error?.message).toContain("abnormal");
  });

  it("cancel during streaming marks turn CANCELLED (regression for AL-1 onSettled path)", () => {
    const { result } = renderHook(() =>
      useChatStream({ backend: "ollama", model: null }),
    );
    act(() => result.current.send("hi"));
    act(() => emit(0, { kind: "open" }));
    act(() =>
      emit(0, { kind: "json", data: { type: "token", delta: "partial" } }),
    );

    act(() => result.current.cancel());

    expect(result.current.isStreaming).toBe(false);
    const ass = result.current.turns[1];
    expect(ass?.status).toBe("cancelled");
    expect(ass?.error?.code).toBe("CANCELLED");
    expect(currentClients[0]?.closed).toBe(true);
  });

  it("session_id from first done is sent as session_id in second send", () => {
    const { result } = renderHook(() =>
      useChatStream({ backend: "ollama", model: null }),
    );
    act(() => result.current.send("first"));
    act(() => emit(0, { kind: "open" }));
    act(() =>
      emit(0, {
        kind: "json",
        data: { type: "done", ok: true, session_id: "sess-1" },
      }),
    );

    act(() => result.current.send("second"));
    expect(mockConnect).toHaveBeenCalledTimes(2);
    // payload is sent after the WS opens; emit `open` to flush it.
    act(() => emit(1, { kind: "open" }));
    expect(currentClients[1]?.sentMessages[0]).toMatchObject({
      message: "second",
      session_id: "sess-1",
    });
  });

  it("retry resends the last user prompt", () => {
    const { result } = renderHook(() =>
      useChatStream({ backend: "ollama", model: null }),
    );
    act(() => result.current.send("the prompt"));
    act(() => emit(0, { kind: "open" }));
    act(() =>
      emit(0, { kind: "json", data: { type: "done", ok: false, error: { code: "X", message: "y" } } }),
    );

    act(() => result.current.retry());
    expect(mockConnect).toHaveBeenCalledTimes(2);
    act(() => emit(1, { kind: "open" }));
    expect(currentClients[1]?.sentMessages[0]).toMatchObject({
      message: "the prompt",
    });
  });

  it("reset clears turns + sessionId + tears down any live socket", () => {
    const { result } = renderHook(() =>
      useChatStream({ backend: "ollama", model: null }),
    );
    act(() => result.current.send("hi"));
    act(() => emit(0, { kind: "open" }));

    act(() => result.current.reset());
    expect(result.current.turns).toEqual([]);
    expect(result.current.sessionId).toBeNull();
    expect(result.current.isStreaming).toBe(false);
    expect(currentClients[0]?.closed).toBe(true);
  });
});
