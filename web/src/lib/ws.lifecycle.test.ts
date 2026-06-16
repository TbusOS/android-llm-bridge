/**
 * lib/ws.ts · lifecycle spec (DEBT-078 concern split — pool · dedup ·
 * cachedSnapshot · lifecycle).
 *
 * Pins solo-mode socket lifetime (one socket per connect(), real
 * close), listener-throw isolation in both fan-out paths (AR-3), and
 * connect()'s mode routing: `noReconnect` is a SoloOptions-only field
 * (ADR-047 AW-2 · 方案 X) — passing it together with `shareKey` warns
 * (dev) and is ignored. Pool entry semantics live in ws.pool.test.ts;
 * send dedup in ws.dedup.test.ts; snapshot age rules in
 * ws.cachedSnapshot.test.ts.
 *
 * Tests stub the global WebSocket constructor — the helper itself
 * never opens a real socket. `__resetPoolForTests` clears module
 * state between cases so one test's pool entries don't leak.
 *
 * The FakeWebSocket harness below is duplicated verbatim across the
 * ws.*.test.ts family: each spec file must run in isolation, and
 * importing a sibling *.test.ts module would re-register that file's
 * suites in the importer. Keep the copies in sync when touching it.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  connect,
  __resetPoolForTests,
  __setListenerErrorHandlerForTests,
  __setNowProviderForTests,
  type WsEvent,
} from "./ws";

type Listener = (ev: Event | MessageEvent | CloseEvent) => void;

/** Minimal fake mimicking the browser's WebSocket surface that
 *  `lib/ws.ts` actually touches (constructor, binaryType setter,
 *  addEventListener for open/message/error/close, send, close,
 *  readyState). Tests drive events via the `simulate*` helpers
 *  instead of going through a real network. */
class FakeWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  /** Instances created by `new FakeWebSocket(url)` during the current
   *  test, in construction order. Reset before each test. */
  static instances: FakeWebSocket[] = [];

  url: string;
  binaryType: string = "";
  readyState: number = FakeWebSocket.CONNECTING;
  closeMock = vi.fn<(code?: number, reason?: string) => void>();
  sendMock = vi.fn<(data: unknown) => void>();
  private listeners: Record<string, Listener[]> = {
    open: [],
    message: [],
    error: [],
    close: [],
  };

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  addEventListener(type: string, listener: Listener): void {
    (this.listeners[type] ??= []).push(listener);
  }

  removeEventListener(type: string, listener: Listener): void {
    const arr = this.listeners[type];
    if (!arr) return;
    const idx = arr.indexOf(listener);
    if (idx >= 0) arr.splice(idx, 1);
  }

  send(data: unknown): void {
    this.sendMock(data);
  }

  close(code?: number, reason?: string): void {
    this.closeMock(code, reason);
    this.readyState = FakeWebSocket.CLOSED;
    this.simulateClose(code ?? 1000, reason ?? "");
  }

  simulateOpen(): void {
    this.readyState = FakeWebSocket.OPEN;
    for (const l of this.listeners.open ?? []) l(new Event("open"));
  }

  simulateJson(payload: unknown): void {
    const msg = new MessageEvent("message", {
      data: JSON.stringify(payload),
    });
    for (const l of this.listeners.message ?? []) l(msg);
  }

  simulateClose(code: number, reason: string): void {
    const ev = { code, reason, type: "close" } as unknown as CloseEvent;
    for (const l of this.listeners.close ?? []) l(ev);
  }
}

// Capture-by-default listener-error handler so a throwing listener
// installed by one test doesn't leak into the next test's
// __resetPoolForTests close fan-out (close events broadcast to live
// listeners, and the default handler re-throws on a microtask — which
// vitest turns into an "unhandled error" test failure even though the
// failing test already finished).
let capturedListenerErrors: unknown[] = [];
let prevListenerErrorHandler: ((e: unknown) => void) | null = null;
// AT-4 (5/26 第六轮 code LOW-4): default nowMs reset in beforeEach so
// any spec that swaps the provider (cachedSnapshot age check tests)
// can't leak its frozen clock into the next spec. Pattern mirrors
// `resetMockImpls` in PlaygroundPage.component.test.tsx — cleanup is
// the framework's job, not the spec author's.
const DEFAULT_NOW = (): number => Date.now();

beforeEach(() => {
  capturedListenerErrors = [];
  prevListenerErrorHandler = __setListenerErrorHandlerForTests((e) => {
    capturedListenerErrors.push(e);
  });
  __setNowProviderForTests(DEFAULT_NOW);
  FakeWebSocket.instances = [];
  __resetPoolForTests();
  vi.stubGlobal("WebSocket", FakeWebSocket);
});

afterEach(() => {
  __resetPoolForTests();
  vi.unstubAllGlobals();
  __setNowProviderForTests(DEFAULT_NOW);
  if (prevListenerErrorHandler) {
    __setListenerErrorHandlerForTests(prevListenerErrorHandler);
    prevListenerErrorHandler = null;
  }
});

/** TS-strict-friendly accessor: throws if the requested fake socket
 *  hasn't been constructed yet. Beats spamming `!`. */
function fakeAt(i: number): FakeWebSocket {
  const f = FakeWebSocket.instances[i];
  if (!f) throw new Error(`FakeWebSocket.instances[${i}] missing`);
  return f;
}

describe("lib/ws · solo mode (no shareKey)", () => {
  it("each connect() mints its own underlying socket", () => {
    connect("/audit/stream");
    connect("/audit/stream");
    expect(FakeWebSocket.instances.length).toBe(2);
  });

  it("close() actually closes the underlying socket (no refcount)", () => {
    const a = connect("/audit/stream");
    a.close();
    expect(fakeAt(0).closeMock).toHaveBeenCalled();
  });
});

describe("lib/ws · noReconnect is solo-mode-only (ADR-047 AW-2 · 方案 X)", () => {
  // Reconnect scheduling rides window.setTimeout with a randomised
  // backoff ≤ 500 ms on the first attempt — fake only the timer APIs
  // the reconnect path touches so microtask-based specs elsewhere in
  // this file stay on real timers.
  it("solo + noReconnect:true → underlying close does NOT schedule a reconnect", () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    try {
      connect("/chat/ws", { noReconnect: true });
      fakeAt(0).simulateOpen();
      fakeAt(0).simulateClose(1006, "dropped");
      vi.advanceTimersByTime(60_000);
      expect(FakeWebSocket.instances.length).toBe(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("solo default (no noReconnect) → underlying close schedules a reconnect", () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    try {
      connect("/audit/stream");
      fakeAt(0).simulateOpen();
      fakeAt(0).simulateClose(1006, "dropped");
      vi.advanceTimersByTime(60_000);
      expect(FakeWebSocket.instances.length).toBe(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("noReconnect + shareKey together → console.warn (dev) + noReconnect ignored (pooled socket still reconnects)", () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    try {
      connect("/audit/stream", { shareKey: "k1", noReconnect: true });
      expect(warnSpy).toHaveBeenCalledTimes(1);
      expect(String(warnSpy.mock.calls[0]?.[0])).toContain("noReconnect");
      fakeAt(0).simulateOpen();
      fakeAt(0).simulateClose(1006, "dropped");
      vi.advanceTimersByTime(60_000);
      // The ignored noReconnect did NOT leak into the pool's underlying
      // socket — it reconnected.
      expect(FakeWebSocket.instances.length).toBe(2);
    } finally {
      vi.useRealTimers();
      warnSpy.mockRestore();
    }
  });

  it("shareKey without noReconnect → no warn", () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    try {
      connect("/audit/stream", { shareKey: "k1" });
      expect(warnSpy).not.toHaveBeenCalled();
    } finally {
      warnSpy.mockRestore();
    }
  });
});

describe("lib/ws · listener-throw isolation (AR-3 / code MID-1)", () => {
  it("pool fan-out: a throwing listener does NOT starve sibling listeners on the same event · errors routed to handler", () => {
    const a = connect("/audit/stream", { shareKey: "k1" });
    const b = connect("/audit/stream", { shareKey: "k1" });
    const aEvents: WsEvent[] = [];
    const bEvents: WsEvent[] = [];
    a.subscribe(() => {
      throw new Error("listener-a deliberately throws");
    });
    a.subscribe((ev) => aEvents.push(ev));
    b.subscribe((ev) => bEvents.push(ev));
    fakeAt(0).simulateOpen();
    fakeAt(0).simulateJson({ type: "snapshot", events: [] });
    // 2 events × 2 sibling listeners survived the throw.
    expect(aEvents.length).toBe(2);
    expect(bEvents.length).toBe(2);
    // Both events triggered the throwing listener once → 2 captured by
    // the spec-wide capture handler installed in beforeEach.
    expect(capturedListenerErrors.length).toBe(2);
    expect((capturedListenerErrors[0] as Error).message).toBe(
      "listener-a deliberately throws",
    );
  });

  it("solo fan-out: a throwing listener does NOT starve siblings either", () => {
    const c = connect("/audit/stream");
    const okEvents: WsEvent[] = [];
    c.subscribe(() => {
      throw new Error("solo-listener-throws");
    });
    c.subscribe((ev) => okEvents.push(ev));
    fakeAt(0).simulateOpen();
    expect(okEvents).toEqual([{ kind: "open" }]);
    expect(capturedListenerErrors.length).toBe(1);
  });
});
