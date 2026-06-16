/**
 * lib/ws.ts · send-dedup spec (AR-1 / DEBT-078 concern split — pool ·
 * dedup · cachedSnapshot · lifecycle).
 *
 * Pins the per-epoch payload dedup that collapses N pooled views'
 * identical config sends into one wire message, plus the per-view
 * `forceNextSendFresh` one-shot bypass (AT-2) and the binary-payload
 * dedup exemption. Pool entry/refcount semantics live in
 * ws.pool.test.ts; cachedSnapshot age rules in
 * ws.cachedSnapshot.test.ts; solo mode + connect() mode routing in
 * ws.lifecycle.test.ts.
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
