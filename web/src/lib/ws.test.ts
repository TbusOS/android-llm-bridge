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

describe("lib/ws · pool send dedup-by-payload-per-epoch (AR-1)", () => {
  it("two views with same shareKey + identical payload after one open → underlying socket sees one send", () => {
    const a = connect("/audit/stream", { shareKey: "k1" });
    const b = connect("/audit/stream", { shareKey: "k1" });
    fakeAt(0).simulateOpen();
    a.send({ config: "x" });
    b.send({ config: "x" });
    expect(fakeAt(0).sendMock).toHaveBeenCalledTimes(1);
  });

  it("different payloads bypass dedup (each pass through)", () => {
    const a = connect("/audit/stream", { shareKey: "k1" });
    const b = connect("/audit/stream", { shareKey: "k1" });
    fakeAt(0).simulateOpen();
    a.send({ config: "x" });
    b.send({ config: "y" });
    expect(fakeAt(0).sendMock).toHaveBeenCalledTimes(2);
  });

  it("string vs object form of the SAME payload still dedups (JSON.stringify normalisation)", () => {
    const a = connect("/audit/stream", { shareKey: "k1" });
    const b = connect("/audit/stream", { shareKey: "k1" });
    fakeAt(0).simulateOpen();
    a.send({ k: 1 });
    b.send(JSON.stringify({ k: 1 }));
    expect(fakeAt(0).sendMock).toHaveBeenCalledTimes(1);
  });

  it("re-open (reconnect) bumps epoch · identical payload IS re-sent in the new epoch", () => {
    const a = connect("/audit/stream", { shareKey: "k1" });
    fakeAt(0).simulateOpen();
    a.send({ config: "x" });
    // Simulate reconnect: same fake fires another "open". In real life
    // soloConnect builds a new WebSocket but the pool fan-out sees only
    // an "open" event either way — that's what bumps currentEpoch.
    fakeAt(0).simulateOpen();
    a.send({ config: "x" });
    expect(fakeAt(0).sendMock).toHaveBeenCalledTimes(2);
  });

  it("binary frames (ArrayBuffer / typed array / Blob) bypass dedup entirely", () => {
    const a = connect("/audit/stream", { shareKey: "k1" });
    fakeAt(0).simulateOpen();
    const buf = new ArrayBuffer(8);
    a.send(buf);
    a.send(buf);
    expect(fakeAt(0).sendMock).toHaveBeenCalledTimes(2);
  });

  it("late joiner whose synth-open handler re-sends the same config → deduped at the pool", async () => {
    const first = connect("/audit/stream", { shareKey: "k1" });
    fakeAt(0).simulateOpen();
    first.subscribe(() => {});
    first.send({ config: "x" });

    const late = connect("/audit/stream", { shareKey: "k1" });
    late.subscribe((ev) => {
      if (ev.kind === "open") late.send({ config: "x" });
    });
    await Promise.resolve();
    // Only the first view's send hit the wire; the late joiner's
    // synth-open-triggered identical config was deduped.
    expect(fakeAt(0).sendMock).toHaveBeenCalledTimes(1);
  });

  it("solo mode (no shareKey) does NOT dedup (each send passes through)", () => {
    const a = connect("/audit/stream");
    fakeAt(0).simulateOpen();
    a.send({ config: "x" });
    a.send({ config: "x" });
    expect(fakeAt(0).sendMock).toHaveBeenCalledTimes(2);
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

describe("lib/ws · cachedSnapshot age check (AS-2 / DEBT-065 MID fix)", () => {
  // AT-4 (5/26 第六轮 code LOW-4): nowMs cleanup moved to beforeEach,
  // so these specs swap the provider via __setNowProviderForTests
  // without per-spec try/finally bookkeeping.
  it("fresh snapshot (< staleSnapshotMs) → microtask replays it", async () => {
    let t = 1_000_000_000_000;
    __setNowProviderForTests(() => t);
    connect("/audit/stream", { shareKey: "k1" });
    fakeAt(0).simulateOpen();
    fakeAt(0).simulateJson({ type: "snapshot", events: ["fresh"] });

    // Advance clock by 4 minutes — still under the 5 min default.
    t += 4 * 60 * 1000;
    const late = connect("/audit/stream", { shareKey: "k1" });
    const events: WsEvent[] = [];
    late.subscribe((ev) => events.push(ev));
    await Promise.resolve();

    expect(events.length).toBe(2);
    expect(events[0]).toEqual({ kind: "open" });
    expect((events[1] as { data: { events: string[] } }).data.events).toEqual([
      "fresh",
    ]);
  });

  it("stale snapshot (> staleSnapshotMs) → microtask skips replay · late joiner sees synth open only", async () => {
    let t = 1_000_000_000_000;
    __setNowProviderForTests(() => t);
    connect("/audit/stream", { shareKey: "k1" });
    fakeAt(0).simulateOpen();
    fakeAt(0).simulateJson({ type: "snapshot", events: ["old"] });

    // Advance clock by 6 minutes — past the 5 min default.
    t += 6 * 60 * 1000;
    const late = connect("/audit/stream", { shareKey: "k1" });
    const events: WsEvent[] = [];
    late.subscribe((ev) => events.push(ev));
    await Promise.resolve();

    expect(events).toEqual([{ kind: "open" }]);
  });

  it("stale path → late joiner's forceNextSendFresh flag bypasses dedup ONCE (per-view · not entry-wide)", async () => {
    let t = 1_000_000_000_000;
    __setNowProviderForTests(() => t);
    const first = connect("/audit/stream", { shareKey: "k1" });
    fakeAt(0).simulateOpen();
    first.send({ minutes: 30 });
    fakeAt(0).simulateJson({ type: "snapshot", events: ["old"] });

    t += 10 * 60 * 1000; // 10 min — definitely stale

    const late = connect("/audit/stream", { shareKey: "k1" });
    late.subscribe((ev) => {
      if (ev.kind === "open") late.send({ minutes: 30 });
    });
    await Promise.resolve();

    // Two sends reached the wire: the original AND the late joiner's
    // (per-view force flag let it bypass dedup once).
    expect(fakeAt(0).sendMock).toHaveBeenCalledTimes(2);
  });

  it("stale path is per-VIEW: late joiner's force-fresh does NOT let sibling views re-send same payload (AT-2 / HIGH-2 fix)", async () => {
    let t = 1_000_000_000_000;
    __setNowProviderForTests(() => t);
    const first = connect("/audit/stream", { shareKey: "k1" });
    fakeAt(0).simulateOpen();
    first.send({ minutes: 30 });
    fakeAt(0).simulateJson({ type: "snapshot", events: ["old"] });

    t += 10 * 60 * 1000;
    const late = connect("/audit/stream", { shareKey: "k1" });
    late.subscribe((ev) => {
      if (ev.kind === "open") late.send({ minutes: 30 });
    });
    await Promise.resolve();

    // After the late joiner's force-fresh send: 2 total.
    expect(fakeAt(0).sendMock).toHaveBeenCalledTimes(2);

    // Now the FIRST view re-sends the same payload. The stale path
    // only flipped the LATE view's per-view flag — `first` still sees
    // entry.lastSentPayload === {minutes:30} from its prior send AND
    // the late joiner's fresh send updated it again to the same value.
    // first's resend is a dedup hit · MUST NOT reach the wire.
    first.send({ minutes: 30 });
    expect(fakeAt(0).sendMock).toHaveBeenCalledTimes(2);
  });

  it("Options.staleSnapshotMs override: caller raises ceiling to 30 min → 6 min-old snapshot is still fresh", async () => {
    let t = 1_000_000_000_000;
    __setNowProviderForTests(() => t);
    connect("/audit/stream", {
      shareKey: "k1",
      staleSnapshotMs: 30 * 60 * 1000,
    });
    fakeAt(0).simulateOpen();
    fakeAt(0).simulateJson({ type: "snapshot", events: ["v"] });

    t += 6 * 60 * 1000; // past the 5 min default · still under custom 30 min
    const late = connect("/audit/stream", {
      shareKey: "k1",
      staleSnapshotMs: 30 * 60 * 1000,
    });
    const events: WsEvent[] = [];
    late.subscribe((ev) => events.push(ev));
    await Promise.resolve();

    expect(events.length).toBe(2);
    expect((events[1] as { data: { events: string[] } }).data.events).toEqual([
      "v",
    ]);
  });

  it("Options.staleSnapshotMs override: caller lowers to 1 min → 90 s-old snapshot is stale", async () => {
    let t = 1_000_000_000_000;
    __setNowProviderForTests(() => t);
    connect("/audit/stream", {
      shareKey: "k1",
      staleSnapshotMs: 60 * 1000,
    });
    fakeAt(0).simulateOpen();
    fakeAt(0).simulateJson({ type: "snapshot", events: ["old"] });

    t += 90 * 1000; // 90 s · past 1 min custom ceiling
    const late = connect("/audit/stream", {
      shareKey: "k1",
      staleSnapshotMs: 60 * 1000,
    });
    const events: WsEvent[] = [];
    late.subscribe((ev) => events.push(ev));
    await Promise.resolve();

    expect(events).toEqual([{ kind: "open" }]);
  });

  it("Options.staleSnapshotMs first-caller-wins: 2nd connect with different staleSnapshotMs is ignored", async () => {
    let t = 1_000_000_000_000;
    __setNowProviderForTests(() => t);
    connect("/audit/stream", {
      shareKey: "k1",
      staleSnapshotMs: 60 * 1000, // first caller pins 1 min
    });
    fakeAt(0).simulateOpen();
    fakeAt(0).simulateJson({ type: "snapshot", events: ["v"] });

    t += 90 * 1000;
    // Second caller WANTS 30 min ceiling, but pool entry already
    // exists with 1 min → 90s is still stale, replay skipped.
    const late = connect("/audit/stream", {
      shareKey: "k1",
      staleSnapshotMs: 30 * 60 * 1000,
    });
    const events: WsEvent[] = [];
    late.subscribe((ev) => events.push(ev));
    await Promise.resolve();

    expect(events).toEqual([{ kind: "open" }]);
  });

  it("no cachedSnapshot at all → microtask synth-open fires · no skip-snapshot side effect", async () => {
    // Pool entry exists but server hasn't sent any snapshot yet.
    connect("/audit/stream", { shareKey: "k1" });
    fakeAt(0).simulateOpen();

    const late = connect("/audit/stream", { shareKey: "k1" });
    const events: WsEvent[] = [];
    late.subscribe((ev) => events.push(ev));
    await Promise.resolve();

    expect(events).toEqual([{ kind: "open" }]);
  });
});

describe("lib/ws · staleSnapshotMs validation (AU-1 / code MID-1 + sec LOW)", () => {
  // Each invalid value silently degrades dedup a different way — pin
  // the fallback + warn at the connect() boundary so the misuse can't
  // sneak through into the entry stale check.
  async function expectInvalidFallsBackToDefault(
    value: number,
    label: string,
  ): Promise<void> {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    try {
      let t = 1_000_000_000_000;
      __setNowProviderForTests(() => t);
      connect("/audit/stream", { shareKey: label, staleSnapshotMs: value });
      fakeAt(0).simulateOpen();
      fakeAt(0).simulateJson({ type: "snapshot", events: ["v"] });

      // Default = 5 min. After 4 min the snapshot is still FRESH; if the
      // invalid value leaked through it would change this behaviour
      // (NaN → never stale BUT entry already uses default so still
      // fresh; negative → always stale → cache skipped).
      t += 4 * 60 * 1000;
      const late = connect("/audit/stream", {
        shareKey: label,
        staleSnapshotMs: value,
      });
      const events: WsEvent[] = [];
      late.subscribe((ev) => events.push(ev));
      await Promise.resolve();
      expect(events.length).toBe(2); // open + cached snapshot (still fresh)

      // Advance past default ceiling → cache becomes stale even though
      // value would have said "never stale" (NaN/Infinity case) — proves
      // entry is using DEFAULT_STALE_SNAPSHOT_MS, not the invalid value.
      t += 2 * 60 * 1000; // now 6 min after snapshot
      const second = connect("/audit/stream", {
        shareKey: label,
        staleSnapshotMs: value,
      });
      const events2: WsEvent[] = [];
      second.subscribe((ev) => events2.push(ev));
      await Promise.resolve();
      expect(events2).toEqual([{ kind: "open" }]); // stale, no snapshot

      expect(warnSpy).toHaveBeenCalled();
    } finally {
      warnSpy.mockRestore();
    }
  }

  it("NaN → fallback to default + warn", async () => {
    await expectInvalidFallsBackToDefault(Number.NaN, "kNaN");
  });

  it("negative number → fallback to default + warn", async () => {
    await expectInvalidFallsBackToDefault(-1, "kNeg");
  });

  it("Infinity → fallback to default + warn", async () => {
    await expectInvalidFallsBackToDefault(Number.POSITIVE_INFINITY, "kInf");
  });

  it("0 is LEGAL (treat-as-stale always) — does NOT trigger warn", async () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    try {
      let t = 1_000_000_000_000;
      __setNowProviderForTests(() => t);
      connect("/audit/stream", { shareKey: "k0", staleSnapshotMs: 0 });
      fakeAt(0).simulateOpen();
      fakeAt(0).simulateJson({ type: "snapshot", events: ["v"] });

      // Any nonzero age vs ceiling 0 → `now - at > 0` is true → stale.
      t += 1;
      const late = connect("/audit/stream", {
        shareKey: "k0",
        staleSnapshotMs: 0,
      });
      const events: WsEvent[] = [];
      late.subscribe((ev) => events.push(ev));
      await Promise.resolve();
      expect(events).toEqual([{ kind: "open" }]); // cached snapshot skipped
      expect(warnSpy).not.toHaveBeenCalled();
    } finally {
      warnSpy.mockRestore();
    }
  });
});

describe("lib/ws · per-view forceNextSendFresh consume semantics (AU-5 / code LOW)", () => {
  it("late joiner with stale cache: force-flag consumed on first send through view (next dup is deduped)", async () => {
    let t = 1_000_000_000_000;
    __setNowProviderForTests(() => t);
    const first = connect("/audit/stream", { shareKey: "k1" });
    fakeAt(0).simulateOpen();
    first.send({ minutes: 30 });
    fakeAt(0).simulateJson({ type: "snapshot", events: ["old"] });

    t += 10 * 60 * 1000; // stale

    const late = connect("/audit/stream", { shareKey: "k1" });
    late.subscribe((ev) => {
      if (ev.kind === "open") late.send({ minutes: 30 });
    });
    await Promise.resolve();

    // 2 sends so far: first's original + late's force-fresh.
    expect(fakeAt(0).sendMock).toHaveBeenCalledTimes(2);

    // The force-flag was a one-shot — same view re-sending the same
    // payload now hits dedup (entry.lastSentPayload was updated by
    // late's force-fresh send, so the duplicate check matches).
    late.send({ minutes: 30 });
    expect(fakeAt(0).sendMock).toHaveBeenCalledTimes(2);
  });
});

describe("lib/ws · SharedArrayBuffer payload routes through binary path (AS-3 code LOW-4 regression)", () => {
  // AU-2 (5/26 第七轮 code MID-2): use vitest's `it.skipIf` so reporter
  // shows a SKIPPED status when SAB is unavailable (post-Spectre browser
  // without crossOriginIsolated) — the prior `if (...) return` made the
  // spec silently PASS with no assertion.
  it.skipIf(typeof SharedArrayBuffer === "undefined")(
    "SAB send bypasses dedup (binary path) · 2 identical sends both reach the wire",
    () => {
      const a = connect("/audit/stream", { shareKey: "k1" });
      fakeAt(0).simulateOpen();
      const sab = new SharedArrayBuffer(16);
      a.send(sab);
      a.send(sab);
      expect(fakeAt(0).sendMock).toHaveBeenCalledTimes(2);
    },
  );
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
