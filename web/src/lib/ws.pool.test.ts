/**
 * lib/ws.ts · pooled-mode spec (DEBT-047 · AP-1 closure / DEBT-078
 * concern split — pool · dedup · cachedSnapshot · lifecycle).
 *
 * Pins entry sharing by (path, shareKey), view refcount lifetime,
 * broadcast fan-out, and late-joiner replay/ordering so future
 * refactors can't silently break the contract `useAuditStream`
 * relies on. Send dedup lives in ws.dedup.test.ts; snapshot age
 * checks in ws.cachedSnapshot.test.ts; solo mode + listener-throw
 * isolation + connect() mode routing in ws.lifecycle.test.ts.
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

describe("lib/ws · late-joiner microtask event ordering (AR-3 / ui-f HIGH-2)", () => {
  it("microtask delivers open THEN snapshot to the late joiner — never in reverse", async () => {
    connect("/audit/stream", { shareKey: "k1" });
    fakeAt(0).simulateOpen();
    fakeAt(0).simulateJson({ type: "snapshot", events: ["seed"] });

    const lateView = connect("/audit/stream", { shareKey: "k1" });
    const ordered: string[] = [];
    lateView.subscribe((ev) => {
      if (ev.kind === "open") ordered.push("open");
      else if (ev.kind === "json") ordered.push("json");
    });
    await Promise.resolve();
    expect(ordered).toEqual(["open", "json"]);
  });

  it("synchronous events arriving between subscribe() and microtask flush DO race ahead — known timing window, deltas arrive ahead of synth bootstrap", async () => {
    connect("/audit/stream", { shareKey: "k1" });
    fakeAt(0).simulateOpen();
    fakeAt(0).simulateJson({ type: "snapshot", events: ["snap1"] });

    const lateView = connect("/audit/stream", { shareKey: "k1" });
    const ordered: WsEvent[] = [];
    lateView.subscribe((ev) => ordered.push(ev));

    // A "later" delta from the underlying socket arrives in the SAME
    // synchronous frame as subscribe() — BEFORE the queued microtask
    // flushes. This pin's the known limitation: the synth-bootstrap
    // microtask runs at end-of-task; any synchronous event fired in
    // between reaches the late joiner FIRST.
    //
    // In production this window is microseconds (subscribe is in a
    // useEffect, no network event arrives in the same JS frame), but
    // consumers that need strict "snapshot before any delta" ordering
    // MUST defer their own state updates until the snapshot event,
    // not the first event of any kind. See ADR-045 trade-off section.
    fakeAt(0).simulateJson({ type: "event", data: { ts: "t2" } });

    await Promise.resolve();

    expect(ordered.length).toBe(3);
    // Sync delta first (the known race window):
    expect((ordered[0] as { data: { type: string } }).data.type).toBe(
      "event",
    );
    // Then microtask: synth open + cached snapshot:
    expect(ordered[1]).toEqual({ kind: "open" });
    expect((ordered[2] as { data: { type: string } }).data.type).toBe(
      "snapshot",
    );
  });
});
