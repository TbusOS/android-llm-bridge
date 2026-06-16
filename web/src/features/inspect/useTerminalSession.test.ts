/**
 * useTerminalSession lifecycle / stale-socket race / HITL specs.
 *
 * Stubs the global WebSocket constructor (same approach as
 * lib/ws.test.ts). The fake's close() deliberately does NOT emit a
 * close event — real close events arrive async, and the stale-socket
 * specs need the old socket's events to land AFTER a new connect().
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useTerminalSession, type HitlRequest } from "./useTerminalSession";

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

  /** Text frames sent by the hook, JSON-parsed. */
  jsonSent(): unknown[] {
    return this.sent
      .filter((d): d is string => typeof d === "string")
      .map((d) => JSON.parse(d));
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

describe("useTerminalSession", () => {
  it("idle until connect · open sends config (rows/cols/read_only defaults) · ready frame → ready", () => {
    const { result } = renderHook(() => useTerminalSession());
    expect(result.current.state).toBe("idle");

    act(() => result.current.connect({ device: "d1" }));
    expect(result.current.state).toBe("connecting");
    expect(sock(0).url).toBe("ws://test/terminal/ws");

    act(() => sock(0).fireOpen());
    expect(sock(0).jsonSent()[0]).toEqual({
      rows: 30,
      cols: 100,
      read_only: false,
      device: "d1",
    });

    act(() => sock(0).fireJson({ type: "ready", session_id: "s1" }));
    expect(result.current.state).toBe("ready");
  });

  it("closed frame mapping: exit_code without error → ended+exitCode · with error → error+message", () => {
    const { result } = renderHook(() => useTerminalSession());
    act(() => result.current.connect());
    act(() => sock(0).fireOpen());
    act(() => sock(0).fireJson({ type: "ready" }));
    act(() => sock(0).fireJson({ type: "closed", exit_code: 0 }));
    expect(result.current.state).toBe("ended");
    expect(result.current.exitCode).toBe(0);
    expect(result.current.error).toBeNull();

    act(() => result.current.connect());
    act(() => sock(1).fireOpen());
    act(() => sock(1).fireJson({ type: "ready" }));
    act(() =>
      sock(1).fireJson({ type: "closed", exit_code: 1, error: "spawn failed" }),
    );
    expect(result.current.state).toBe("error");
    expect(result.current.error).toBe("spawn failed");
    expect(result.current.exitCode).toBe(1);
  });

  it("ws error event → error · server-side close while ready → ended", () => {
    const { result } = renderHook(() => useTerminalSession());
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

  it("hitl_request with NO subscriber → auto-deny frame {type, approve:false, allow_session:false}", () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    try {
      const { result } = renderHook(() => useTerminalSession());
      act(() => result.current.connect());
      act(() => sock(0).fireOpen());
      act(() => sock(0).fireJson({ type: "ready" }));

      act(() =>
        sock(0).fireJson({
          type: "hitl_request",
          command: "reboot",
          rule: "deny_reboot",
          reason: "destructive",
        }),
      );

      const frames = sock(0).jsonSent();
      expect(frames[frames.length - 1]).toEqual({
        type: "hitl_response",
        approve: false,
        allow_session: false,
      });
      expect(warnSpy).toHaveBeenCalled();
    } finally {
      warnSpy.mockRestore();
    }
  });

  it("hitl_request WITH subscriber → no auto-deny · subscriber gets the request · respondHitl sends the reply", () => {
    const { result } = renderHook(() => useTerminalSession());
    const requests: HitlRequest[] = [];
    act(() => {
      result.current.onHitl((req) => requests.push(req));
      result.current.connect();
    });
    act(() => sock(0).fireOpen());
    act(() => sock(0).fireJson({ type: "ready" }));
    const framesBefore = sock(0).jsonSent().length;

    act(() =>
      sock(0).fireJson({
        type: "hitl_request",
        command: "rm /sdcard/x",
        rule: "deny_rm",
        reason: "guard",
      }),
    );
    // No auto-deny went out — the modal owns the reply.
    expect(sock(0).jsonSent().length).toBe(framesBefore);
    expect(requests).toHaveLength(1);
    expect(requests[0]).toMatchObject({
      command: "rm /sdcard/x",
      rule: "deny_rm",
      reason: "guard",
    });

    act(() => result.current.respondHitl(true, true));
    const frames = sock(0).jsonSent();
    expect(frames[frames.length - 1]).toEqual({
      type: "hitl_response",
      approve: true,
      allow_session: true,
    });
  });

  it("re-connect: stale socket's closed frame / error / close / bytes don't touch the new connection (CR-1)", () => {
    const { result } = renderHook(() => useTerminalSession());
    const chunks: ArrayBuffer[] = [];
    act(() => {
      result.current.onBytes((c) => chunks.push(c));
      result.current.connect();
    });
    act(() => sock(0).fireOpen());
    act(() => sock(0).fireJson({ type: "ready" }));
    expect(result.current.state).toBe("ready");

    act(() => result.current.connect());
    expect(FakeWebSocket.instances.length).toBe(2);
    expect(result.current.state).toBe("connecting");

    // Old socket's late events land after the new connect.
    act(() => sock(0).fireJson({ type: "closed", exit_code: 0 }));
    expect(result.current.state).toBe("connecting");
    expect(result.current.exitCode).toBeNull();
    act(() => sock(0).fireJson({ type: "closed", error: "boom" }));
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
    const { result } = renderHook(() => useTerminalSession());
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
