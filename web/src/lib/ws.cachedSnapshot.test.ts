/**
 * lib/ws.ts · cachedSnapshot spec (AS-2 · DEBT-065 / DEBT-078 concern
 * split — pool · dedup · cachedSnapshot · lifecycle).
 *
 * Pins the late-joiner snapshot age check: fresh cache replays,
 * stale cache is skipped + flips the per-view force-fresh flag, and
 * the `staleSnapshotMs` ceiling honours first-caller-wins plus the
 * AU-1 validation fallback. Entry/refcount semantics live in
 * ws.pool.test.ts; send dedup in ws.dedup.test.ts; solo mode +
 * connect() mode routing in ws.lifecycle.test.ts.
 *
 * NOTE: the `30 * 60 * 1000` literals below are deliberate — lib-layer
 * specs must not import hook-layer constants (e.g.
 * AUDIT_STREAM_DEFAULT_STALE_MS); the dependency only points hook → lib.
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
