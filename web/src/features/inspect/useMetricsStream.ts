/**
 * Live device metrics stream hook (PR-F).
 *
 * Subscribes to `/metrics/stream` WS — server pushes one
 * MetricSample per interval (default 1 Hz). Hook keeps a rolling
 * ring buffer (last `maxSamples` ticks) for charts to read.
 *
 * Differs from useUart/Logcat/TerminalSession in that the server
 * frames are JSON `{type: 'sample', data: MetricSample}` (not raw
 * binary) — we don't go through xterm so that's the natural shape.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { wsUrl } from "../../lib/ws";

export interface MetricSample {
  ts_ms: number;
  cpu_pct_total: number;
  cpu_freq_khz: number[];
  cpu_temp_c: number;
  mem_used_kb: number;
  mem_total_kb: number;
  mem_avail_kb: number;
  swap_used_kb: number;
  gpu_freq_hz: number;
  gpu_util_pct: number;
  net_rx_bytes_per_s: number;
  net_tx_bytes_per_s: number;
  disk_read_kb_per_s: number;
  disk_write_kb_per_s: number;
  battery_temp_c: number;
}

export type MetricsState = "idle" | "connecting" | "ready" | "ended" | "error";

export interface MetricsApi {
  state: MetricsState;
  error: string | null;
  samples: MetricSample[];
  intervalS: number;
  paused: boolean;
  connect: (device?: string | null, historySeconds?: number) => void;
  disconnect: () => void;
  pause: () => void;
  resume: () => void;
}

export function useMetricsStream(maxSamples = 120): MetricsApi {
  const wsRef = useRef<WebSocket | null>(null);
  const [state, setState] = useState<MetricsState>("idle");
  const [error, setError] = useState<string | null>(null);
  const [samples, setSamples] = useState<MetricSample[]>([]);
  const [intervalS, setIntervalS] = useState(1);
  const [paused, setPaused] = useState(false);

  const cleanup = useCallback(() => {
    const ws = wsRef.current;
    wsRef.current = null;
    if (ws && ws.readyState <= WebSocket.OPEN) {
      try {
        ws.close();
      } catch {
        // ignore
      }
    }
  }, []);

  const disconnect = useCallback(() => {
    cleanup();
    setState("idle");
    setError(null);
    setPaused(false);
  }, [cleanup]);

  const sendControl = useCallback((action: string, valueS?: number) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    try {
      ws.send(
        JSON.stringify({
          type: "control",
          action,
          ...(valueS !== undefined ? { value_s: valueS } : {}),
        }),
      );
    } catch {
      // ignore
    }
  }, []);

  const pause = useCallback(() => sendControl("pause"), [sendControl]);
  const resume = useCallback(() => sendControl("resume"), [sendControl]);

  const connect = useCallback(
    (device?: string | null, historySeconds = 60) => {
      cleanup();
      setError(null);
      setSamples([]);
      setPaused(false);
      setState("connecting");

      const ws = new WebSocket(wsUrl("/metrics/stream"));
      wsRef.current = ws;

      ws.addEventListener("open", () => {
        const config: Record<string, unknown> = { history_seconds: historySeconds };
        if (device) config.device = device;
        try {
          ws.send(JSON.stringify(config));
        } catch {
          // ignore
        }
      });

      ws.addEventListener("message", (ev) => {
        if (typeof ev.data !== "string") return;
        try {
          const msg = JSON.parse(ev.data);
          if (msg.type === "history") {
            setIntervalS(msg.interval_s ?? 1);
            const replay = (msg.samples ?? []) as MetricSample[];
            setSamples(replay.slice(-maxSamples));
            setState("ready");
          } else if (msg.type === "sample") {
            const s = msg.data as MetricSample;
            setSamples((prev) => {
              const next = prev.length >= maxSamples
                ? prev.slice(prev.length - maxSamples + 1)
                : prev.slice();
              next.push(s);
              return next;
            });
          } else if (msg.type === "control_ack") {
            if (typeof msg.interval_s === "number") setIntervalS(msg.interval_s);
            if (typeof msg.paused === "boolean") setPaused(msg.paused);
          }
        } catch {
          // ignore malformed frame
        }
      });

      ws.addEventListener("error", () => {
        setState("error");
        setError("WebSocket error");
      });

      ws.addEventListener("close", () => {
        setState((s) => (s === "ready" || s === "connecting" ? "ended" : s));
        if (wsRef.current === ws) wsRef.current = null;
      });
    },
    [cleanup, maxSamples],
  );

  useEffect(() => () => cleanup(), [cleanup]);

  return {
    state,
    error,
    samples,
    intervalS,
    paused,
    connect,
    disconnect,
    pause,
    resume,
  };
}
