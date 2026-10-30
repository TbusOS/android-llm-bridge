/**
 * API calls used by the v3 pages. Shapes follow the hub routes
 * (src/alb/api/*_route.py); endpoints that stream answer NDJSON — one JSON
 * object per line, progress first and a final `{"ev": "done"}` verdict.
 */
import { AlbApiError } from "./api";

/* ---------------- helpers ---------------- */

function q(params: Record<string, string | number | boolean | null | undefined>): string {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === null || v === undefined || v === "") continue;
    u.set(k, String(v));
  }
  const s = u.toString();
  return s ? `?${s}` : "";
}

async function getJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const r = await fetch(url, { signal });
  if (!r.ok) {
    let detail = "";
    try {
      const b = (await r.json()) as { detail?: unknown };
      detail = typeof b.detail === "string" ? b.detail : JSON.stringify(b.detail ?? "");
    } catch {
      /* body was not JSON */
    }
    throw new AlbApiError(`GET ${url.split("?")[0]} returned ${r.status}${detail ? `: ${detail}` : ""}`, r.status, `HTTP_${r.status}`);
  }
  return (await r.json()) as T;
}

async function postJson<T>(url: string, body?: unknown): Promise<T> {
  const r = await fetch(url, {
    method: "POST",
    headers: body === undefined ? undefined : { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let parsed: unknown = null;
  try {
    parsed = await r.json();
  } catch {
    /* empty body */
  }
  if (!r.ok && !(parsed && typeof parsed === "object" && "ok" in parsed)) {
    const detail = (parsed as { detail?: unknown } | null)?.detail;
    return { ok: false, error: { code: `HTTP_${r.status}`, message: typeof detail === "string" ? detail : JSON.stringify(detail ?? "") } } as T;
  }
  return parsed as T;
}

export interface Envelope<T> {
  ok: boolean;
  data?: T;
  error?: { code: string; message?: string; suggestion?: string; details?: Record<string, unknown> };
  timing_ms?: number;
}

export type NdjsonEvent = Record<string, unknown> & { ev?: string };

/**
 * POST and read an NDJSON stream. `onEvent` sees every line; the promise
 * resolves with the `done` line (or a synthetic failure if the stream ends
 * without one). The hub keeps a started job running if the page goes away.
 */
export async function postNdjson(
  url: string,
  body: unknown,
  onEvent: (ev: NdjsonEvent) => void,
  signal?: AbortSignal,
): Promise<NdjsonEvent> {
  const r = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body ?? {}),
    signal,
  });
  const type = r.headers.get("content-type") ?? "";
  if (!r.body || !type.includes("ndjson")) {
    // A request refused before the job started comes back as plain JSON.
    let parsed: NdjsonEvent = { ev: "done", ok: false };
    try {
      const b = (await r.json()) as Record<string, unknown>;
      const detail = b.detail as Record<string, unknown> | string | undefined;
      parsed =
        "ok" in b
          ? { ev: "done", ...b }
          : {
              ev: "done",
              ok: false,
              error: typeof detail === "object" && detail ? { code: String(detail.code ?? `HTTP_${r.status}`), message: JSON.stringify(detail.problems ?? detail) } : { code: `HTTP_${r.status}`, message: String(detail ?? "") },
            };
    } catch {
      parsed = { ev: "done", ok: false, error: { code: `HTTP_${r.status}`, message: "" } };
    }
    onEvent(parsed);
    return parsed;
  }
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let done: NdjsonEvent | null = null;
  for (;;) {
    const { done: end, value } = await reader.read();
    if (end) break;
    buf += decoder.decode(value, { stream: true });
    const parts = buf.split("\n");
    buf = parts.pop() ?? "";
    for (const part of parts) {
      if (!part.trim()) continue;
      let msg: NdjsonEvent;
      try {
        msg = JSON.parse(part) as NdjsonEvent;
      } catch {
        continue;
      }
      onEvent(msg);
      if (msg.ev === "done") done = msg;
    }
  }
  return done ?? { ev: "done", ok: false, error: { code: "STREAM_ENDED", message: "the stream ended without a result" } };
}

/* ---------------- bench ---------------- */

export interface BenchBoard {
  name: string;
  serial_com: string;
  serial_baud: number;
  note: string;
}

export interface BenchBoardView {
  ok: boolean;
  table: string | null;
  boards: BenchBoard[];
  active: string | null;
  source: string;
  problem: string | null;
  serial: { com: string | null; baud: number; bound: boolean; readers: number };
  code?: string;
  error?: string;
}

export const fetchBenchBoard = (signal?: AbortSignal) => getJson<BenchBoardView>("/api/bench/board", signal);
export const selectBenchBoard = (name: string, force = false) => postJson<BenchBoardView>("/api/bench/board", { name, force });

export interface OpParam {
  name: string;
  kind: string;
  choices?: string[];
  required?: boolean;
}
export interface OpSpecView {
  name: string;
  group: string;
  summary: string;
  params: OpParam[];
  needs: string;
  image: boolean;
  mutates: boolean;
  exclusive: boolean;
  timeout_s: number;
}
export interface BenchOpsView {
  ok: boolean;
  agent: { id: string; name: string; build: string } | null;
  bundle_build: string;
  reported: boolean;
  ops: OpSpecView[];
}
export const fetchBenchOps = (signal?: AbortSignal) => getJson<BenchOpsView>("/api/bench/ops", signal);

export interface UsbDeviceView {
  mode: string;
  what: string;
  driver: string;
  hint: string;
  vid: string;
  pid: string;
  serial: string;
  windows_id: string;
  name: string;
  problem?: string;
}

export function runBenchOp(op: string, params: Record<string, unknown>, onEvent: (ev: NdjsonEvent) => void) {
  return postNdjson(`/api/bench/ops/${encodeURIComponent(op)}`, { params }, onEvent);
}
export const benchAgentUpdate = (onEvent: (ev: NdjsonEvent) => void) => postNdjson("/api/bench/agent-update", {}, onEvent);
export const benchAgentStart = (onEvent: (ev: NdjsonEvent) => void) => postNdjson("/api/bench/agent-start", {}, onEvent);

/* ---------------- flash ---------------- */

/** Why flashing is unavailable (null when it is available). `code` is the
 *  one a refused job carries; `cause` picks the fix. */
export interface FlashUnavailable {
  code: string;
  cause: "no_agent" | "no_fastboot" | string;
  message: string;
  suggestion?: string;
}

export interface FlashStatusView {
  ok: boolean;
  available: boolean;
  unavailable?: FlashUnavailable | null;
  busy: boolean;
  job: string | null;
  partitions: string[];
  partitions_problem: string;
  board: string | null;
  [k: string]: unknown;
}
export const fetchFlashStatus = (signal?: AbortSignal) => getJson<FlashStatusView>("/api/flash/status", signal);

export interface FlashJobSummary {
  id: string;
  op: string;
  label: string;
  started_at: string | null;
  ok: boolean | null;
  code: string | null;
  failed_line: number | null;
  rows: { total: number; ok: number; failed: number; skipped: number; not_run: number };
}
export interface FlashJobsView {
  ok: boolean;
  running: string | null;
  jobs: FlashJobSummary[];
}
export const fetchFlashJobs = (limit = 20, signal?: AbortSignal) => getJson<FlashJobsView>(`/api/flash/jobs${q({ limit })}`, signal);

export interface PlanRow {
  line: number;
  action: string;
  partition?: string;
  file?: string;
  key?: string;
  value?: string;
  command?: string;
  size?: number;
  sha256?: string;
  md5?: string;
  skip?: string;
  run?: boolean;
  status?: string;
  duration_s?: number;
  code?: string;
  error?: string;
}
export interface PlanView {
  ok: boolean;
  manifest: string;
  rows: PlanRow[];
  problems: string[];
  fingerprint: string;
  total?: number;
  ran?: number;
  skipped?: number;
  [k: string]: unknown;
}
/** `fromLine` > 0 resumes a plan that stopped: rows before that manifest line are skipped (getvar checks still run). */
const planBody = (manifest: string, skip: string[], skipActions: string[], fromLine: number) => ({
  manifest,
  skip,
  skip_actions: skipActions,
  ...(fromLine > 0 ? { from_line: fromLine } : {}),
});
export const checkPlan = (manifest: string, skip: string[], skipActions: string[], fromLine = 0) =>
  postJson<PlanView>("/api/flash/plan", planBody(manifest, skip, skipActions, fromLine));
export const runPlan = (manifest: string, skip: string[], skipActions: string[], fingerprint: string, onEvent: (ev: NdjsonEvent) => void, fromLine = 0) =>
  postNdjson("/api/flash/plan/run", { ...planBody(manifest, skip, skipActions, fromLine), fingerprint }, onEvent);
export const flashOp = (path: "flash" | "getvar" | "oem" | "reboot" | "devices", body: unknown, onEvent: (ev: NdjsonEvent) => void) =>
  postNdjson(`/api/flash/${path}`, body, onEvent);

/* ---------------- workspace ---------------- */

/** One entry of GET /workspace/files (files_route._scan_workspace_dir). */
export interface WorkspaceEntry {
  name: string;
  is_dir: boolean;
  is_link: boolean;
  size: number;
  mtime_epoch: number;
}
export async function fetchWorkspace(path: string, signal?: AbortSignal): Promise<WorkspaceEntry[]> {
  const r = await getJson<{ ok: boolean; entries: WorkspaceEntry[]; error?: string }>(`/workspace/files${q({ path })}`, signal);
  return r.ok ? r.entries : [];
}

/* ---------------- timeline ---------------- */

export interface CaptureSummary {
  name: string;
  /** "capture" = a timeline recording; "flash" = a flash job's timeline. */
  kind?: "capture" | "flash";
  /** flash jobs: what ran ("reboot normal", "flash cfg") and its verdict (null = none on disk). */
  label?: string;
  ok?: boolean | null;
  started_at: string;
  duration_s: number;
  sources: { logcat?: number; dmesg?: number; uart?: number; job?: number };
  size_bytes: number;
  has_boot: boolean;
}
export interface TimelineLine {
  t: number;
  ts: string;
  src: "logcat" | "dmesg" | "uart" | string;
  text: string;
  level: "V" | "D" | "I" | "W" | "E" | "F" | null;
  tag: string | null;
  pid: number | null;
}
export interface DensityView {
  t0: number;
  t1: number;
  bucket_s: number;
  tracks: { uart: number[]; dmesg: number[]; logcat: Record<"V" | "D" | "I" | "W" | "E" | "F", number[]> };
  markers: { t: number; src: string; kind: string; text: string }[];
}
export interface BootMilestone {
  key: string;
  label: string;
  src: string;
  t: number;
}
export interface BootView {
  found: boolean;
  total_s: number;
  milestones: BootMilestone[];
  stages: { key: string; src: string; t0: number; t1: number }[];
  previous: { name: string; total_s: number; milestones: BootMilestone[] } | null;
}

export const listCaptures = (device: string | null, signal?: AbortSignal) =>
  getJson<Envelope<CaptureSummary[]>>(`/api/timeline/captures${q({ device, limit: 50 })}`, signal);
export const captureLines = (
  name: string,
  p: { device: string | null; sources?: string; grep?: string; t0?: number; t1?: number; limit?: number; offset?: number },
  signal?: AbortSignal,
) => getJson<Envelope<{ lines: TimelineLine[]; truncated: boolean }>>(`/api/timeline/captures/${encodeURIComponent(name)}/lines${q(p)}`, signal);
export const captureDensity = (name: string, device: string | null, bucket_s: number, signal?: AbortSignal) =>
  getJson<Envelope<DensityView>>(`/api/timeline/captures/${encodeURIComponent(name)}/density${q({ device, bucket_s })}`, signal);
export const captureBoot = (name: string, device: string | null, signal?: AbortSignal) =>
  getJson<Envelope<BootView>>(`/api/timeline/captures/${encodeURIComponent(name)}/boot${q({ device })}`, signal);
export const startCapture = (
  device: string | null,
  body: { duration_s: number; sources?: string[]; reboot?: "" | "system" | "bootloader" },
  onEvent: (ev: NdjsonEvent) => void,
) => postNdjson(`/api/timeline/capture${q({ device })}`, body, onEvent);

/* ---------------- diagnostics / adb / serial ---------------- */

export interface PstoreRecord {
  name: string;
  size: number;
  kind: string;
  summary: string;
  has_panic: boolean;
  saved_path: string;
  error: string;
}
export const pullPstore = (device: string | null) =>
  postJson<Envelope<{ source: string; pstore_present: boolean; last_kmsg_present: boolean; saved_dir: string; note: string; records: PstoreRecord[] }>>(
    `/api/diag/pstore${q({ device })}`,
  );
export const adbAction = (action: "root" | "unroot" | "remount", device: string | null) =>
  postJson<Envelope<Record<string, unknown>>>(`/api/adb/${action}${q({ device })}`);
export const breakIn = (device: string | null, reboot: "" | "auto" | "adb" | "serial", onEvent: (ev: NdjsonEvent) => void) =>
  postNdjson(`/api/serial/break-in${q({ device })}`, { reboot, timeout_s: 120 }, onEvent);

/* ---------------- assistant ---------------- */

export interface OnDeviceProbe {
  port: number;
  ok: boolean;
  kind: "openai" | "ollama" | null;
  models: string[];
  error: string | null;
}
export const probeOnDevice = (device: string | null, ports = [8080, 8000, 11434]) =>
  postJson<Envelope<OnDeviceProbe[]>>(`/api/assistant/ondevice/probe${q({ device })}`, { ports });
