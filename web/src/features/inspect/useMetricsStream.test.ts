/**
 * useMetricsStream lifecycle / ring-buffer specs.
 *
 * Stubs the global WebSocket constructor (same approach as
 * lib/ws.test.ts); tests drive server frames via fireJson so timing
 * is fully controlled.
 *
 * NOTE: unlike its uart/logcat/terminal siblings, this hook has no
 * stale-socket guard yet (its handlers don't check wsRef against the
 * socket that fired) — so there is deliberately no connect-while-
 * connected race spec here. Add one when the guard lands.
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useMetricsStream, type MetricSample } from "./useMetricsStream";

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

/** Only ts_ms matters to the ring-buffer logic under test. */
function sample(ts: number): MetricSample {
  return { ts_ms: ts } as MetricSample;
}

beforeEach(() => {
  FakeWebSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeWebSocket);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useMetricsStream", () => {
  it("idle until connect · open sends config · history frame → ready + replay + intervalS", () => {
    const { result } = renderHook(() => useMetricsStream());
    expect(result.current.state).toBe("idle");

    act(() => result.current.connect("d1", 30));
    expect(result.current.state).toBe("connecting");
    expect(sock(0).url).toBe("ws://test/metrics/stream");

    act(() => sock(0).fireOpen());
    expect(JSON.parse(String(sock(0).sent[0]))).toEqual({
      history_seconds: 30,
      device: "d1",
    });

    act(() =>
      sock(0).fireJson({
        type: "history",
        interval_s: 2,
        samples: [sample(1), sample(2)],
      }),
    );
    expect(result.current.state).toBe("ready");
    expect(result.current.intervalS).toBe(2);
    expect(result.current.samples.map((s) => s.ts_ms)).toEqual([1, 2]);
  });

  it("sample frames append · ring buffer caps at maxSamples (oldest dropped)", () => {
    const { result } = renderHook(() => useMetricsStream(3));
    act(() => result.current.connect());
    act(() => sock(0).fireOpen());
    act(() =>
      sock(0).fireJson({ type: "history", samples: [sample(1), sample(2)] }),
    );

    act(() => sock(0).fireJson({ type: "sample", data: sample(3) }));
    expect(result.current.samples.map((s) => s.ts_ms)).toEqual([1, 2, 3]);

    act(() => sock(0).fireJson({ type: "sample", data: sample(4) }));
    expect(result.current.samples.map((s) => s.ts_ms)).toEqual([2, 3, 4]);
  });

  it("history replay longer than maxSamples is trimmed to the newest entries", () => {
    const { result } = renderHook(() => useMetricsStream(2));
    act(() => result.current.connect());
    act(() => sock(0).fireOpen());
    act(() =>
      sock(0).fireJson({
        type: "history",
        samples: [sample(1), sample(2), sample(3)],
      }),
    );
    expect(result.current.samples.map((s) => s.ts_ms)).toEqual([2, 3]);
  });

  it("control_ack updates paused + intervalS · pause()/resume() send control frames", () => {
    const { result } = renderHook(() => useMetricsStream());
    act(() => result.current.connect());
    act(() => sock(0).fireOpen());
    act(() => sock(0).fireJson({ type: "history", samples: [] }));

    act(() => result.current.pause());
    expect(JSON.parse(String(sock(0).sent[1]))).toEqual({
      type: "control",
      action: "pause",
    });

    act(() => sock(0).fireJson({ type: "control_ack", paused: true, interval_s: 5 }));
    expect(result.current.paused).toBe(true);
    expect(result.current.intervalS).toBe(5);

    act(() => result.current.resume());
    expect(JSON.parse(String(sock(0).sent[2]))).toEqual({
      type: "control",
      action: "resume",
    });
    act(() => sock(0).fireJson({ type: "control_ack", paused: false }));
    expect(result.current.paused).toBe(false);
  });

  it("ws error event → error · server-side close while ready → ended", () => {
    const { result } = renderHook(() => useMetricsStream());
    act(() => result.current.connect());
    act(() => sock(0).fireError());
    expect(result.current.state).toBe("error");
    expect(result.current.error).toBe("WebSocket error");

    act(() => result.current.connect());
    act(() => sock(1).fireOpen());
    act(() => sock(1).fireJson({ type: "history", samples: [] }));
    act(() => sock(1).fireClose(1006, "network"));
    expect(result.current.state).toBe("ended");
  });

  it("disconnect → idle · re-connect resets the sample buffer", () => {
    const { result } = renderHook(() => useMetricsStream());
    act(() => result.current.connect());
    act(() => sock(0).fireOpen());
    act(() => sock(0).fireJson({ type: "history", samples: [sample(1)] }));
    expect(result.current.samples).toHaveLength(1);

    act(() => result.current.disconnect());
    expect(result.current.state).toBe("idle");
    expect(result.current.error).toBeNull();

    act(() => result.current.connect());
    expect(result.current.samples).toEqual([]);
    expect(result.current.state).toBe("connecting");
  });
});
