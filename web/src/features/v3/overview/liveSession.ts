/**
 * Fold the audit stream (business + tps_sample events, newest first, as
 * useAuditStream keeps them) into the chat session that is running right
 * now: turn, prompt, tool calls, tokens per second, model.
 *
 * Only `source === "chat"` counts: terminal and playground events feed
 * other cards. A session is running until it emits `done` or `error`.
 */
import type { AuditEvent } from "../../../lib/api";

export interface LiveTool {
  id: string;
  name: string;
  state: "running" | "done" | "err";
  startedMs: number;
  /** Seconds the call took; 0 while it is still running. */
  tookS: number;
}

export interface LiveSession {
  sessionId: string;
  startedMs: number;
  lastMs: number;
  prompt: string;
  turn: number;
  tools: LiveTool[];
  /** Latest sampled tokens per second. */
  tps: number;
  /** Last SPARK_WINDOW samples, oldest first (about 60 s at 1 Hz). */
  tpsSamples: number[];
  totalTokens: number;
  /** Known once the turn finishes (`done`); until then only the backend is. */
  model: string;
  backend: string;
  done: boolean;
}

const SPARK_WINDOW = 60;
/** A session that has said nothing for this long crashed or was killed
 *  without a `done`; showing it as running would be a lie. Long tool
 *  calls (a bugreport) stay well under this. */
export const LIVE_IDLE_MS = 10 * 60_000;

function ms(ts: string): number {
  const n = Date.parse(ts);
  return Number.isNaN(n) ? Date.now() : n;
}

export function reduceSessions(events: readonly AuditEvent[]): Map<string, LiveSession> {
  const map = new Map<string, LiveSession>();
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const e = events[i];
    if (!e || e.source !== "chat") continue;
    const ts = ms(e.ts);
    let s = map.get(e.session_id);
    if (!s) {
      s = { sessionId: e.session_id, startedMs: ts, lastMs: ts, prompt: "", turn: 0, tools: [], tps: 0, tpsSamples: [], totalTokens: 0, model: "", backend: "", done: false };
      map.set(e.session_id, s);
    }
    if (ts > s.lastMs) s.lastMs = ts;
    const data = e.data ?? {};
    if (e.kind === "user") {
      s.turn += 1;
      s.prompt = e.summary;
    } else if (e.kind === "tool_call_start") {
      s.tools.push({ id: String(data.id ?? `${e.ts}-${s.tools.length}`), name: String(data.name ?? "?"), state: "running", startedMs: ts, tookS: 0 });
    } else if (e.kind === "tool_call_end") {
      const tool = s.tools.find((x) => x.id === String(data.id ?? ""));
      if (tool) {
        tool.state = data.ok === false ? "err" : "done";
        tool.tookS = (ts - tool.startedMs) / 1000;
      }
    } else if (e.kind === "tps_sample") {
      // One sampler per session; total_tokens only grows, so trust it.
      const rate = typeof data.rate_per_s === "number" && Number.isFinite(data.rate_per_s) ? Math.max(0, data.rate_per_s) : 0;
      s.tpsSamples.push(rate);
      if (s.tpsSamples.length > SPARK_WINDOW) s.tpsSamples.splice(0, s.tpsSamples.length - SPARK_WINDOW);
      s.tps = rate;
      if (typeof data.total_tokens === "number" && Number.isFinite(data.total_tokens)) s.totalTokens = data.total_tokens;
      if (typeof data.backend === "string" && data.backend) s.backend = data.backend;
    } else if (e.kind === "done") {
      s.done = true;
      if (typeof data.model === "string" && data.model) s.model = data.model;
      const usage = (data.usage as Record<string, number> | undefined) ?? {};
      const tokens = usage.total_tokens ?? usage.output_tokens ?? 0;
      if (tokens > s.totalTokens) s.totalTokens = tokens;
    } else if (e.kind === "error") {
      s.done = true;
    }
  }
  return map;
}

/** The most recently active unfinished session, or null. */
export function activeSession(events: readonly AuditEvent[], now = Date.now()): LiveSession | null {
  let best: LiveSession | null = null;
  for (const s of reduceSessions(events).values()) {
    if (s.done || now - s.lastMs > LIVE_IDLE_MS) continue;
    if (!best || s.lastMs > best.lastMs) best = s;
  }
  return best;
}
