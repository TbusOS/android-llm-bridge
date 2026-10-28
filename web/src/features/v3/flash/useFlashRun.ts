/**
 * One place that runs a job stream for the Flash page and keeps what the
 * right-hand "job and serial" card shows. The hub keeps a started job going
 * when this page goes away, so the stream is only the live view: the record
 * on disk (jobs list + timeline) is what a reload reads.
 */
import { useCallback, useRef, useState } from "react";

import type { NdjsonEvent } from "../../../lib/api3";
import type { FeedLine } from "./flashApi";

export interface RunState {
  running: boolean;
  label: string;
  /** Bytes progress of the current transfer, 0..1, or null when the agent gave no total. */
  frac: number | null;
  phase: string;
  lines: FeedLine[];
  done: NdjsonEvent | null;
}

export interface FlashRun extends RunState {
  start: (label: string, fn: (onEvent: (ev: NdjsonEvent) => void) => Promise<NdjsonEvent>, extra?: (ev: NdjsonEvent) => void) => Promise<NdjsonEvent>;
  reset: () => void;
}

const IDLE: RunState = { running: false, label: "", frac: null, phase: "", lines: [], done: null };
const MAX_LINES = 400;

export function useFlashRun(onFinished?: () => void): FlashRun {
  const [state, setState] = useState<RunState>(IDLE);
  const t0 = useRef(0);
  const finished = useRef(onFinished);
  finished.current = onFinished;

  const start: FlashRun["start"] = useCallback(async (label, fn, extra) => {
    t0.current = Date.now();
    setState({ ...IDLE, running: true, label });
    const onEvent = (ev: NdjsonEvent) => {
      extra?.(ev);
      const t = (Date.now() - t0.current) / 1000;
      if (ev.ev === "progress") {
        const total = Number(ev.total ?? 0);
        const doneBytes = Number(ev.done ?? 0);
        const text = String(ev.text ?? "");
        const phase = String(ev.phase ?? "");
        setState((s) => {
          const lines = text ? [...s.lines, { t, src: "job" as const, text }].slice(-MAX_LINES) : s.lines;
          // step / skip / wait events count rows, not bytes: keep the last byte fraction.
          const isBytes = phase === "transfer" || phase === "flash";
          return { ...s, phase, lines, frac: isBytes && total > 0 ? Math.min(1, doneBytes / total) : phase === "step" ? null : s.frac };
        });
      } else if (ev.ev === "done") {
        setState((s) => ({
          ...s,
          running: false,
          done: ev,
          frac: ev.ok ? 1 : s.frac,
          lines: [...s.lines, { t, src: "job" as const, text: ev.ok ? "done: ok" : `done: failed ${String(ev.code ?? (ev.error as { code?: string } | undefined)?.code ?? "")}`.trim() }].slice(-MAX_LINES),
        }));
      }
    };
    let result: NdjsonEvent;
    try {
      result = await fn(onEvent);
    } catch (e) {
      result = { ev: "done", ok: false, error: { code: "NETWORK", message: e instanceof Error ? e.message : String(e) } };
      onEvent(result);
    }
    setState((s) => (s.running ? { ...s, running: false, done: result } : s));
    finished.current?.();
    return result;
  }, []);

  const reset = useCallback(() => setState(IDLE), []);
  return { ...state, start, reset };
}
