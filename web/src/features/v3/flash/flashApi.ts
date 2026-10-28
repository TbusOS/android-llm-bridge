/**
 * Flash page helpers that api3.ts does not carry: one job's record, the
 * recorded timeline of a job, a workspace walk for manifests / images, and
 * small formatters. Shapes follow src/alb/api/flash_route.py.
 */
import { AlbApiError } from "../../../lib/api";
import { type FlashJobSummary, type NdjsonEvent, type PlanRow, fetchWorkspace } from "../../../lib/api3";

/** GET /api/flash/jobs/{id}: the summary plus job.json and the timeline path. */
export interface FlashJobDetail extends FlashJobSummary {
  job: {
    ok?: boolean;
    code?: string;
    error?: string;
    failed_line?: number;
    total?: number;
    ran?: number;
    skipped?: number;
    not_run?: number;
    duration_s?: number;
    stdout?: string;
    stderr?: string;
    manifest?: string;
    rows?: PlanRow[];
  } | null;
  timeline: string | null;
  timeline_path: string | null;
}

export async function fetchFlashJob(id: string, signal?: AbortSignal): Promise<FlashJobDetail> {
  const r = await fetch(`/api/flash/jobs/${encodeURIComponent(id)}`, { signal });
  if (!r.ok) throw new AlbApiError(`GET /api/flash/jobs/{id} returned ${r.status}`, r.status, `HTTP_${r.status}`);
  return (await r.json()) as FlashJobDetail;
}

export interface FeedLine {
  t: number;
  src: "job" | "uart" | "meta";
  text: string;
}

/** Turn timeline.jsonl text into display lines: job progress without text is noise. */
export function parseTimeline(text: string): FeedLine[] {
  const out: FeedLine[] = [];
  for (const raw of text.split("\n")) {
    if (!raw.trim()) continue;
    let o: Record<string, unknown>;
    try {
      o = JSON.parse(raw) as Record<string, unknown>;
    } catch {
      continue; // the last line may be half written while the job runs
    }
    const t = Number(o.t ?? 0);
    if (o.src === "uart") {
      out.push({ t, src: "uart", text: String(o.line ?? "") });
    } else if (o.src === "job") {
      if (o.ev === "done") {
        out.push({ t, src: "job", text: o.ok ? "done: ok" : `done: failed ${String(o.code ?? "")}`.trim() });
      } else if (o.text) {
        out.push({ t, src: "job", text: String(o.text) });
      }
    } else if (o.ev === "start") {
      out.push({ t, src: "meta", text: String(o.label ?? "start") });
    }
  }
  return out;
}

export async function fetchTimelineLines(path: string, signal?: AbortSignal): Promise<FeedLine[]> {
  const url = `/workspace/files/preview/${path.split("/").map(encodeURIComponent).join("/")}?max_bytes=262144`;
  const r = await fetch(url, { signal });
  if (!r.ok) throw new AlbApiError(`preview returned ${r.status}`, r.status, `HTTP_${r.status}`);
  const b = (await r.json()) as { ok?: boolean; text?: string };
  return b.ok ? parseTimeline(b.text ?? "") : [];
}

export interface WorkspaceFile {
  path: string;
  size: number;
  mtime: number;
}

const IMAGE_EXT = /\.(img|bin|elf|mbn|zip|gz|xz|tar|tgz|sparse|dtb|dtbo|sig|pem|der|so|apk)$/i;
export const looksLikeImage = (p: string) => IMAGE_EXT.test(p);

/**
 * Files under `root`, walking at most `depth` directory levels below it.
 * A directory that cannot be read is skipped, not fatal: a symlink to a
 * disk that is not mounted must not hide the rest.
 */
export async function walkWorkspace(root: string, depth = 2, signal?: AbortSignal): Promise<WorkspaceFile[]> {
  const out: WorkspaceFile[] = [];
  const visit = async (dir: string, left: number): Promise<void> => {
    let entries;
    try {
      entries = await fetchWorkspace(dir, signal);
    } catch {
      return;
    }
    const subdirs: string[] = [];
    for (const e of entries) {
      const p = dir ? `${dir}/${e.name}` : e.name;
      if (e.is_dir) subdirs.push(p);
      else out.push({ path: p, size: e.size, mtime: e.mtime_epoch });
    }
    if (left > 0) await Promise.all(subdirs.map((d) => visit(d, left - 1)));
  };
  await visit(root, depth);
  return out.sort((a, b) => a.path.localeCompare(b.path));
}

export function fmtBytes(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return "—";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v >= 100 || i === 0 ? Math.round(v) : v.toFixed(1)} ${u[i]}`;
}

export function fmtClock(epochSeconds: number): string {
  const d = new Date(epochSeconds * 1000);
  const p = (x: number) => String(x).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** `(bootloader) product:board_x` lines of fastboot getvar output. */
export function parseGetvar(text: string): Record<string, string> {
  const vars: Record<string, string> = {};
  for (const line of text.split(/\r?\n/)) {
    const m = /^(?:\(bootloader\)\s*)?([A-Za-z0-9][A-Za-z0-9._-]*):\s*(.*?)\s*$/.exec(line);
    if (m && m[1] && m[2] !== undefined && !(m[1] in vars)) vars[m[1]] = m[2];
  }
  return vars;
}

export function fmtMaxDownload(v: string | undefined): string {
  if (!v) return "—";
  const n = /^0x/i.test(v) ? parseInt(v, 16) : Number(v);
  return Number.isFinite(n) && n > 0 ? fmtBytes(n).replace("MB", "MiB").replace("GB", "GiB") : v;
}

/** Output of a finished job, stdout then stderr, as one block. */
export function outputOf(ev: NdjsonEvent): string {
  return [ev.stdout, ev.stderr].filter((x) => typeof x === "string" && x.trim()).join("\n");
}

/** Line numbers a plan problem talks about: "line 27", "(line 27, 28)". */
export function problemLines(problems: string[]): Set<number> {
  const s = new Set<number>();
  for (const p of problems) {
    for (const m of p.matchAll(/\blines? (\d+(?:,\s*\d+)*)/g)) {
      for (const n of (m[1] ?? "").split(",")) s.add(Number(n.trim()));
    }
  }
  return s;
}

/** Error code and message of a failed `done` line, whatever shape it came in. */
export function failureOf(ev: NdjsonEvent): { code: string; message: string } {
  const e = ev.error;
  if (e && typeof e === "object") {
    const o = e as { code?: string; message?: string };
    return { code: String(o.code ?? ""), message: String(o.message ?? "") };
  }
  return { code: String(ev.code ?? ""), message: typeof e === "string" ? e : String(ev.error ?? "") };
}
