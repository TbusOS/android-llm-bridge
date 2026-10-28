/**
 * Live logcat model for the Logs page: the byte stream from
 * /logcat/stream (reused hook from the v2 Inspect tab) is split into
 * `logcat -v threadtime` entries, kept in a bounded buffer, counted into
 * 2-second bins for the histogram, and filtered on the client.
 *
 * Two filters exist on purpose. The level / tag / text filter runs here in
 * the browser and changes nothing on the wire. The board filter (`spec`,
 * e.g. `Tag:V *:S` or `*:E`) is handed to `adb logcat` itself, so lines it
 * drops never reach the bounded buffer; on a noisy board that is the only
 * way to keep a long history of what matters.
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

/* ---------------- board filter spec ---------------- */

/** One `<TAG>:<LEVEL>` token, the rule /logcat/stream applies before it starts adb. */
const SPEC_TOKEN = /^[A-Za-z0-9_.*-]+:[VDIWEFS]$/i;

/** Tokens of a logcat filter spec the server would refuse; empty when the spec is fine. */
export function badSpecTokens(spec: string): string[] {
  return spec.split(/\s+/).filter((tok) => tok && !SPEC_TOKEN.test(tok));
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
  // The rows on screen while paused. A copy, not a window into `entries`:
  // the live buffer keeps dropping its oldest rows, and a window would
  // lose exactly the rows being read.
  const frozen = useRef<LogEntry[] | null>(null);
  const [paused, setPaused] = useState(false);
  const [tick, setTick] = useState(0);
  const [received, setReceived] = useState(0);
  const total = useRef(0);
  const [spec, setSpec] = useState("");
  const specRef = useRef("");

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

  /** Empty the buffer, the histogram and the pause snapshot. */
  const reset = useCallback(() => {
    entries.current = [];
    bins.current.clear();
    stamps.current = [];
    total.current = 0;
    pausedAt.current = null;
    frozen.current = null;
    setPaused(false);
    setReceived(0);
    setTick((n) => n + 1);
  }, []);

  const [stopped, setStopped] = useState(false);
  useEffect(() => {
    if (!device) {
      disconnect();
      return;
    }
    reset();
    setStopped(false);
    connect({ device, filter: specRef.current || null });
    return () => disconnect();
  }, [device, connect, disconnect, reset]);

  const pause = useCallback(() => {
    pausedAt.current = nextId.current;
    frozen.current = entries.current.slice();
    setPaused(true);
  }, []);
  const resume = useCallback(() => {
    pausedAt.current = null;
    frozen.current = null;
    setPaused(false);
    setTick((n) => n + 1);
  }, []);
  /** Clear the view; the stream keeps running, so only lines from now on show. */
  const clear = useCallback(() => {
    entries.current = [];
    total.current = 0;
    if (frozen.current) {
      frozen.current = [];
      pausedAt.current = nextId.current;
    }
    setReceived(0);
    setTick((n) => n + 1);
  }, []);
  /**
   * (Re)open the stream from an empty view. `adb logcat` without `-T`
   * starts by sending everything still in the board's ring buffer, so
   * keeping the old rows would show each of them twice.
   */
  const start = useCallback(() => {
    if (!device) return;
    reset();
    setStopped(false);
    connect({ device, filter: specRef.current || null });
  }, [device, connect, reset]);
  const stop = useCallback(() => {
    setStopped(true);
    disconnect();
  }, [disconnect]);
  /** Change the filter handed to `adb logcat`; restarts the stream when one is wanted. */
  const applySpec = useCallback(
    (next: string) => {
      const s = next.trim().split(/\s+/).filter(Boolean).join(" ");
      specRef.current = s;
      setSpec(s);
      if (device && !stopped) start();
    },
    [device, stopped, start],
  );

  // `tick` is the change signal for the refs read below.
  const visible = useMemo(() => {
    void tick;
    return frozen.current ?? entries.current.slice();
  }, [tick, paused]);

  // Lines that arrived since the pause, and how many of them the live
  // buffer still holds (the rest were pushed out by newer ones).
  const arrived = paused && pausedAt.current !== null ? Math.max(0, nextId.current - pausedAt.current) : 0;
  const buffered = Math.min(arrived, entries.current.length);
  const dropped = arrived - buffered;

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

  return { ...stream, stopped, visible, received, paused, buffered, dropped, pause, resume, clear, start, stop, spec, applySpec, histogram, perMinute };
}
