/**
 * useLogcatStream lifecycle / stale-socket race specs.
 *
 * Stubs the global WebSocket constructor (same approach as
 * lib/ws.test.ts). The fake's close() deliberately does NOT emit a
 * close event — real close events arrive async, and the stale-socket
 * specs need the old socket's events to land AFTER a new connect()
 * (this is exactly the LogcatTab debounced filter-reconnect timing).
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useLogcatStream } from "./useLogcatStream";

vi.mock("../../lib/ws", () => ({
  wsUrl: (path: string) => `ws://test${path}`,
}));

interface FakeEvent {
  data?: unknown;
  code?: number;
  reason?: string;
}
type Listener = (ev: FakeEvent) => void;

class FakeWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  static instances: FakeWebSocket[] = [];

  url: string;
  binaryType = "";
  readyState = FakeWebSocket.CONNECTING;
  sent: unknown[] = [];
  private listeners: Record<string, Listener[]> = {};

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  addEventListener(type: string, l: Listener): void {
    (this.listeners[type] ??= []).push(l);
  }

  send(data: unknown): void {
    this.sent.push(data);
  }

  close(): void {
    this.readyState = FakeWebSocket.CLOSED;
  }

  fireOpen(): void {
    this.readyState = FakeWebSocket.OPEN;
    this.emit("open", {});
  }

  fireJson(payload: unknown): void {
    this.emit("message", { data: JSON.stringify(payload) });
  }

  fireBinary(buf: ArrayBuffer): void {
    this.emit("message", { data: buf });
  }

  fireError(): void {
    this.emit("error", {});
  }

  fireClose(code = 1000, reason = ""): void {
    this.readyState = FakeWebSocket.CLOSED;
    this.emit("close", { code, reason });
  }

  private emit(type: string, ev: FakeEvent): void {
    for (const l of [...(this.listeners[type] ?? [])]) l(ev);
  }
}

function sock(i: number): FakeWebSocket {
  const s = FakeWebSocket.instances[i];
  if (!s) throw new Error(`FakeWebSocket.instances[${i}] missing`);
  return s;
}

beforeEach(() => {
  FakeWebSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeWebSocket);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useLogcatStream", () => {
  it("idle until connect · open sends config · ready frame → ready", () => {
    const { result } = renderHook(() => useLogcatStream());
    expect(result.current.state).toBe("idle");

    act(() =>
      result.current.connect({ device: "d1", filter: "*:E", tags: ["MyApp"] }),
    );
    expect(result.current.state).toBe("connecting");
    expect(sock(0).url).toBe("ws://test/logcat/stream");

    act(() => sock(0).fireOpen());
    expect(JSON.parse(String(sock(0).sent[0]))).toEqual({
      device: "d1",
      filter: "*:E",
      tags: ["MyApp"],
    });

    act(() => sock(0).fireJson({ type: "ready" }));
    expect(result.current.state).toBe("ready");
  });

  it("closed-reason mapping: error reasons → error+message · other reasons → ended", () => {
    const { result } = renderHook(() => useLogcatStream());
    act(() => result.current.connect());
    act(() => sock(0).fireOpen());
    act(() => sock(0).fireJson({ type: "ready" }));
    act(() =>
      sock(0).fireJson({ type: "closed", reason: "bad_filter", error: "bad spec" }),
    );
    expect(result.current.state).toBe("error");
    expect(result.current.error).toBe("bad spec");

    act(() => result.current.connect());
    act(() => sock(1).fireOpen());
    act(() => sock(1).fireJson({ type: "ready" }));
    act(() => sock(1).fireJson({ type: "closed", reason: "eof" }));
    expect(result.current.state).toBe("ended");
    expect(result.current.error).toBeNull();
  });

  it("ws error event → error · server-side close while ready → ended", () => {
    const { result } = renderHook(() => useLogcatStream());
    act(() => result.current.connect());
    act(() => sock(0).fireError());
    expect(result.current.state).toBe("error");
    expect(result.current.error).toBe("WebSocket error");

    act(() => result.current.connect());
    act(() => sock(1).fireOpen());
    act(() => sock(1).fireJson({ type: "ready" }));
    act(() => sock(1).fireClose(1006, "network"));
    expect(result.current.state).toBe("ended");
  });

  it("binary frames fan out to onBytes subscribers", () => {
    const { result } = renderHook(() => useLogcatStream());
    const chunks: ArrayBuffer[] = [];
    act(() => {
      result.current.onBytes((c) => chunks.push(c));
      result.current.connect();
    });
    act(() => sock(0).fireOpen());
    const buf = new ArrayBuffer(4);
    act(() => sock(0).fireBinary(buf));
    expect(chunks).toEqual([buf]);
  });

  it("re-connect (debounced filter change): stale socket's closed frame / error / close / bytes don't touch the new connection (CR-1)", () => {
    const { result } = renderHook(() => useLogcatStream());
    const chunks: ArrayBuffer[] = [];
    act(() => {
      result.current.onBytes((c) => chunks.push(c));
      result.current.connect({ filter: "*:V" });
    });
    act(() => sock(0).fireOpen());
    act(() => sock(0).fireJson({ type: "ready" }));
    expect(result.current.state).toBe("ready");

    act(() => result.current.connect({ filter: "*:E" }));
    expect(FakeWebSocket.instances.length).toBe(2);
    expect(result.current.state).toBe("connecting");

    // Old socket's late events land after the new connect.
    act(() => sock(0).fireJson({ type: "closed", reason: "eof" }));
    expect(result.current.state).toBe("connecting");
    act(() =>
      sock(0).fireJson({ type: "closed", reason: "stream_error", error: "x" }),
    );
    expect(result.current.state).toBe("connecting");
    expect(result.current.error).toBeNull();
    act(() => sock(0).fireError());
    expect(result.current.state).toBe("connecting");
    expect(result.current.error).toBeNull();
    act(() => sock(0).fireClose(1006, "stale"));
    expect(result.current.state).toBe("connecting");
    act(() => sock(0).fireBinary(new ArrayBuffer(2)));
    expect(chunks).toEqual([]);

    // New socket still completes its handshake normally.
    act(() => sock(1).fireOpen());
    act(() => sock(1).fireJson({ type: "ready" }));
    expect(result.current.state).toBe("ready");
  });

  it("disconnect → idle · late close/error from the torn-down socket stays idle", () => {
    const { result } = renderHook(() => useLogcatStream());
    act(() => result.current.connect());
    act(() => sock(0).fireOpen());
    act(() => sock(0).fireJson({ type: "ready" }));

    act(() => result.current.disconnect());
    expect(result.current.state).toBe("idle");

    act(() => sock(0).fireClose(1000, "client close"));
    expect(result.current.state).toBe("idle");
    act(() => sock(0).fireError());
    expect(result.current.state).toBe("idle");
    expect(result.current.error).toBeNull();
  });
});
