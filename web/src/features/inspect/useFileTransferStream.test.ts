/**
 * useFileTransferStream protocol / lifecycle specs (CR-6).
 *
 * Mocks the global WebSocket so server frames can be driven
 * synchronously. Locks:
 *   (a) progress / closed(done|error|sensitive_path|cancelled) frame
 *       → state mapping, plus browser-level error / bare close;
 *   (b) the `inflight` exposure — set by start(), cleared on every
 *       terminal state — that FilesTab uses for direction-specific
 *       busy labels.
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  useFileTransferStream,
  type StartArgs,
} from "./useFileTransferStream";

vi.mock("../../lib/ws", () => ({
  wsUrl: (path: string) => `ws://test${path}`,
}));

type Listener = (ev: { data?: unknown }) => void;

class MockWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  static instances: MockWebSocket[] = [];

  url: string;
  readyState = MockWebSocket.CONNECTING;
  sent: string[] = [];
  private listeners = new Map<string, Set<Listener>>();

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  addEventListener(type: string, fn: Listener) {
    let set = this.listeners.get(type);
    if (!set) {
      set = new Set();
      this.listeners.set(type, set);
    }
    set.add(fn);
  }

  removeEventListener(type: string, fn: Listener) {
    this.listeners.get(type)?.delete(fn);
  }

  send(data: string) {
    this.sent.push(data);
  }

  close() {
    this.readyState = MockWebSocket.CLOSED;
  }

  emit(type: string, ev: { data?: unknown } = {}) {
    for (const fn of Array.from(this.listeners.get(type) ?? [])) fn(ev);
  }

  open() {
    this.readyState = MockWebSocket.OPEN;
    this.emit("open");
  }

  message(frame: Record<string, unknown>) {
    this.emit("message", { data: JSON.stringify(frame) });
  }
}

const PULL_ARGS: StartArgs = {
  serial: "SER1",
  direction: "pull",
  remote: "/sdcard/big.bin",
  local: "devices/SER1/big.bin",
};

const PUSH_ARGS: StartArgs = {
  serial: "SER1",
  direction: "push",
  remote: "/sdcard/app.apk",
  local: "devices/SER1/app.apk",
  force: true,
};

function lastWs(): MockWebSocket {
  const ws = MockWebSocket.instances[MockWebSocket.instances.length - 1];
  if (!ws) throw new Error("no WebSocket constructed");
  return ws;
}

function sentJson(ws: MockWebSocket, i: number): unknown {
  const raw = ws.sent[i];
  if (raw === undefined) throw new Error(`no sent frame at index ${i}`);
  return JSON.parse(raw);
}

beforeEach(() => {
  MockWebSocket.instances = [];
  vi.stubGlobal("WebSocket", MockWebSocket);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useFileTransferStream · frame → state mapping", () => {
  it("initial state is idle, inflight/progress/result null", () => {
    const { result } = renderHook(() => useFileTransferStream());
    expect(result.current.state).toBe("idle");
    expect(result.current.inflight).toBeNull();
    expect(result.current.progress).toBeNull();
    expect(result.current.result).toBeNull();
    expect(result.current.error).toBeNull();
  });

  it("start → connecting · config first-frame on open · ready → running", () => {
    const { result } = renderHook(() => useFileTransferStream());
    act(() => result.current.start(PULL_ARGS));
    expect(result.current.state).toBe("connecting");
    expect(lastWs().url).toBe("ws://test/devices/SER1/files/pull/stream");

    act(() => lastWs().open());
    expect(lastWs().sent).toHaveLength(1);
    expect(sentJson(lastWs(), 0)).toEqual({
      remote: PULL_ARGS.remote,
      local: PULL_ARGS.local,
    });

    act(() => lastWs().message({ type: "ready", direction: "pull" }));
    expect(result.current.state).toBe("running");
  });

  it("push config frame carries force=true when set", () => {
    const { result } = renderHook(() => useFileTransferStream());
    act(() => result.current.start(PUSH_ARGS));
    expect(lastWs().url).toBe("ws://test/devices/SER1/files/push/stream");
    act(() => lastWs().open());
    expect(sentJson(lastWs(), 0)).toEqual({
      remote: PUSH_ARGS.remote,
      local: PUSH_ARGS.local,
      force: true,
    });
  });

  it("progress frames map to progress state (missing percent → null)", () => {
    const { result } = renderHook(() => useFileTransferStream());
    act(() => result.current.start(PULL_ARGS));
    act(() => lastWs().open());
    act(() => lastWs().message({ type: "ready" }));

    act(() =>
      lastWs().message({
        type: "progress",
        percent: 42.5,
        bytes_transferred: 1024,
        file: "big.bin",
      }),
    );
    expect(result.current.progress).toEqual({
      percent: 42.5,
      bytes_transferred: 1024,
      file: "big.bin",
    });

    act(() => lastWs().message({ type: "progress", bytes_transferred: 2048 }));
    expect(result.current.progress).toEqual({
      percent: null,
      bytes_transferred: 2048,
      file: null,
    });
  });

  it("closed done/ok → state done · result carries args direction/paths", () => {
    const { result } = renderHook(() => useFileTransferStream());
    act(() => result.current.start(PULL_ARGS));
    act(() => lastWs().open());
    act(() => lastWs().message({ type: "ready" }));
    act(() =>
      lastWs().message({
        type: "closed",
        reason: "done",
        ok: true,
        bytes_transferred: 4096,
        duration_ms: 1500,
      }),
    );
    expect(result.current.state).toBe("done");
    expect(result.current.result).toEqual({
      reason: "done",
      ok: true,
      bytes_transferred: 4096,
      duration_ms: 1500,
      error: null,
      direction: "pull",
      remote: PULL_ARGS.remote,
      local: PULL_ARGS.local,
    });
    expect(result.current.error).toBeNull();
  });

  it("closed with error reason → state error + error message", () => {
    const { result } = renderHook(() => useFileTransferStream());
    act(() => result.current.start(PULL_ARGS));
    act(() => lastWs().open());
    act(() =>
      lastWs().message({
        type: "closed",
        reason: "adb_failed",
        ok: false,
        error: "device offline",
      }),
    );
    expect(result.current.state).toBe("error");
    expect(result.current.error).toBe("device offline");
    expect(result.current.result?.ok).toBe(false);
  });

  it("closed sensitive_path → needs_confirm + error surfaced", () => {
    const { result } = renderHook(() => useFileTransferStream());
    act(() => result.current.start(PUSH_ARGS));
    act(() => lastWs().open());
    act(() =>
      lastWs().message({
        type: "closed",
        reason: "sensitive_path",
        ok: false,
        error: "remote path requires force",
      }),
    );
    expect(result.current.state).toBe("needs_confirm");
    expect(result.current.error).toBe("remote path requires force");
    expect(result.current.result?.direction).toBe("push");
  });

  it("cancel() sends control frame · closed cancelled → state cancelled", () => {
    const { result } = renderHook(() => useFileTransferStream());
    act(() => result.current.start(PULL_ARGS));
    act(() => lastWs().open());
    act(() => lastWs().message({ type: "ready" }));

    act(() => result.current.cancel());
    // cancel doesn't flip state locally — server's closed frame is
    // authoritative.
    expect(result.current.state).toBe("running");
    expect(sentJson(lastWs(), 1)).toEqual({ type: "cancel" });

    act(() =>
      lastWs().message({ type: "closed", reason: "cancelled", ok: false }),
    );
    expect(result.current.state).toBe("cancelled");
  });

  it("browser-level ws error event → state error", () => {
    const { result } = renderHook(() => useFileTransferStream());
    act(() => result.current.start(PULL_ARGS));
    act(() => lastWs().emit("error"));
    expect(result.current.state).toBe("error");
    expect(result.current.error).toBe("WebSocket error");
  });

  it("bare close without closed frame → error 'connection closed unexpectedly'", () => {
    const { result } = renderHook(() => useFileTransferStream());
    act(() => result.current.start(PULL_ARGS));
    act(() => lastWs().open());
    act(() => lastWs().message({ type: "ready" }));
    act(() => lastWs().emit("close"));
    expect(result.current.state).toBe("error");
    expect(result.current.error).toBe("connection closed unexpectedly");
  });

  it("close after terminal closed frame does not override done", () => {
    const { result } = renderHook(() => useFileTransferStream());
    act(() => result.current.start(PULL_ARGS));
    act(() => lastWs().open());
    act(() => lastWs().message({ type: "closed", reason: "done", ok: true }));
    act(() => lastWs().emit("close"));
    expect(result.current.state).toBe("done");
    expect(result.current.error).toBeNull();
  });
});

describe("useFileTransferStream · inflight exposure", () => {
  it("set on start · cleared on done", () => {
    const { result } = renderHook(() => useFileTransferStream());
    act(() => result.current.start(PULL_ARGS));
    expect(result.current.inflight).toEqual(PULL_ARGS);

    act(() => lastWs().open());
    act(() => lastWs().message({ type: "ready" }));
    expect(result.current.inflight).toEqual(PULL_ARGS);

    act(() => lastWs().message({ type: "closed", reason: "done", ok: true }));
    expect(result.current.inflight).toBeNull();
  });

  it.each([
    ["error reason", { type: "closed", reason: "adb_failed", ok: false }],
    ["cancelled", { type: "closed", reason: "cancelled", ok: false }],
    [
      "sensitive_path",
      { type: "closed", reason: "sensitive_path", ok: false },
    ],
  ])("cleared on terminal closed frame · %s", (_label, frame) => {
    const { result } = renderHook(() => useFileTransferStream());
    act(() => result.current.start(PUSH_ARGS));
    act(() => lastWs().open());
    expect(result.current.inflight).toEqual(PUSH_ARGS);
    act(() => lastWs().message(frame));
    expect(result.current.inflight).toBeNull();
  });

  it("cleared on browser ws error and on bare close", () => {
    const { result } = renderHook(() => useFileTransferStream());
    act(() => result.current.start(PULL_ARGS));
    act(() => lastWs().emit("error"));
    expect(result.current.inflight).toBeNull();

    act(() => result.current.start(PULL_ARGS));
    act(() => lastWs().open());
    act(() => lastWs().emit("close"));
    expect(result.current.inflight).toBeNull();
  });

  it("cleared by reset()", () => {
    const { result } = renderHook(() => useFileTransferStream());
    act(() => result.current.start(PULL_ARGS));
    expect(result.current.inflight).toEqual(PULL_ARGS);
    act(() => result.current.reset());
    expect(result.current.inflight).toBeNull();
    expect(result.current.state).toBe("idle");
  });

  it("restart switches inflight · stale socket frames don't pollute", () => {
    const { result } = renderHook(() => useFileTransferStream());
    act(() => result.current.start(PULL_ARGS));
    const first = lastWs();
    act(() => first.open());
    act(() => first.message({ type: "ready" }));
    expect(result.current.state).toBe("running");

    act(() => result.current.start(PUSH_ARGS));
    const second = lastWs();
    expect(second).not.toBe(first);
    expect(result.current.state).toBe("connecting");
    expect(result.current.inflight).toEqual(PUSH_ARGS);

    // Late frames / close from the aborted first socket must not
    // touch the new transfer's state, result or inflight.
    act(() => first.message({ type: "closed", reason: "done", ok: true }));
    act(() => first.emit("close"));
    expect(result.current.state).toBe("connecting");
    expect(result.current.inflight).toEqual(PUSH_ARGS);
    expect(result.current.result).toBeNull();
  });
});
