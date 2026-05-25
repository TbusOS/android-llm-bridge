/**
 * useAuditStream shareKey contract test (AM-3 · arch MID-14).
 *
 * lib/ws.connect's `shareKey` opt is reserved for DEBT-047 (WS pool
 * dedup). Today the key is computed but ignored — `void opts.shareKey`
 * in lib/ws.ts. When the pool lands, two callers with the same
 * `(path, shareKey)` will share one socket.
 *
 * That makes shareKey a **wire-format-style contract**: any change to
 * what we feed in (field set / serialization shape / option default)
 * silently re-keys existing callers and can break sharing assumptions
 * (e.g. ADR-022 — includeMetrics false vs true MUST NOT share).
 *
 * This file pins the exact byte sequence so any future drift fails
 * loud at test time, with a clear "if you changed shareKey on purpose,
 * update DEBT-047 + this spec" message.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";

import type { WsClient } from "../ws";

interface ConnectCapture {
  path: string | undefined;
  opts: { shareKey?: string } | undefined;
}

// Hoisted mock — capture the `(path, opts)` passed to connect().
// Return type annotated explicitly so TS doesn't widen lastConnect to
// `{ path: undefined; opts: undefined }` (which narrows opts.shareKey
// access to `never` at the call sites).
const { mockConnect, lastConnect } = vi.hoisted((): {
  mockConnect: ReturnType<typeof vi.fn>;
  lastConnect: ConnectCapture;
} => {
  const lastConnect: ConnectCapture = { path: undefined, opts: undefined };
  const mockConnect = vi.fn((path: string, opts?: { shareKey?: string }) => {
    lastConnect.path = path;
    lastConnect.opts = opts;
    return {
      send: vi.fn(),
      close: vi.fn(),
      subscribe: () => () => {},
      readyState: 1,
    } as unknown as WsClient;
  });
  return { mockConnect, lastConnect };
});

vi.mock("../ws", () => ({ connect: mockConnect }));

import { useAuditStream } from "./useAuditStream";

beforeEach(() => {
  mockConnect.mockClear();
  lastConnect.path = undefined;
  lastConnect.opts = undefined;
});

afterEach(() => {
  // unmount happens via teardown; no extra cleanup needed.
});

describe("useAuditStream · shareKey contract", () => {
  it("default opts → shareKey = canonical 'minutes 30 / includeMetrics false'", () => {
    renderHook(() => useAuditStream());
    expect(lastConnect.path).toBe("/audit/stream");
    // Field-order + value-string is the wire contract. Bump DEBT-047
    // and this test when intentionally changing.
    expect(lastConnect.opts?.shareKey).toBe(
      JSON.stringify({ minutes: 30, includeMetrics: false }),
    );
  });

  it("includeMetrics=true differs in shareKey → cannot share socket with false", () => {
    renderHook(() => useAuditStream({ includeMetrics: true }));
    const keyTrue = lastConnect.opts?.shareKey;

    mockConnect.mockClear();
    // reset capture; next renderHook will refill it
    lastConnect.opts = undefined as ConnectCapture["opts"];
    renderHook(() => useAuditStream({ includeMetrics: false }));
    const keyFalse = lastConnect.opts?.shareKey;

    expect(keyTrue).not.toBe(keyFalse);
    // ADR-022 invariant: timeline pause must not freeze metric stream.
    // If pool dedup ever lets these share, LiveSession spark dies.
    expect(keyTrue).toBe(
      JSON.stringify({ minutes: 30, includeMetrics: true }),
    );
    expect(keyFalse).toBe(
      JSON.stringify({ minutes: 30, includeMetrics: false }),
    );
  });

  it("minutes window differs → different shareKey", () => {
    renderHook(() => useAuditStream({ minutes: 5 }));
    const key5 = lastConnect.opts?.shareKey;

    mockConnect.mockClear();
    // reset capture; next renderHook will refill it
    lastConnect.opts = undefined as ConnectCapture["opts"];
    renderHook(() => useAuditStream({ minutes: 30 }));
    const key30 = lastConnect.opts?.shareKey;

    expect(key5).not.toBe(key30);
  });
});
