/**
 * Derive a "currently in-progress chat session" view from the audit
 * event stream.
 *
 * Pure functional fold over an `AuditEvent[]` (newest-first, as
 * `useAuditStream` keeps it). Picks the chat session whose latest
 * event is most recent and which has not yet emitted a `done`
 * event. Returns `null` when nothing is running.
 *
 * Why no separate WS: the dashboard already subscribes to
 * `/audit/stream` for the timeline. Reducing the same stream into
 * a "current live session" view keeps one source of truth and
 * avoids second connection.
 */
import { useMemo } from "react";

import type { AuditEvent } from "../../lib/api";
import type { LiveSessionData, LiveToolCallData } from "./types";

interface ToolAccum extends LiveToolCallData {
  startedTs: number;
}

interface SessionAccum {
  sessionId: string;
  firstTs: number;
  lastTs: number;
  prompt: string;
  turn: number;
  tools: Map<string, ToolAccum>;
  toolsOrder: string[];
  done: boolean;
  errored: boolean;
  modelName: string;
  totalTokens: number;
  tps: number;
}

function tsMillis(ts: string): number {
  const n = new Date(ts).getTime();
  return Number.isNaN(n) ? Date.now() : n;
}

function emptyAccum(sessionId: string, ts: number): SessionAccum {
  return {
    sessionId,
    firstTs: ts,
    lastTs: ts,
    prompt: "",
    turn: 0,
    tools: new Map(),
    toolsOrder: [],
    done: false,
    errored: false,
    modelName: "",
    totalTokens: 0,
    tps: 0,
  };
}

export function reduceSessions(events: AuditEvent[]): Map<string, SessionAccum> {
  // useAuditStream keeps events newest-first; we fold oldest → newest
  // so accumulators see the chronological order.
  const ordered = [...events].reverse();
  const map = new Map<string, SessionAccum>();
  for (const e of ordered) {
    if (e.source !== "chat") continue;
    const ts = tsMillis(e.ts);
    let acc = map.get(e.session_id);
    if (!acc) {
      acc = emptyAccum(e.session_id, ts);
      map.set(e.session_id, acc);
    }
    if (ts > acc.lastTs) acc.lastTs = ts;

    const data = (e as { data?: Record<string, unknown> }).data ?? {};
    if (e.kind === "user") {
      acc.turn += 1;
      acc.prompt = e.summary;
    } else if (e.kind === "tool_call_start") {
      const id = String(data.id ?? `${e.kind}-${e.ts}-${acc.toolsOrder.length}`);
      acc.toolsOrder.push(id);
      acc.tools.set(id, {
        name: String(data.name ?? "?"),
        args: "",
        state: "running",
        elapsedSec: 0,
        startedTs: ts,
      });
    } else if (e.kind === "tool_call_end") {
      const id = String(data.id ?? "");
      const tool = id ? acc.tools.get(id) : undefined;
      if (tool) {
        tool.state = data.ok === false ? "err" : "done";
        tool.elapsedSec = (ts - tool.startedTs) / 1000;
      }
    } else if (e.kind === "done") {
      acc.done = true;
      const model = data.model;
      if (typeof model === "string" && model) acc.modelName = model;
      const usage = (data.usage as Record<string, number> | undefined) ?? {};
      const tokens = usage.total_tokens ?? usage.output_tokens ?? 0;
      acc.totalTokens += tokens;
      const elapsedMs = ts - acc.firstTs;
      acc.tps =
        elapsedMs > 0 && acc.totalTokens > 0
          ? Math.round((acc.totalTokens / elapsedMs) * 1000)
          : 0;
    } else if (e.kind === "error") {
      acc.errored = true;
      acc.done = true; // error terminates the session for "live" purposes
    }
  }
  return map;
}

/** Pick the most recent unfinished chat session, or null. */
export function selectActiveSession(
  map: Map<string, SessionAccum>,
): SessionAccum | null {
  let best: SessionAccum | null = null;
  for (const acc of map.values()) {
    if (acc.done) continue;
    if (best === null || acc.lastTs > best.lastTs) best = acc;
  }
  return best;
}

function formatElapsed(ms: number): string {
  const sec = Math.max(0, Math.floor(ms / 1000));
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}m ${s}s`;
}

export function toLiveSessionData(
  acc: SessionAccum,
  now = Date.now(),
): LiveSessionData {
  const elapsedHuman = formatElapsed(now - acc.firstTs);
  const tools: LiveToolCallData[] = acc.toolsOrder
    .map((id) => acc.tools.get(id))
    .filter((t): t is ToolAccum => t !== undefined)
    .map((t) => ({
      name: t.name,
      args: t.args,
      state: t.state,
      elapsedSec: t.elapsedSec,
    }));
  return {
    active: !acc.done,
    deviceId: acc.sessionId.slice(-6) || acc.sessionId,
    deviceTransport: "chat",
    turn: acc.turn,
    elapsedHuman,
    elapsedHumanZh: elapsedHuman,
    prompt: acc.prompt || "(no prompt yet)",
    promptZh: acc.prompt || "（暂无 prompt）",
    tools,
    tps: acc.tps,
    totalTokens: acc.totalTokens,
    modelName: acc.modelName || "?",
    tpsSpark: [],
  };
}

const IDLE_LIVE_SESSION: LiveSessionData = {
  active: false,
  deviceId: "",
  deviceTransport: "",
  turn: 0,
  elapsedHuman: "",
  elapsedHumanZh: "",
  prompt: "",
  promptZh: "",
  tools: [],
  tps: 0,
  totalTokens: 0,
  modelName: "",
  tpsSpark: [],
};

export function useLiveSession(rawEvents: AuditEvent[]): LiveSessionData {
  return useMemo(() => {
    const map = reduceSessions(rawEvents);
    const active = selectActiveSession(map);
    return active ? toLiveSessionData(active) : IDLE_LIVE_SESSION;
  }, [rawEvents]);
}
