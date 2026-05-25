/**
 * useWsChatStream — protocol-level race / lifecycle specs.
 *
 * These are extracted/distilled from usePlaygroundChat.test.ts after
 * AL-1 redesign, plus added cases the consumer spec couldn't reach
 * (helpers.markSettled / onSettled cause variants / re-start race
 * defence). Consumer-level specs (Playground / Chat) keep their own
 * end-to-end assertions on synthetic done payload + turn projection.
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { WsClient, WsEvent } from "../ws";
import {
  useWsChatStream,
  type OnJsonHelpers,
  type WsChatSettledInfo,
} from "./useWsChatStream";

// Hoisted mock — vitest re-runs the factory per spec.
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
  it("initial state is idle / settledReason null", () => {
    const { result } = renderHook(() =>
      useWsChatStream<TestReq>({ path: "/test/ws" }),
    );
    expect(result.current.phase).toBe("idle");
    expect(result.current.settledReason).toBeNull();
    expect(mockConnect).not.toHaveBeenCalled();
  });

  it("happy path: start → open sends req → onJson markSettled done", () => {
    const onJson = vi.fn();
    const onSettled = vi.fn();
    const { result } = renderHook(() =>
      useWsChatStream<TestReq>({
        path: "/test/ws",
        onJson: (raw, helpers) => {
          onJson(raw);
          const msg = raw as { type?: string };
          if (msg.type === "done") helpers.markSettled("done");
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
    expect(result.current.settledReason).toBe("done");
    expect(onSettled).toHaveBeenCalledTimes(1);
    expect(onSettled).toHaveBeenCalledWith(
      expect.objectContaining({ reason: "done", cause: "server-done" }),
    );
    expect(currentClients[0]?.closed).toBe(true);
  });

  it("markSettled('error') sets phase=settled / reason=error", () => {
    const onSettled = vi.fn();
    const { result } = renderHook(() =>
      useWsChatStream<TestReq>({
        path: "/test/ws",
        onJson: (raw, helpers: OnJsonHelpers) => {
          const msg = raw as { type?: string; ok?: boolean };
          if (msg.type === "done") {
            helpers.markSettled(msg.ok === false ? "error" : "done");
          }
        },
        onSettled,
      }),
    );

    act(() => result.current.start({ prompt: "hi" }));
    act(() => emit(0, { kind: "open" }));
    act(() => emit(0, { kind: "json", data: { type: "done", ok: false } }));

    expect(result.current.phase).toBe("settled");
    expect(result.current.settledReason).toBe("error");
    expect(onSettled).toHaveBeenCalledWith(
      expect.objectContaining({ reason: "error", cause: "server-done" }),
    );
  });

  it("ws close before markSettled → settled with cause=ws-close + reasonText/code", () => {
    const onSettled = vi.fn();
    const { result } = renderHook(() =>
      useWsChatStream<TestReq>({ path: "/test/ws", onSettled }),
    );
    act(() => result.current.start({ prompt: "hi" }));
    act(() => emit(0, { kind: "open" }));
    act(() => emit(0, { kind: "json", data: { type: "token", delta: "partial" } }));

    act(() => emit(0, { kind: "close", code: 1006, reason: "network abend" }));

    expect(result.current.phase).toBe("settled");
    expect(result.current.settledReason).toBe("error");
    expect(onSettled).toHaveBeenCalledWith({
      reason: "error",
      cause: "ws-close",
      code: 1006,
      reasonText: "network abend",
    });
  });

  it("ws error event before markSettled → settled with cause=ws-error", () => {
    const onSettled = vi.fn();
    const { result } = renderHook(() =>
      useWsChatStream<TestReq>({ path: "/test/ws", onSettled }),
    );
    act(() => result.current.start({ prompt: "hi" }));
    act(() => emit(0, { kind: "error", message: "browser error" }));

    expect(result.current.phase).toBe("settled");
    expect(result.current.settledReason).toBe("error");
    expect(onSettled).toHaveBeenCalledWith(
      expect.objectContaining({ reason: "error", cause: "ws-error" }),
    );
  });

  it("cancel() → settled with cause=user-cancel + suppresses late close", () => {
    const onSettled = vi.fn();
    const { result } = renderHook(() =>
      useWsChatStream<TestReq>({ path: "/test/ws", onSettled }),
    );
    act(() => result.current.start({ prompt: "hi" }));
    act(() => emit(0, { kind: "open" }));

    act(() => result.current.cancel());
    expect(result.current.phase).toBe("settled");
    expect(result.current.settledReason).toBe("cancelled");
    expect(onSettled).toHaveBeenCalledTimes(1);
    expect(onSettled).toHaveBeenCalledWith({
      reason: "cancelled",
      cause: "user-cancel",
    });
    expect(currentClients[0]?.closed).toBe(true);

    // late close event from the closed socket should NOT re-fire onSettled
    act(() => emit(0, { kind: "close", code: 1000, reason: "client cancelled" }));
    expect(onSettled).toHaveBeenCalledTimes(1);
    expect(result.current.settledReason).toBe("cancelled");
  });

  it("close after settled (done) is a no-op · no second onSettled call", () => {
    const onSettled = vi.fn();
    const { result } = renderHook(() =>
      useWsChatStream<TestReq>({
        path: "/test/ws",
        onJson: (raw, helpers) => {
          const msg = raw as { type?: string };
          if (msg.type === "done") helpers.markSettled("done");
        },
        onSettled,
      }),
    );
    act(() => result.current.start({ prompt: "hi" }));
    act(() => emit(0, { kind: "open" }));
    act(() => emit(0, { kind: "json", data: { type: "done" } }));
    expect(onSettled).toHaveBeenCalledTimes(1);

    // Server commonly sends close frame right after done — must not
    // re-fire onSettled or change reason.
    act(() => emit(0, { kind: "close", code: 1000, reason: "" }));
    expect(onSettled).toHaveBeenCalledTimes(1);
    expect(result.current.settledReason).toBe("done");
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
    expect(result.current.settledReason).toBeNull();
    expect(currentClients[0]?.closed).toBe(true);
  });

  it("reset() after settled clears reason / returns to idle without touching socket", () => {
    const { result } = renderHook(() =>
      useWsChatStream<TestReq>({
        path: "/test/ws",
        onJson: (raw, helpers) => {
          const msg = raw as { type?: string };
          if (msg.type === "done") helpers.markSettled("done");
        },
      }),
    );
    act(() => result.current.start({ prompt: "hi" }));
    act(() => emit(0, { kind: "open" }));
    act(() => emit(0, { kind: "json", data: { type: "done" } }));
    expect(result.current.settledReason).toBe("done");

    act(() => result.current.reset());
    expect(result.current.phase).toBe("idle");
    expect(result.current.settledReason).toBeNull();
  });

  it("cancel on idle is a no-op (no settled fire)", () => {
    const onSettled = vi.fn();
    const { result } = renderHook(() =>
      useWsChatStream<TestReq>({ path: "/test/ws", onSettled }),
    );
    act(() => result.current.cancel());
    expect(result.current.phase).toBe("idle");
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
    // The cleanup effect sets cancelledRef so a late close should not
    // fire onSettled (it would also setState on an unmounted hook).
    expect(() => emit(0, { kind: "close", code: 1006, reason: "" })).not.toThrow();
    expect(onSettled).not.toHaveBeenCalled();
  });

  it("onSettled info object shape · all 4 cause variants covered", () => {
    // sanity: TS surface stays narrow. This is more a compile-time
    // assertion than a runtime check.
    const _shape: WsChatSettledInfo[] = [
      { reason: "done", cause: "server-done" },
      { reason: "error", cause: "server-done" },
      { reason: "error", cause: "ws-close", code: 1006, reasonText: "x" },
      { reason: "error", cause: "ws-error", reasonText: "x" },
      { reason: "cancelled", cause: "user-cancel" },
    ];
    expect(_shape).toHaveLength(5);
  });
});
