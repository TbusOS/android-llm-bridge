/** Pure helpers for the Timeline page. */
import type { TimelineLine } from "../../../lib/api3";

const pad = (n: number, w = 2) => String(n).padStart(w, "0");

/** Wall-clock label for second `t` of a recording that started at `base` (ms). */
export function clock(base: number, t: number, ms = false): string {
  if (!Number.isFinite(base)) return `+${t.toFixed(ms ? 3 : 1)} s`;
  const d = new Date(base + t * 1000);
  const s = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  return ms ? `${s}.${pad(d.getMilliseconds(), 3)}` : s;
}

/** "10-28 09:30:58" style label for the recording picker. */
export function stamp(startedAt: string | null | undefined): string {
  const b = Date.parse(startedAt ?? "");
  if (!Number.isFinite(b)) return startedAt ?? "?";
  const d = new Date(b);
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

const PROBLEM_RE = /Kernel panic|Unable to handle kernel|Internal error: Oops|\bANR in |FATAL EXCEPTION|Fatal signal|avc:\s+denied/;

export function isProblem(l: TimelineLine): boolean {
  return l.level === "W" || l.level === "E" || l.level === "F" || PROBLEM_RE.test(l.text);
}

/** Tick positions (seconds) for an axis over [a, b], about `n` of them. */
export function ticks(a: number, b: number, n = 5): number[] {
  const span = b - a;
  if (span <= 0) return [a];
  const raw = span / n;
  const steps = [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600];
  const step = steps.find((s) => s >= raw) ?? 600;
  const out: number[] = [];
  for (let v = Math.ceil(a / step) * step; v <= b + 1e-9; v += step) out.push(Number(v.toFixed(3)));
  return out;
}

/** Index of the line closest in time to `t`, or -1. Lines are sorted by t. */
export function nearest(lines: readonly TimelineLine[], t: number): number {
  let best = -1;
  let bd = Infinity;
  for (let i = 0; i < lines.length; i++) {
    const d = Math.abs((lines[i] as TimelineLine).t - t);
    if (d < bd) {
      bd = d;
      best = i;
    }
  }
  return best;
}

export function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}
