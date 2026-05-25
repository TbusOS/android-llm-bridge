/**
 * usePlaygroundChat regression specs.
 *
 * Targets the close-before-done race that bit us in commit Y (audit
 * code-r HIGH#2 + perf MID#3): the previous `if (status ===
 * "streaming") setStatus("error")` read a stale closure of `status`,
 * so WS close before `done` arrived never set the error state.
 *
 * We mock the entire `lib/ws` module so specs can drive the WS event
 * timeline deterministically — no real socket, no jsdom WebSocket
 * implementation to fight.
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { WsClient, WsEvent } from "../../lib/ws";

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

vi.mock("../../lib/ws", () => ({
  connect: mockConnect,
}));

// Import AFTER vi.mock so the hook sees the mocked connect().
import { usePlaygroundChat } from "./usePlaygroundChat";

function emit(idx: number, ev: WsEvent) {
  const entry = currentClients[idx];
  if (!entry) throw new Error(`no client at index ${idx}`);
  // Copy to array — listeners may mutate the set during iteration.
  for (const l of Array.from(entry.listeners)) l(ev);
}

beforeEach(() => {
  mockConnect.mockClear();
  currentClients.length = 0;
});

afterEach(() => {
  currentClients.length = 0;
});

describe("usePlaygroundChat", () => {
  it("initial state is idle / empty delta / no done", () => {
    const { result } = renderHook(() => usePlaygroundChat());
    expect(result.current.status).toBe("idle");
    expect(result.current.delta).toBe("");
    expect(result.current.done).toBeNull();
    expect(mockConnect).not.toHaveBeenCalled();
  });

  it("happy path: send → open → 3 tokens → done sets status=done and merges delta", () => {
    const { result } = renderHook(() => usePlaygroundChat());

    act(() => {
      result.current.send({
        backend: "ollama",
        model: "qwen2.5",
        messages: [{ role: "user", content: "hi" }],
      });
    });
    expect(result.current.status).toBe("streaming");
    expect(mockConnect).toHaveBeenCalledWith("/playground/chat/ws", {
      noReconnect: true,
    });

    act(() => emit(0, { kind: "open" }));
    expect(currentClients[0]?.sentMessages).toHaveLength(1);

    act(() => emit(0, { kind: "json", data: { type: "token", delta: "Hel" } }));
    act(() => emit(0, { kind: "json", data: { type: "token", delta: "lo" } }));
    act(() => emit(0, { kind: "json", data: { type: "token", delta: "!" } }));
    expect(result.current.delta).toBe("Hello!");
    expect(result.current.status).toBe("streaming");

    act(() =>
      emit(0, {
        kind: "json",
        data: {
          type: "done",
          ok: true,
          content: "Hello!",
          finish_reason: "stop",
          model: "qwen2.5",
          backend: "ollama",
          metrics: { tokens_per_s: 42 },
        },
      }),
    );

    expect(result.current.status).toBe("done");
    expect(result.current.done?.ok).toBe(true);
    expect(result.current.done?.content).toBe("Hello!");
    expect(currentClients[0]?.closed).toBe(true);
  });

  it("close-before-done sets status=error (regression: commit Y stale-closure fix)", () => {
    const { result } = renderHook(() => usePlaygroundChat());

    act(() => {
      result.current.send({
        backend: "ollama",
        model: null,
        messages: [{ role: "user", content: "hi" }],
      });
    });
    expect(result.current.status).toBe("streaming");

    // open + 1 token, then unexpected close. The previous buggy code
    // (status closure captured at send time = "idle") would silently
    // leave status="streaming" forever.
    act(() => emit(0, { kind: "open" }));
    act(() => emit(0, { kind: "json", data: { type: "token", delta: "Hel" } }));
    expect(result.current.delta).toBe("Hel");

    act(() => emit(0, { kind: "close", code: 1006, reason: "abnormal" }));
    expect(result.current.status).toBe("error");
  });

  it("error event before done also sets status=error", () => {
    const { result } = renderHook(() => usePlaygroundChat());

    act(() => {
      result.current.send({
        backend: "ollama",
        model: null,
        messages: [{ role: "user", content: "hi" }],
      });
    });
    act(() => emit(0, { kind: "error", message: "boom" }));
    expect(result.current.status).toBe("error");
  });

  it("close AFTER done is a no-op (don't overwrite done with error)", () => {
    const { result } = renderHook(() => usePlaygroundChat());

    act(() =>
      result.current.send({
        backend: "ollama",
        model: null,
        messages: [{ role: "user", content: "hi" }],
      }),
    );
    act(() => emit(0, { kind: "open" }));
    act(() =>
      emit(0, {
        kind: "json",
        data: {
          type: "done",
          ok: true,
          content: "",
          finish_reason: "stop",
          model: "",
          backend: "ollama",
        },
      }),
    );
    expect(result.current.status).toBe("done");

    // Server might send a close frame immediately after done — that's
    // the natural protocol end. We must NOT flip status back to error.
    act(() => emit(0, { kind: "close", code: 1000, reason: "" }));
    expect(result.current.status).toBe("done");
  });

  it("server done.ok=false flips status to error", () => {
    const { result } = renderHook(() => usePlaygroundChat());
    act(() =>
      result.current.send({
        backend: "ollama",
        model: null,
        messages: [{ role: "user", content: "hi" }],
      }),
    );
    act(() => emit(0, { kind: "open" }));
    act(() =>
      emit(0, {
        kind: "json",
        data: {
          type: "done",
          ok: false,
          content: "",
          finish_reason: "error",
          model: "",
          backend: "ollama",
          error: { code: "BACKEND_DOWN", message: "ollama unreachable" },
        },
      }),
    );
    expect(result.current.status).toBe("error");
    expect(result.current.done?.error?.code).toBe("BACKEND_DOWN");
  });

  it("send after done starts a fresh connection + resets delta/done", () => {
    const { result } = renderHook(() => usePlaygroundChat());

    act(() =>
      result.current.send({
        backend: "ollama",
        model: null,
        messages: [{ role: "user", content: "first" }],
      }),
    );
    act(() => emit(0, { kind: "open" }));
    act(() =>
      emit(0, { kind: "json", data: { type: "token", delta: "first reply" } }),
    );
    act(() =>
      emit(0, {
        kind: "json",
        data: {
          type: "done",
          ok: true,
          content: "first reply",
          finish_reason: "stop",
          model: "",
          backend: "ollama",
        },
      }),
    );

    act(() => result.current.reset());
    expect(result.current.status).toBe("idle");
    expect(result.current.delta).toBe("");
    expect(result.current.done).toBeNull();

    act(() =>
      result.current.send({
        backend: "ollama",
        model: null,
        messages: [{ role: "user", content: "second" }],
      }),
    );
    // A second connect() should fire.
    expect(mockConnect).toHaveBeenCalledTimes(2);
    expect(result.current.status).toBe("streaming");
  });

  it("cancel during streaming closes the socket and returns to idle", () => {
    const { result } = renderHook(() => usePlaygroundChat());

    act(() =>
      result.current.send({
        backend: "ollama",
        model: null,
        messages: [{ role: "user", content: "hi" }],
      }),
    );
    act(() => emit(0, { kind: "open" }));

    act(() => result.current.cancel());
    expect(currentClients[0]?.closed).toBe(true);
    expect(result.current.status).toBe("idle");
  });

  it("close-before-done injects a synthetic done payload (H1 fix)", () => {
    // Regression: before AH-3, close-before-done set status="error" but
    // left `done` null — PlaygroundPage's error block ("status=error
    // && done") then never rendered, silently freezing the assistant
    // bubble. The hook now writes a synthetic done with
    // code=WS_DISCONNECTED so the UI surfaces the disconnect.
    const { result } = renderHook(() => usePlaygroundChat());

    act(() =>
      result.current.send({
        backend: "ollama",
        model: null,
        messages: [{ role: "user", content: "hi" }],
      }),
    );
    act(() => emit(0, { kind: "open" }));
    act(() =>
      emit(0, { kind: "json", data: { type: "token", delta: "partial" } }),
    );

    act(() =>
      emit(0, { kind: "close", code: 1006, reason: "abnormal closure" }),
    );

    expect(result.current.status).toBe("error");
    expect(result.current.done).not.toBeNull();
    expect(result.current.done?.ok).toBe(false);
    expect(result.current.done?.error?.code).toBe("WS_DISCONNECTED");
    expect(result.current.done?.error?.message).toContain("abnormal closure");
    expect(result.current.done?.finish_reason).toBe("disconnected");
  });

  it("cancel-then-late-close does NOT flip status back to error (H1 race fix)", () => {
    // Regression target: pre-AH-3 the cancel() path closed the socket
    // and set status="idle", but the same subscriber lived on; the
    // browser's async close event then fired and the "close-before-
    // done" branch ran, overwriting idle → error. UI flickered idle
    // for one tick before reverting to a false error state.
    const { result } = renderHook(() => usePlaygroundChat());

    act(() =>
      result.current.send({
        backend: "ollama",
        model: null,
        messages: [{ role: "user", content: "hi" }],
      }),
    );
    act(() => emit(0, { kind: "open" }));
    act(() =>
      emit(0, { kind: "json", data: { type: "token", delta: "partial" } }),
    );

    act(() => result.current.cancel());
    expect(result.current.status).toBe("idle");
    expect(currentClients[0]?.closed).toBe(true);

    // The browser will deliver the close event AFTER cancel(). Without
    // the cancelledRef suppression in useWsChatStream, this would
    // trigger onCloseBeforeDone and flip status → error.
    act(() =>
      emit(0, { kind: "close", code: 1000, reason: "client cancelled" }),
    );

    expect(result.current.status).toBe("idle");
    expect(result.current.done).toBeNull();
  });

  it("re-send during streaming: late close on the old socket does not pollute the new stream", () => {
    // Regression target: send() while the previous stream is still
    // in flight tears down the old socket. Its close event still
    // lands async. Pre-AH-3 the old subscriber would fire the
    // close-before-done branch on the NEW stream's state, flipping
    // its status to error mid-stream.
    const { result } = renderHook(() => usePlaygroundChat());

    act(() =>
      result.current.send({
        backend: "ollama",
        model: null,
        messages: [{ role: "user", content: "first" }],
      }),
    );
    act(() => emit(0, { kind: "open" }));
    act(() =>
      emit(0, { kind: "json", data: { type: "token", delta: "first-1" } }),
    );

    // User fires a second send before first completes.
    act(() =>
      result.current.send({
        backend: "ollama",
        model: null,
        messages: [{ role: "user", content: "second" }],
      }),
    );

    expect(mockConnect).toHaveBeenCalledTimes(2);
    expect(result.current.status).toBe("streaming");
    expect(result.current.delta).toBe(""); // reset for new send

    // Old socket's late close event lands. The hook must NOT flip
    // status to error on the NEW stream.
    act(() =>
      emit(0, { kind: "close", code: 1006, reason: "stale socket" }),
    );
    expect(result.current.status).toBe("streaming");

    // The new stream still works normally.
    act(() => emit(1, { kind: "open" }));
    act(() =>
      emit(1, { kind: "json", data: { type: "token", delta: "second-reply" } }),
    );
    expect(result.current.delta).toBe("second-reply");
  });
});
