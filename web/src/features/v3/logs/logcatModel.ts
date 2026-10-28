/**
 * Live logcat model for the Logs page: the byte stream from
 * /logcat/stream (reused hook from the v2 Inspect tab) is split into
 * `logcat -v threadtime` entries, kept in a bounded buffer, counted into
 * 2-second bins for the histogram, and filtered on the client.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useLogcatStream } from "../../inspect/useLogcatStream";

export type Level = "V" | "D" | "I" | "W" | "E" | "F";
export const LEVELS: readonly Level[] = ["V", "D", "I", "W", "E", "F"];

export interface LogEntry {
  id: number;
  ts: string;
  pid: string;
  tid: string;
  level: Level;
  tag: string;
  msg: string;
  /** Lines that followed without a threadtime header (backtraces, wraps). */
  cont: string[];
}

export const BUFFER_CAP = 5000;
export const BIN_MS = 2000;
export const BINS = 150;

const THREADTIME = /^(\d\d-\d\d \d\d:\d\d:\d\d\.\d{3})\s+(\d+)\s+(\d+)\s+([VDIWEF])\s+(.*?)\s*:\s?(.*)$/;

export function parseThreadtime(line: string): Omit<LogEntry, "id" | "cont"> | null {
  const m = THREADTIME.exec(line);
  if (!m) return null;
  return { ts: m[1] as string, pid: m[2] as string, tid: m[3] as string, level: m[4] as Level, tag: m[5] as string, msg: m[6] as string };
}

/** Joins chunks into whole lines; the unfinished tail waits for the next chunk. */
export class LineSplitter {
  private dec = new TextDecoder();
  private tail = "";
  push(chunk: ArrayBuffer): string[] {
    const text = this.tail + this.dec.decode(chunk, { stream: true });
    const parts = text.split(/\r?\n/);
    this.tail = parts.pop() ?? "";
    return parts;
  }
}

/* ---------------- query ---------------- */

export interface Query {
  tags: string[];
  pids: string[];
  text: string;
}

/** `tag:Sensor* pid:1234 some words` -> parts. */
export function parseQuery(q: string): Query {
  const tags: string[] = [];
  const pids: string[] = [];
  const rest: string[] = [];
  for (const tok of q.split(/\s+/).filter(Boolean)) {
    if (tok.startsWith("tag:") && tok.length > 4) tags.push(tok.slice(4));
    else if (tok.startsWith("pid:") && tok.length > 4) pids.push(tok.slice(4));
    else rest.push(tok);
  }
  return { tags, pids, text: rest.join(" ") };
}

function glob(p: string): RegExp {
  return new RegExp(`^${p.replace(/[.+?^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*")}$`, "i");
}

export interface Matcher {
  test: (e: LogEntry) => boolean;
  /** Set when the regex toggle is on and the text is not a valid pattern. */
  badRegex: boolean;
}

export function makeMatcher(q: string, levels: ReadonlySet<Level>, regex: boolean): Matcher {
  const p = parseQuery(q);
  const tagRes = p.tags.map(glob);
  let textRe: RegExp | null = null;
  let bad = false;
  if (p.text) {
    try {
      textRe = new RegExp(regex ? p.text : p.text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i");
    } catch {
      bad = true;
    }
  }
  return {
    badRegex: bad,
    test: (e) => {
      if (!levels.has(e.level)) return false;
      if (tagRes.length && !tagRes.some((r) => r.test(e.tag))) return false;
      if (p.pids.length && !p.pids.includes(e.pid)) return false;
      if (textRe && !textRe.test(`${e.tag} ${e.msg}`)) return false;
      return true;
    },
  };
}

/* ---------------- hook ---------------- */

export interface Bin {
  d: number;
  i: number;
  w: number;
  e: number;
}

export function useLogcat(device: string | null) {
  const stream = useLogcatStream();
  const { onBytes, connect, disconnect } = stream;
  const entries = useRef<LogEntry[]>([]);
  const nextId = useRef(1);
  const bins = useRef(new Map<number, Bin>());
  const stamps = useRef<number[]>([]);
  const dirty = useRef(false);
  const pausedAt = useRef<number | null>(null);
  const [paused, setPaused] = useState(false);
  const [tick, setTick] = useState(0);
  const [received, setReceived] = useState(0);
  const total = useRef(0);

  useEffect(() => {
    const split = new LineSplitter();
    return onBytes((chunk) => {
      const now = Date.now();
      for (const line of split.push(chunk)) {
        if (!line.trim()) continue;
        const p = parseThreadtime(line);
        if (p) {
          entries.current.push({ id: nextId.current++, cont: [], ...p });
          total.current++;
          stamps.current.push(now);
          const k = Math.floor(now / BIN_MS);
          const b = bins.current.get(k) ?? { d: 0, i: 0, w: 0, e: 0 };
          if (p.level === "V" || p.level === "D") b.d++;
          else if (p.level === "I") b.i++;
          else if (p.level === "W") b.w++;
          else b.e++;
          bins.current.set(k, b);
        } else if (!line.startsWith("--------- ")) {
          const last = entries.current[entries.current.length - 1];
          if (last && last.cont.length < 200) last.cont.push(line);
        }
        dirty.current = true;
      }
      if (entries.current.length > BUFFER_CAP + 500) entries.current.splice(0, entries.current.length - BUFFER_CAP);
      if (stamps.current.length > 20000) stamps.current.splice(0, stamps.current.length - 10000);
    });
  }, [onBytes]);

  useEffect(() => {
    const id = window.setInterval(() => {
      if (!dirty.current) return;
      dirty.current = false;
      const cut = Math.floor(Date.now() / BIN_MS) - BINS - 2;
      for (const k of bins.current.keys()) if (k < cut) bins.current.delete(k);
      setReceived(total.current);
      setTick((n) => n + 1);
    }, 250);
    return () => window.clearInterval(id);
  }, []);

  const [stopped, setStopped] = useState(false);
  useEffect(() => {
    if (!device) {
      disconnect();
      return;
    }
    entries.current = [];
    bins.current.clear();
    stamps.current = [];
    total.current = 0;
    setReceived(0);
    setStopped(false);
    connect({ device });
    return () => disconnect();
  }, [device, connect, disconnect]);

  const pause = useCallback(() => {
    pausedAt.current = nextId.current;
    setPaused(true);
  }, []);
  const resume = useCallback(() => {
    pausedAt.current = null;
    setPaused(false);
    setTick((n) => n + 1);
  }, []);
  const clear = useCallback(() => {
    entries.current = [];
    setTick((n) => n + 1);
  }, []);
  const reconnect = useCallback(() => {
    if (!device) return;
    setStopped(false);
    connect({ device });
  }, [device, connect]);
  const stop = useCallback(() => {
    setStopped(true);
    disconnect();
  }, [disconnect]);

  // `tick` is the change signal for the refs read below.
  const visible = useMemo(() => {
    void tick;
    const all = entries.current;
    const lim = pausedAt.current;
    if (lim === null) return all.slice();
    let end = all.length;
    while (end > 0 && (all[end - 1] as LogEntry).id >= lim) end--;
    return all.slice(0, end);
  }, [tick, paused]);

  const buffered = paused && pausedAt.current !== null ? Math.max(0, nextId.current - pausedAt.current) : 0;

  const histogram = useMemo(() => {
    void tick;
    const nowK = Math.floor(Date.now() / BIN_MS);
    return Array.from({ length: BINS }, (_, i) => bins.current.get(nowK - BINS + 1 + i) ?? { d: 0, i: 0, w: 0, e: 0 });
  }, [tick]);

  const perMinute = useMemo(() => {
    void tick;
    const since = Date.now() - 60_000;
    let n = 0;
    for (let i = stamps.current.length - 1; i >= 0 && (stamps.current[i] as number) >= since; i--) n++;
    return n;
  }, [tick]);

  return { ...stream, stopped, visible, received, paused, buffered, pause, resume, clear, reconnect, stop, histogram, perMinute };
}
