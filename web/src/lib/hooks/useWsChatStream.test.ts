/**
 * useWsChatStream protocol-level race / lifecycle specs.
 *
 * AO-1 rewrite: API moved from (phase + reason + cause) tri-enum to
 * single `settled: WsChatSettled` discriminated union. All assertions
 * now switch on `info.kind` and the explicit `source` for error.
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { WsClient, WsEvent } from "../ws";
import {
  useWsChatStream,
  type WsChatSettled,
} from "./useWsChatStream";

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

vi.mock("../ws", () => ({ connect: mockConnect }));

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

interface TestReq {
  prompt: string;
}

describe("useWsChatStream", () => {
  it("initial state is idle / settled null", () => {
    const { result } = renderHook(() =>
      useWsChatStream<TestReq>({ path: "/test/ws" }),
    );
    expect(result.current.phase).toBe("idle");
    expect(result.current.settled).toBeNull();
    expect(mockConnect).not.toHaveBeenCalled();
  });

  it("happy path: start → open sends req → markSettled({kind:'done'})", () => {
    const onSettled = vi.fn();
    const { result } = renderHook(() =>
      useWsChatStream<TestReq>({
        path: "/test/ws",
        onJson: (raw, { markSettled }) => {
          const msg = raw as { type?: string };
          if (msg.type === "done") markSettled({ kind: "done" });
        },
        onSettled,
      }),
    );

    act(() => result.current.start({ prompt: "hi" }));
    expect(result.current.phase).toBe("streaming");
    expect(mockConnect).toHaveBeenCalledWith("/test/ws", { noReconnect: true });

    act(() => emit(0, { kind: "open" }));
    expect(currentClients[0]?.sentMessages).toEqual([{ prompt: "hi" }]);

    act(() => emit(0, { kind: "json", data: { type: "token", delta: "Hi" } }));
    act(() => emit(0, { kind: "json", data: { type: "done", ok: true } }));

    expect(result.current.phase).toBe("settled");
    expect(result.current.settled).toEqual({ kind: "done" });
    expect(onSettled).toHaveBeenCalledTimes(1);
    expect(onSettled).toHaveBeenCalledWith({ kind: "done" });
    expect(currentClients[0]?.closed).toBe(true);
  });

  it("markSettled({kind:'error',source:'server'}) → settled with that variant", () => {
    const onSettled = vi.fn();
    const { result } = renderHook(() =>
      useWsChatStream<TestReq>({
        path: "/test/ws",
        onJson: (raw, { markSettled }) => {
          const msg = raw as { type?: string; ok?: boolean };
          if (msg.type === "done") {
            markSettled(
              msg.ok === false
                ? { kind: "error", source: "server" }
                : { kind: "done" },
            );
          }
        },
        onSettled,
      }),
    );

    act(() => result.current.start({ prompt: "hi" }));
    act(() => emit(0, { kind: "open" }));
    act(() => emit(0, { kind: "json", data: { type: "done", ok: false } }));

    expect(result.current.phase).toBe("settled");
    expect(result.current.settled).toEqual({ kind: "error", source: "server" });
    expect(onSettled).toHaveBeenCalledWith({ kind: "error", source: "server" });
  });

  it("ws close before markSettled → settled {kind:'error', source:'ws-close', code, reasonText}", () => {
    const onSettled = vi.fn();
    const { result } = renderHook(() =>
      useWsChatStream<TestReq>({ path: "/test/ws", onSettled }),
    );
    act(() => result.current.start({ prompt: "hi" }));
    act(() => emit(0, { kind: "open" }));
    act(() =>
      emit(0, { kind: "json", data: { type: "token", delta: "partial" } }),
    );

    act(() => emit(0, { kind: "close", code: 1006, reason: "network abend" }));

    expect(result.current.phase).toBe("settled");
    expect(result.current.settled).toEqual({
      kind: "error",
      source: "ws-close",
      code: 1006,
      reasonText: "network abend",
    });
    expect(onSettled).toHaveBeenCalledWith(result.current.settled);
  });

  it("ws error event before markSettled → settled {kind:'error', source:'ws-error'}", () => {
    const onSettled = vi.fn();
    const { result } = renderHook(() =>
      useWsChatStream<TestReq>({ path: "/test/ws", onSettled }),
    );
    act(() => result.current.start({ prompt: "hi" }));
    act(() => emit(0, { kind: "error", message: "browser error" }));

    expect(result.current.phase).toBe("settled");
    expect(result.current.settled).toEqual({
      kind: "error",
      source: "ws-error",
      reasonText: "browser error",
    });
  });

  it("cancel() → settled {kind:'cancelled'} + suppresses late close event", () => {
    const onSettled = vi.fn();
    const { result } = renderHook(() =>
      useWsChatStream<TestReq>({ path: "/test/ws", onSettled }),
    );
    act(() => result.current.start({ prompt: "hi" }));
    act(() => emit(0, { kind: "open" }));

    act(() => result.current.cancel());
    expect(result.current.phase).toBe("settled");
    expect(result.current.settled).toEqual({ kind: "cancelled" });
    expect(onSettled).toHaveBeenCalledTimes(1);
    expect(onSettled).toHaveBeenCalledWith({ kind: "cancelled" });
    expect(currentClients[0]?.closed).toBe(true);

    // late close event from the closed socket should NOT re-fire onSettled
    act(() => emit(0, { kind: "close", code: 1000, reason: "client cancelled" }));
    expect(onSettled).toHaveBeenCalledTimes(1);
    expect(result.current.settled).toEqual({ kind: "cancelled" });
  });

  it("close after settled (done) is a no-op · no second onSettled call", () => {
    const onSettled = vi.fn();
    const { result } = renderHook(() =>
      useWsChatStream<TestReq>({
        path: "/test/ws",
        onJson: (raw, { markSettled }) => {
          const msg = raw as { type?: string };
          if (msg.type === "done") markSettled({ kind: "done" });
        },
        onSettled,
      }),
    );
    act(() => result.current.start({ prompt: "hi" }));
    act(() => emit(0, { kind: "open" }));
    act(() => emit(0, { kind: "json", data: { type: "done" } }));
    expect(onSettled).toHaveBeenCalledTimes(1);

    // Server commonly sends close frame right after done — must not
    // re-fire onSettled or change settled value.
    act(() => emit(0, { kind: "close", code: 1000, reason: "" }));
    expect(onSettled).toHaveBeenCalledTimes(1);
    expect(result.current.settled).toEqual({ kind: "done" });
  });

  it("re-start during streaming · late close on old socket doesn't pollute new stream", () => {
    const onSettled = vi.fn();
    const { result } = renderHook(() =>
      useWsChatStream<TestReq>({ path: "/test/ws", onSettled }),
    );
    act(() => result.current.start({ prompt: "first" }));
    act(() => emit(0, { kind: "open" }));

    act(() => result.current.start({ prompt: "second" }));
    expect(mockConnect).toHaveBeenCalledTimes(2);
    expect(result.current.phase).toBe("streaming");

    // Old socket's late close event lands. Must not flip the new
    // stream to error or fire onSettled.
    act(() => emit(0, { kind: "close", code: 1006, reason: "stale" }));
    expect(result.current.phase).toBe("streaming");
    expect(onSettled).not.toHaveBeenCalled();

    // New stream completes normally.
    act(() => emit(1, { kind: "open" }));
    expect(currentClients[1]?.sentMessages).toEqual([{ prompt: "second" }]);
  });

  it("reset() during streaming tears down socket + returns to idle", () => {
    const { result } = renderHook(() =>
      useWsChatStream<TestReq>({ path: "/test/ws" }),
    );
    act(() => result.current.start({ prompt: "hi" }));
    act(() => emit(0, { kind: "open" }));

    act(() => result.current.reset());
    expect(result.current.phase).toBe("idle");
    expect(result.current.settled).toBeNull();
    expect(currentClients[0]?.closed).toBe(true);
  });

  it("reset() after settled clears settled / returns to idle without touching socket", () => {
    const { result } = renderHook(() =>
      useWsChatStream<TestReq>({
        path: "/test/ws",
        onJson: (raw, { markSettled }) => {
          const msg = raw as { type?: string };
          if (msg.type === "done") markSettled({ kind: "done" });
        },
      }),
    );
    act(() => result.current.start({ prompt: "hi" }));
    act(() => emit(0, { kind: "open" }));
    act(() => emit(0, { kind: "json", data: { type: "done" } }));
    expect(result.current.settled).toEqual({ kind: "done" });

    act(() => result.current.reset());
    expect(result.current.phase).toBe("idle");
    expect(result.current.settled).toBeNull();
  });

  it("cancel on idle is a no-op (no settled fire)", () => {
    const onSettled = vi.fn();
    const { result } = renderHook(() =>
      useWsChatStream<TestReq>({ path: "/test/ws", onSettled }),
    );
    act(() => result.current.cancel());
    expect(result.current.phase).toBe("idle");
    expect(result.current.settled).toBeNull();
    expect(onSettled).not.toHaveBeenCalled();
  });

  it("unmount during streaming · no late setState warn from a delivered close", () => {
    const onSettled = vi.fn();
    const { result, unmount } = renderHook(() =>
      useWsChatStream<TestReq>({ path: "/test/ws", onSettled }),
    );
    act(() => result.current.start({ prompt: "hi" }));
    act(() => emit(0, { kind: "open" }));

    unmount();
    expect(() => emit(0, { kind: "close", code: 1006, reason: "" })).not.toThrow();
    expect(onSettled).not.toHaveBeenCalled();
  });

  it("WsChatSettled discriminated union shape (compile-time + runtime)", () => {
    // Compile-time: every variant typed correctly.
    const variants: WsChatSettled[] = [
      { kind: "done" },
      { kind: "error", source: "server" },
      { kind: "error", source: "ws-close", code: 1006, reasonText: "x" },
      { kind: "error", source: "ws-error", reasonText: "browser error" },
      { kind: "cancelled" },
    ];
    expect(variants).toHaveLength(5);
    // Runtime: TS narrow on switch works as expected.
    for (const v of variants) {
      switch (v.kind) {
        case "done":
          expect(v).not.toHaveProperty("source");
          break;
        case "error":
          expect(v.source).toMatch(/^(server|ws-close|ws-error)$/);
          break;
        case "cancelled":
          expect(v).not.toHaveProperty("source");
          break;
      }
    }
  });
});
