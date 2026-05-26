/**
 * lib/ws.ts · pool behaviour spec (DEBT-047 · AP-1 closure).
 *
 * Pin the pool semantics so future refactors can't silently break
 * the dedup contract that `useAuditStream` relies on:
 *
 *   - solo mode (no shareKey) preserves the pre-DEBT-047 1-socket-
 *     per-connect() behaviour that `useWsChatStream` needs
 *   - pooled mode (shareKey) dedups by (path, shareKey), refcounts
 *     view lifetime, and replays cached snapshot to late joiners
 *
 * Tests stub the global WebSocket constructor — the helper itself
 * never opens a real socket. `__resetPoolForTests` clears module
 * state between cases so one test's pool entries don't leak.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { connect, __resetPoolForTests, type WsEvent } from "./ws";

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

beforeEach(() => {
  FakeWebSocket.instances = [];
  __resetPoolForTests();
  vi.stubGlobal("WebSocket", FakeWebSocket);
});

afterEach(() => {
  __resetPoolForTests();
  vi.unstubAllGlobals();
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

describe("lib/ws · pooled mode (shareKey)", () => {
  it("same (path, shareKey) shares ONE underlying socket", () => {
    connect("/audit/stream", { shareKey: "k1" });
    connect("/audit/stream", { shareKey: "k1" });
    expect(FakeWebSocket.instances.length).toBe(1);
  });

  it("different shareKey on same path → two sockets", () => {
    connect("/audit/stream", { shareKey: "k1" });
    connect("/audit/stream", { shareKey: "k2" });
    expect(FakeWebSocket.instances.length).toBe(2);
  });

  it("different path with same shareKey → two sockets (path is part of the key)", () => {
    connect("/audit/stream", { shareKey: "k1" });
    connect("/metrics/stream", { shareKey: "k1" });
    expect(FakeWebSocket.instances.length).toBe(2);
  });

  it("refcount: 1 of 2 views close → underlying stays open", () => {
    const a = connect("/audit/stream", { shareKey: "k1" });
    connect("/audit/stream", { shareKey: "k1" });
    a.close();
    expect(fakeAt(0).closeMock).not.toHaveBeenCalled();
  });

  it("refcount: last view close → underlying closes + entry dropped", () => {
    const a = connect("/audit/stream", { shareKey: "k1" });
    const b = connect("/audit/stream", { shareKey: "k1" });
    a.close();
    b.close();
    expect(fakeAt(0).closeMock).toHaveBeenCalledTimes(1);
    // After both views close, the pool entry is gone — reconnecting
    // with the same key mints a NEW underlying socket.
    connect("/audit/stream", { shareKey: "k1" });
    expect(FakeWebSocket.instances.length).toBe(2);
  });

  it("view.close() is idempotent (double-close doesn't double-decrement)", () => {
    const a = connect("/audit/stream", { shareKey: "k1" });
    connect("/audit/stream", { shareKey: "k1" });
    a.close();
    a.close(); // second call should be a no-op
    expect(fakeAt(0).closeMock).not.toHaveBeenCalled();
  });

  it("broadcast: a real json event reaches every view's subscriber", () => {
    const a = connect("/audit/stream", { shareKey: "k1" });
    const b = connect("/audit/stream", { shareKey: "k1" });
    const aEvents: WsEvent[] = [];
    const bEvents: WsEvent[] = [];
    a.subscribe((ev) => aEvents.push(ev));
    b.subscribe((ev) => bEvents.push(ev));
    fakeAt(0).simulateOpen();
    fakeAt(0).simulateJson({ type: "snapshot", events: [] });
    expect(aEvents).toEqual(bEvents);
    expect(aEvents.length).toBe(2);
    expect(aEvents[0]).toEqual({ kind: "open" });
    expect(aEvents[1]).toEqual({
      kind: "json",
      data: { type: "snapshot", events: [] },
    });
  });

  it("late joiner (subscribe after open, no snapshot yet) → microtask replays open only", async () => {
    connect("/audit/stream", { shareKey: "k1" });
    fakeAt(0).simulateOpen();

    const lateView = connect("/audit/stream", { shareKey: "k1" });
    const lateEvents: WsEvent[] = [];
    lateView.subscribe((ev) => lateEvents.push(ev));
    await Promise.resolve();

    expect(lateEvents).toEqual([{ kind: "open" }]);
  });

  it("late joiner (subscribe after snapshot cached) → replays open + snapshot", async () => {
    connect("/audit/stream", { shareKey: "k1" });
    fakeAt(0).simulateOpen();
    fakeAt(0).simulateJson({
      type: "snapshot",
      events: [{ ts: "t1", kind: "user" }],
    });

    const lateView = connect("/audit/stream", { shareKey: "k1" });
    const lateEvents: WsEvent[] = [];
    lateView.subscribe((ev) => lateEvents.push(ev));
    await Promise.resolve();

    expect(lateEvents.length).toBe(2);
    expect(lateEvents[0]).toEqual({ kind: "open" });
    expect(lateEvents[1]).toEqual({
      kind: "json",
      data: { type: "snapshot", events: [{ ts: "t1", kind: "user" }] },
    });
  });

  it("late joiner: subscribe + unsubscribe BEFORE microtask → listener never fires", async () => {
    connect("/audit/stream", { shareKey: "k1" });
    fakeAt(0).simulateOpen();
    fakeAt(0).simulateJson({ type: "snapshot", events: [] });

    const lateView = connect("/audit/stream", { shareKey: "k1" });
    const lateEvents: WsEvent[] = [];
    const unsub = lateView.subscribe((ev) => lateEvents.push(ev));
    unsub();
    await Promise.resolve();

    expect(lateEvents).toEqual([]);
  });

  it("late joiner: view.close() BEFORE microtask → listener never fires", async () => {
    connect("/audit/stream", { shareKey: "k1" });
    fakeAt(0).simulateOpen();

    const lateView = connect("/audit/stream", { shareKey: "k1" });
    const lateEvents: WsEvent[] = [];
    lateView.subscribe((ev) => lateEvents.push(ev));
    lateView.close();
    await Promise.resolve();

    expect(lateEvents).toEqual([]);
  });

  it("delta event after snapshot does NOT overwrite cached snapshot (late joiner still gets the snapshot)", async () => {
    connect("/audit/stream", { shareKey: "k1" });
    fakeAt(0).simulateOpen();
    fakeAt(0).simulateJson({ type: "snapshot", events: ["s"] });
    fakeAt(0).simulateJson({ type: "event", data: { ts: "t2", kind: "tool" } });

    const lateView = connect("/audit/stream", { shareKey: "k1" });
    const lateEvents: WsEvent[] = [];
    lateView.subscribe((ev) => lateEvents.push(ev));
    await Promise.resolve();

    // Late joiner sees open + the SNAPSHOT (the cached one), not the
    // delta — convergence comes from the snapshot, deltas after the
    // join arrive live.
    expect(lateEvents[1]).toEqual({
      kind: "json",
      data: { type: "snapshot", events: ["s"] },
    });
  });

  it("a newer snapshot replaces the cached one (later joiner sees the latest)", async () => {
    connect("/audit/stream", { shareKey: "k1" });
    fakeAt(0).simulateOpen();
    fakeAt(0).simulateJson({ type: "snapshot", events: ["v1"] });
    fakeAt(0).simulateJson({ type: "snapshot", events: ["v2"] });

    const lateView = connect("/audit/stream", { shareKey: "k1" });
    const lateEvents: WsEvent[] = [];
    lateView.subscribe((ev) => lateEvents.push(ev));
    await Promise.resolve();

    expect(lateEvents[1]).toEqual({
      kind: "json",
      data: { type: "snapshot", events: ["v2"] },
    });
  });

  it("view.send() delegates to the underlying socket (one call regardless of view count)", () => {
    const a = connect("/audit/stream", { shareKey: "k1" });
    const b = connect("/audit/stream", { shareKey: "k1" });
    fakeAt(0).simulateOpen();
    a.send({ hello: "from-a" });
    b.send({ hello: "from-b" });
    expect(fakeAt(0).sendMock).toHaveBeenCalledTimes(2);
  });

  it("late joiner does NOT trigger a real open event in the underlying socket", () => {
    connect("/audit/stream", { shareKey: "k1" });
    fakeAt(0).simulateOpen();
    // Just creating a second view on an open pool entry must not
    // construct a second WebSocket.
    connect("/audit/stream", { shareKey: "k1" });
    expect(FakeWebSocket.instances.length).toBe(1);
  });
});
