/**
 * alb REST / WS client helpers.
 *
 * Uses a single base URL — Vite dev proxies /api / /chat etc. to the
 * running alb-api; the production build hits the FastAPI mount on the
 * same origin, so relative URLs work.
 */

export class AlbApiError extends Error {
  status?: number;
  code?: string;
  constructor(message: string, status?: number, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

/**
 * Parse a JSON body the endpoint whitelists as an `{ ok, ... }`
 * envelope even on 4xx/503.  FastAPI request-validation rejects (422)
 * bypass the envelope wrapper and return a bare `{ detail }` body —
 * passing those through as-is renders an empty "ERROR: " in the UI.
 * Bodies without an `ok` field are normalised into a synthetic error
 * envelope carrying the HTTP status and the detail text.
 */
export async function parseEnvelope<T extends { ok: boolean }>(
  r: Response,
): Promise<T> {
  const body: unknown = await r.json();
  if (body !== null && typeof body === "object" && "ok" in body) {
    return body as T;
  }
  const detail = (body as { detail?: unknown } | null)?.detail;
  return {
    ok: false,
    error: {
      code: `HTTP_${r.status}`,
      message:
        typeof detail === "string" ? detail : JSON.stringify(detail ?? ""),
    },
  } as unknown as T;
}

export interface ApiEndpoint {
  method: string;
  path: string;
  description?: string;
}

export interface WsEndpoint {
  path: string;
  description?: string;
  messages?: { type: string; direction?: string; description?: string }[];
}

export interface ApiVersion {
  version: string;
  alb_version: string;
  rest: ApiEndpoint[];
  ws: WsEndpoint[];
  reference?: string;
}

export async function fetchApiVersion(signal?: AbortSignal): Promise<ApiVersion> {
  const r = await fetch("/api/version", { signal });
  if (!r.ok) {
    throw new AlbApiError(
      `GET /api/version returned ${r.status}`,
      r.status,
      "API_VERSION_FAILED",
    );
  }
  return (await r.json()) as ApiVersion;
}

export interface SessionSummary {
  session_id: string;
  created: string | null;
  backend: string;
  model: string;
  device: string | null;
  turns: number;
  last_event_ts: string | null;
}

export interface SessionsResponse {
  ok: boolean;
  sessions: SessionSummary[];
}

export interface SessionMessage {
  role: "user" | "assistant" | "tool" | string;
  content?: string;
  tool_calls?: Array<{
    id?: string;
    name?: string;
    arguments?: Record<string, unknown> | string;
  }>;
  tool_call_id?: string;
  name?: string;
}

export interface SessionDetailResponse {
  ok: boolean;
  session_id: string;
  created: string | null;
  backend: string;
  model: string;
  device: string | null;
  turns: number;
  last_event_ts: string | null;
  messages: SessionMessage[];
}

export async function fetchSessionDetail(
  sessionId: string,
  signal?: AbortSignal,
): Promise<SessionDetailResponse> {
  const r = await fetch(`/sessions/${encodeURIComponent(sessionId)}`, { signal });
  if (!r.ok) {
    throw new AlbApiError(
      `GET /sessions/${sessionId} returned ${r.status}`,
      r.status,
      r.status === 404 ? "SESSION_NOT_FOUND" : "SESSION_FETCH_FAILED",
    );
  }
  return (await r.json()) as SessionDetailResponse;
}

export async function fetchSessions(
  limit = 20,
  signal?: AbortSignal,
): Promise<SessionsResponse> {
  const r = await fetch(`/sessions?limit=${limit}`, { signal });
  if (!r.ok) {
    throw new AlbApiError(
      `GET /sessions returned ${r.status}`,
      r.status,
      "SESSIONS_FETCH_FAILED",
    );
  }
  return (await r.json()) as SessionsResponse;
}

export interface ApiDevice {
  serial: string;
  state: string; // "device" | "offline" | "unauthorized" | ...
  product?: string;
  model?: string;
  transport_id?: string;
}

export interface DevicesResponse {
  ok: boolean;
  transport: string | null; // class name e.g. "AdbTransport"; null on factory failure
  devices: ApiDevice[];
  error?: string; // present when ok=false
}

export async function fetchDevices(signal?: AbortSignal): Promise<DevicesResponse> {
  const r = await fetch(`/devices`, { signal });
  if (!r.ok) {
    throw new AlbApiError(
      `GET /devices returned ${r.status}`,
      r.status,
      "DEVICES_FETCH_FAILED",
    );
  }
  return (await r.json()) as DevicesResponse;
}

export interface ApiDeviceDetailsExtras {
  soc: string;
  cpu_cores: number;
  cpu_max_khz: number;
  ram_total_kb: number;
  ram_avail_kb: number;
  display: { size?: string; density?: string };
  temp_c: number;
}

export interface ApiDeviceDetails {
  model: string;
  brand: string;
  manufacturer: string;
  sdk: string;
  release: string;
  build_fingerprint: string;
  abi: string;
  hardware: string;
  serialno: string;
  uptime_sec: number;
  battery_level: number;
  storage: Record<string, string>;
  extras: ApiDeviceDetailsExtras;
}

export interface DeviceDetailsResponse {
  ok: boolean;
  serial: string;
  transport: string | null;
  device: ApiDeviceDetails | null;
  error?: string;
}

export async function fetchDeviceDetails(
  serial: string,
  signal?: AbortSignal,
): Promise<DeviceDetailsResponse> {
  const r = await fetch(`/devices/${encodeURIComponent(serial)}/details`, { signal });
  if (!r.ok) {
    throw new AlbApiError(
      `GET /devices/${serial}/details returned ${r.status}`,
      r.status,
      "DEVICE_DETAILS_FETCH_FAILED",
    );
  }
  return (await r.json()) as DeviceDetailsResponse;
}

// ── UART captures (PR-C.a) ────────────────────────────────────────

export interface UartCaptureSummary {
  name: string;
  size_bytes: number;
  mtime: number;
}

export interface UartCaptureListResponse {
  ok: boolean;
  device: string | null;
  captures: UartCaptureSummary[];
}

export interface UartCaptureReadResponse {
  ok: boolean;
  name: string;
  size_bytes: number;
  text: string;
}

export interface UartCaptureTriggerResponse {
  ok: boolean;
  duration: number;
  lines?: number;
  errors?: number;
  filename?: string | null;
  path?: string | null;
  error?: string;
}

export async function fetchUartCaptures(
  device?: string | null,
  signal?: AbortSignal,
): Promise<UartCaptureListResponse> {
  const qs = device ? `?device=${encodeURIComponent(device)}` : "";
  const r = await fetch(`/uart/captures${qs}`, { signal });
  if (!r.ok) {
    throw new AlbApiError(
      `GET /uart/captures returned ${r.status}`,
      r.status,
      "UART_CAPTURES_FETCH_FAILED",
    );
  }
  return (await r.json()) as UartCaptureListResponse;
}

export async function readUartCapture(
  name: string,
  device?: string | null,
  signal?: AbortSignal,
): Promise<UartCaptureReadResponse> {
  const qs = device ? `?device=${encodeURIComponent(device)}` : "";
  const r = await fetch(`/uart/captures/${encodeURIComponent(name)}${qs}`, { signal });
  if (!r.ok) {
    throw new AlbApiError(
      `GET /uart/captures/${name} returned ${r.status}`,
      r.status,
      "UART_CAPTURE_READ_FAILED",
    );
  }
  return (await r.json()) as UartCaptureReadResponse;
}

export async function triggerUartCapture(
  duration: number,
  device?: string | null,
  signal?: AbortSignal,
): Promise<UartCaptureTriggerResponse> {
  const params = new URLSearchParams({ duration: String(duration) });
  if (device) params.set("device", device);
  const r = await fetch(`/uart/capture?${params.toString()}`, {
    method: "POST",
    signal,
  });
  if (!r.ok) {
    throw new AlbApiError(
      `POST /uart/capture returned ${r.status}`,
      r.status,
      "UART_CAPTURE_TRIGGER_FAILED",
    );
  }
  return (await r.json()) as UartCaptureTriggerResponse;
}

export async function deleteUartCapture(
  name: string,
  device?: string | null,
  signal?: AbortSignal,
): Promise<{ ok: boolean; name: string }> {
  const params = new URLSearchParams();
  if (device) params.set("device", device);
  const qs = params.toString();
  const r = await fetch(
    `/uart/captures/${encodeURIComponent(name)}${qs ? `?${qs}` : ""}`,
    { method: "DELETE", signal },
  );
  if (!r.ok) {
    throw new AlbApiError(
      `DELETE /uart/captures/${name} returned ${r.status}`,
      r.status,
      "UART_CAPTURE_DELETE_FAILED",
    );
  }
  return (await r.json()) as { ok: boolean; name: string };
}

// ── Device system snapshot (PR-B) ─────────────────────────────────

export interface ApiDeviceSystem {
  props: Record<string, string>;
  partitions: { name: string; target: string }[];
  mounts: { device: string; mount_point: string; fstype: string; opts: string }[];
  block_devices: { major: string; minor: string; size_kib: string; name: string }[];
  meminfo: Record<string, number>;
  storage: Record<string, { used_kib: string; avail_kib: string; use_pct: string; device: string }>;
  network: { iface: string; ipv4?: string; ipv6?: string; mac?: string }[];
  battery: Record<string, string>;
  thermal: { zone: string; type: string; temp_c: string }[];
}

export interface DeviceSystemResponse {
  ok: boolean;
  serial: string;
  transport: string | null;
  system: ApiDeviceSystem | null;
  error?: string;
}

export async function fetchDeviceSystem(
  serial: string,
  signal?: AbortSignal,
): Promise<DeviceSystemResponse> {
  const r = await fetch(`/devices/${encodeURIComponent(serial)}/system`, { signal });
  if (!r.ok) {
    throw new AlbApiError(
      `GET /devices/${serial}/system returned ${r.status}`,
      r.status,
      "DEVICE_SYSTEM_FETCH_FAILED",
    );
  }
  return (await r.json()) as DeviceSystemResponse;
}

// ── Screenshot + UI dump (PR-G) ───────────────────────────────────

export interface ScreenshotPayload {
  filename: string;
  path: string;
  device_path: string;
  size_bytes: number;
  width: number;
  height: number;
  png_base64: string;
}

export interface ScreenshotResponse {
  ok: boolean;
  serial: string;
  transport: string | null;
  screenshot: ScreenshotPayload | null;
  error?: string;
}

export async function captureScreenshot(
  serial: string,
  signal?: AbortSignal,
): Promise<ScreenshotResponse> {
  const r = await fetch(`/devices/${encodeURIComponent(serial)}/screenshot`, {
    method: "POST",
    signal,
  });
  if (!r.ok) {
    throw new AlbApiError(
      `POST /devices/${serial}/screenshot returned ${r.status}`,
      r.status,
      "SCREENSHOT_FAILED",
    );
  }
  return (await r.json()) as ScreenshotResponse;
}

export interface ScreenshotEntry {
  name: string;
  size_bytes: number;
  mtime: number;
  width: number | null;
  height: number | null;
}

export interface ScreenshotsListResponse {
  ok: boolean;
  serial: string;
  screenshots: ScreenshotEntry[];
}

export async function fetchScreenshots(
  serial: string,
  signal?: AbortSignal,
): Promise<ScreenshotsListResponse> {
  const r = await fetch(`/devices/${encodeURIComponent(serial)}/screenshots`, {
    signal,
  });
  if (!r.ok) {
    throw new AlbApiError(
      `GET /devices/${serial}/screenshots returned ${r.status}`,
      r.status,
      "SCREENSHOTS_LIST_FAILED",
    );
  }
  return (await r.json()) as ScreenshotsListResponse;
}

/** Build the URL for `<img src>` to load one stored screenshot's PNG bytes
 *  via the backend (no base64 inflation, browser caches naturally). */
export function screenshotImageUrl(serial: string, name: string): string {
  return `/devices/${encodeURIComponent(serial)}/screenshots/${encodeURIComponent(name)}`;
}

export interface DeleteScreenshotResponse {
  ok: boolean;
  serial: string;
  name: string;
  /** False when the file was already gone (idempotent). */
  removed: boolean;
}

export async function deleteScreenshot(
  serial: string,
  name: string,
  signal?: AbortSignal,
): Promise<DeleteScreenshotResponse> {
  const r = await fetch(
    `/devices/${encodeURIComponent(serial)}/screenshots/${encodeURIComponent(name)}`,
    { method: "DELETE", signal },
  );
  if (!r.ok) {
    throw new AlbApiError(
      `DELETE /devices/${serial}/screenshots/${name} returned ${r.status}`,
      r.status,
      "SCREENSHOT_DELETE_FAILED",
    );
  }
  return (await r.json()) as DeleteScreenshotResponse;
}

export interface UiNode {
  index: number;
  class: string;
  resource_id: string;
  text: string;
  content_desc: string;
  bounds: [number, number, number, number];
  clickable: boolean;
  enabled: boolean;
  focused: boolean;
  selected: boolean;
  package: string;
  children: UiNode[];
}

export interface UiDumpPayload {
  path: string;
  device_path: string;
  size_bytes: number;
  root: UiNode | null;
  top_activity: string | null;
  package_name: string | null;
  node_count: number;
  rotation: number;
}

export interface UiDumpResponse {
  ok: boolean;
  serial: string;
  transport: string | null;
  ui_dump: UiDumpPayload | null;
  error?: string;
}

export async function captureUiDump(
  serial: string,
  signal?: AbortSignal,
): Promise<UiDumpResponse> {
  const r = await fetch(`/devices/${encodeURIComponent(serial)}/ui-dump`, {
    method: "POST",
    signal,
  });
  if (!r.ok) {
    throw new AlbApiError(
      `POST /devices/${serial}/ui-dump returned ${r.status}`,
      r.status,
      "UI_DUMP_FAILED",
    );
  }
  return (await r.json()) as UiDumpResponse;
}

export interface AuditEvent {
  ts: string;
  session_id: string;
  // "playground" (ADR-049): Playground throughput events carry this so the
  // live-session card (which folds only source==="chat") ignores them while
  // they still feed the cross-backend KPI + be-card sparkline.
  source: "chat" | "terminal" | "playground";
  kind: string;
  summary: string;
  /** Kind-specific payload — `rate_per_s` for tps_sample, `id`/`name`
   * for tool_call_*, `usage`/`model` for done, etc. Reducers narrow
   * by `kind` then read fields here at runtime (no static schema). */
  data?: Record<string, unknown> | null;
  ts_approx: boolean;
}

export interface AuditResponse {
  ok: boolean;
  since: string;
  until: string;
  events: AuditEvent[];
}

export async function fetchAudit(
  minutes = 30,
  limit = 200,
  signal?: AbortSignal,
): Promise<AuditResponse> {
  const r = await fetch(`/audit?minutes=${minutes}&limit=${limit}`, { signal });
  if (!r.ok) {
    throw new AlbApiError(
      `GET /audit returned ${r.status}`,
      r.status,
      "AUDIT_FETCH_FAILED",
    );
  }
  return (await r.json()) as AuditResponse;
}

export interface ToolCategory {
  name: string;
  count: number;
}
export interface ToolEntry {
  name: string;
  description: string;
  category: string;
}
export interface ToolsResponse {
  ok: boolean;
  count: number;
  categories: ToolCategory[];
  tools: ToolEntry[];
}

export async function fetchTools(signal?: AbortSignal): Promise<ToolsResponse> {
  const r = await fetch(`/tools`, { signal });
  if (!r.ok) {
    throw new AlbApiError(
      `GET /tools returned ${r.status}`,
      r.status,
      "TOOLS_FETCH_FAILED",
    );
  }
  return (await r.json()) as ToolsResponse;
}

export interface MetricsSummaryTps {
  mean: number;
  p50: number;
  p95: number;
  max: number;
  min: number;
}

export interface MetricsSummaryResponse {
  ok: boolean;
  since: string;
  until: string;
  window_s: number;
  session_id: string | null;
  tps: MetricsSummaryTps | null;
  total_tokens: number;
  sample_count: number;
}

export async function fetchMetricsSummary(
  windowSeconds = 300,
  signal?: AbortSignal,
): Promise<MetricsSummaryResponse> {
  const r = await fetch(`/metrics/summary?window_seconds=${windowSeconds}`, {
    signal,
  });
  if (!r.ok) {
    throw new AlbApiError(
      `GET /metrics/summary returned ${r.status}`,
      r.status,
      "METRICS_SUMMARY_FETCH_FAILED",
    );
  }
  return (await r.json()) as MetricsSummaryResponse;
}

/** Static manifest entry for an LLM backend (alb.infra.registry). The
 * server returns the *registered shape*, not runtime health — see
 * `BackendSpec` in `src/alb/infra/registry.py`. Runtime health (latency,
 * throughput, error rate) requires a separate metric source not yet
 * wired up; for now the LlmBackendCards UI only knows whether each
 * backend is implemented (status="beta") or planned. */
export interface ApiBackend {
  name: string;
  status: "beta" | "planned" | string;
  /**
   * ADR-027 (formal 2026-05-02): host-side compute requirement.
   *   "cpu"    — alb-host runs the inference locally on CPU
   *   "gpu"    — alb-host requires a local GPU
   *   "remote" — alb-host only sends HTTP; model runs elsewhere
   * Replaces the old `runs_on_cpu: boolean` whose name lied for
   * HTTP-only backends (was true for openai-compat though the model
   * ran on the upstream server). UI may render as a 3-state badge.
   */
  host_compute_type: "cpu" | "gpu" | "remote" | string;
  supports_tool_calls: boolean;
  requires: string[];
  description: string;
}

export interface BackendsResponse {
  backends: ApiBackend[];
}

export async function fetchBackends(
  signal?: AbortSignal,
): Promise<BackendsResponse> {
  const r = await fetch(`/playground/backends`, { signal });
  if (!r.ok) {
    throw new AlbApiError(
      `GET /playground/backends returned ${r.status}`,
      r.status,
      "BACKENDS_FETCH_FAILED",
    );
  }
  return (await r.json()) as BackendsResponse;
}

/** Live probe of a registered backend — see DEBT-017 + ADR-021 doctrine
 * (registry tells us what's *implemented*, this tells us what's *running*).
 *
 * `reachable` is the headline:
 *   - true  → probe says up (latency_ms and usually model populated)
 *   - false → probe says down OR backend can't be probed at all (in
 *             which case `reason` is always set)
 *   - null  → reserved for future async-pending probes (today the
 *             server never returns null; UI treats it as "unknown")
 *
 * `reason` is a closed enum; when a future server adds new variants
 * the UI will see the unknown string and fall back to the generic
 * "down" rendering. */
export type BackendHealthReason =
  | "no_probe" // ABC default, no concrete probe wired
  | "not_implemented" // registry status='planned'
  | "init_failed" // construction raised
  | "probe_failed" // health() raised
  | "probe_timeout" // health() exceeded the endpoint deadline
  | "down"; // health() ran cleanly and reported reachable=false

export interface BackendHealth {
  name: string;
  reachable: boolean | null;
  latency_ms: number | null;
  model: string | null;
  model_present?: boolean | null;
  /** Discriminator carried whenever `reachable === false`. Always null
   * when reachable=true. */
  reason?: BackendHealthReason | null;
  error?: string | null;
}

export interface BackendModelsResponse {
  backend: string;
  models: string[];
}

export async function fetchBackendModels(
  backend: string,
  signal?: AbortSignal,
): Promise<BackendModelsResponse> {
  const r = await fetch(
    `/playground/backends/${encodeURIComponent(backend)}/models`,
    { signal },
  );
  if (!r.ok) {
    throw new AlbApiError(
      `GET /playground/backends/${backend}/models returned ${r.status}`,
      r.status,
      "BACKEND_MODELS_FAILED",
    );
  }
  return (await r.json()) as BackendModelsResponse;
}

export async function fetchBackendHealth(
  name: string,
  signal?: AbortSignal,
): Promise<BackendHealth> {
  const r = await fetch(
    `/playground/backends/${encodeURIComponent(name)}/health`,
    { signal },
  );
  if (!r.ok) {
    throw new AlbApiError(
      `GET /playground/backends/${name}/health returned ${r.status}`,
      r.status,
      "BACKEND_HEALTH_FETCH_FAILED",
    );
  }
  return (await r.json()) as BackendHealth;
}

/** One backend's throughput series (ADR-049). `samples` is the mean
 * tok/s per time bucket, oldest → newest, for the be-card sparkline. */
export interface BackendThroughput {
  samples: number[];
  total_tokens: number;
  tps:
    | { mean: number; p50: number; p95: number; max: number; min: number }
    | null;
  sample_count: number;
}

export interface BackendThroughputResponse {
  ok: boolean;
  window_s: number;
  bucket_s: number;
  group_by: "backend";
  source: string;
  backends: Record<string, BackendThroughput>;
}

/** Per-backend throughput for the dashboard be-card sparkline (ADR-049).
 * Hits the read-model-backed `/metrics/summary?group_by=backend`. Served
 * from the in-memory MetricStore; on a cold start it's partial/empty,
 * which renders as a flat baseline. */
export async function fetchBackendThroughput(
  windowSeconds = 300,
  buckets = 15,
  signal?: AbortSignal,
): Promise<BackendThroughputResponse> {
  const r = await fetch(
    `/metrics/summary?group_by=backend&window_seconds=${windowSeconds}&buckets=${buckets}`,
    { signal },
  );
  if (!r.ok) {
    throw new AlbApiError(
      `GET /metrics/summary?group_by=backend returned ${r.status}`,
      r.status,
      "BACKEND_THROUGHPUT_FAILED",
    );
  }
  return (await r.json()) as BackendThroughputResponse;
}

// ─── Files browser (PR-H) ─────────────────────────────────────────
export interface DeviceFileEntry {
  name: string;
  is_dir: boolean;
  is_link: boolean;
  link_target: string | null;
  size: number;
  mode: string;
  owner: string;
  group: string;
  mtime: string;
}

export interface DeviceFilesResponse {
  ok: boolean;
  serial: string;
  path: string;
  entries: DeviceFileEntry[];
  truncated?: boolean;
  exit_code?: number;
  error?: string;
}

export async function listDeviceFiles(
  serial: string,
  path: string,
  signal?: AbortSignal,
): Promise<DeviceFilesResponse> {
  const r = await fetch(
    `/devices/${encodeURIComponent(serial)}/files?path=${encodeURIComponent(path)}`,
    { signal },
  );
  if (!r.ok) {
    throw new AlbApiError(
      `GET /devices/${serial}/files returned ${r.status}`,
      r.status,
      "DEVICE_FILES_FAILED",
    );
  }
  return (await r.json()) as DeviceFilesResponse;
}

export interface WorkspaceFileEntry {
  name: string;
  is_dir: boolean;
  is_link: boolean;
  size: number;
  mtime_epoch: number;
}

export interface WorkspaceFilesResponse {
  ok: boolean;
  path: string;
  absolute_path?: string;
  entries: WorkspaceFileEntry[];
  truncated?: boolean;
  error?: string;
}

export async function listWorkspaceFiles(
  path: string,
  signal?: AbortSignal,
): Promise<WorkspaceFilesResponse> {
  const r = await fetch(
    `/workspace/files?path=${encodeURIComponent(path)}`,
    { signal },
  );
  if (!r.ok) {
    throw new AlbApiError(
      `GET /workspace/files returned ${r.status}`,
      r.status,
      "WORKSPACE_FILES_FAILED",
    );
  }
  return (await r.json()) as WorkspaceFilesResponse;
}

export interface PullResponse {
  ok: boolean;
  serial: string;
  remote: string;
  local?: string | null;
  local_workspace_rel?: string | null;
  duration_ms?: number;
  error?: string;
}

export async function pullDeviceFile(
  serial: string,
  remote: string,
  local?: string,
): Promise<PullResponse> {
  const r = await fetch(`/devices/${encodeURIComponent(serial)}/files/pull`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(local ? { remote, local } : { remote }),
  });
  if (!r.ok) {
    throw new AlbApiError(
      `POST /devices/${serial}/files/pull returned ${r.status}`,
      r.status,
      "DEVICE_PULL_FAILED",
    );
  }
  return (await r.json()) as PullResponse;
}

export interface PushResponse {
  ok: boolean;
  serial: string;
  local?: string;
  remote: string;
  bytes_transferred?: number;
  duration_ms?: number;
  /** True when the path is in a sensitive prefix (/system /vendor …)
   * and the user must confirm before resubmitting with `force: true`. */
  requires_confirm?: boolean;
  error?: string;
}

export async function pushDeviceFile(
  serial: string,
  local: string,
  remote: string,
  opts?: { force?: boolean },
): Promise<PushResponse> {
  const r = await fetch(`/devices/${encodeURIComponent(serial)}/files/push`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      local,
      remote,
      ...(opts?.force ? { force: true } : {}),
    }),
  });
  if (!r.ok) {
    throw new AlbApiError(
      `POST /devices/${serial}/files/push returned ${r.status}`,
      r.status,
      "DEVICE_PUSH_FAILED",
    );
  }
  return (await r.json()) as PushResponse;
}

/** Browser-friendly download URL — anchors point straight at the API
 * so the browser handles the stream itself; no JS buffering needed. */
export function workspaceDownloadUrl(workspaceRelPath: string): string {
  const safe = workspaceRelPath
    .split("/")
    .filter((seg) => seg.length > 0)
    .map((seg) => encodeURIComponent(seg))
    .join("/");
  return `/workspace/files/download/${safe}`;
}

export interface WorkspaceFilePreview {
  ok: boolean;
  path: string;
  size_bytes: number;
  is_binary: boolean;
  truncated: boolean;
  text: string;
  encoding: string;
}

export async function previewWorkspaceFile(
  workspaceRelPath: string,
  signal?: AbortSignal,
): Promise<WorkspaceFilePreview> {
  const safe = workspaceRelPath
    .split("/")
    .filter((seg) => seg.length > 0)
    .map((seg) => encodeURIComponent(seg))
    .join("/");
  const r = await fetch(`/workspace/files/preview/${safe}`, { signal });
  if (!r.ok) {
    throw new AlbApiError(
      `GET /workspace/files/preview/${workspaceRelPath} returned ${r.status}`,
      r.status,
      "WORKSPACE_PREVIEW_FAILED",
    );
  }
  return (await r.json()) as WorkspaceFilePreview;
}

/* ─── App (install / uninstall / start / stop / list / info / clear) ─ */

export interface AppListData {
  packages: string[];
  count: number;
}

export interface AppInfoData {
  package: string;
  version_name: string;
  version_code: string;
  first_install_time: string;
  last_update_time: string;
  requested_permissions: string[];
}

export interface AppEnvelope<T> {
  ok: boolean;
  data?: T;
  error?: { code: string; message: string; suggestion?: string };
  timing_ms?: number;
}

export async function fetchAppList(
  device: string | null | undefined,
  opts: { filter?: string; include_system?: boolean } = {},
  signal?: AbortSignal,
): Promise<AppEnvelope<AppListData>> {
  const p = new URLSearchParams();
  if (device) p.set("device", device);
  if (opts.filter) p.set("filter", opts.filter);
  if (opts.include_system) p.set("include_system", "true");
  const r = await fetch(`/api/app/list?${p.toString()}`, { signal });
  if (!r.ok && r.status !== 503 && r.status !== 400) {
    throw new AlbApiError(
      `GET /api/app/list returned ${r.status}`,
      r.status,
      "APP_LIST_FAILED",
    );
  }
  return parseEnvelope<AppEnvelope<AppListData>>(r);
}

export async function fetchAppInfo(
  pkg: string,
  device: string | null | undefined,
  signal?: AbortSignal,
): Promise<AppEnvelope<AppInfoData>> {
  const p = new URLSearchParams({ package: pkg });
  if (device) p.set("device", device);
  const r = await fetch(`/api/app/info?${p.toString()}`, { signal });
  if (!r.ok && r.status !== 503 && r.status !== 400) {
    throw new AlbApiError(
      `GET /api/app/info returned ${r.status}`,
      r.status,
      "APP_INFO_FAILED",
    );
  }
  return parseEnvelope<AppEnvelope<AppInfoData>>(r);
}

async function _postApp(
  endpoint: string,
  device: string | null | undefined,
  body: object,
): Promise<AppEnvelope<unknown>> {
  const r = await fetch(`/api/app/${endpoint}${_qDevice(device)}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok && r.status !== 422 && r.status !== 400 && r.status !== 503) {
    throw new AlbApiError(
      `POST /api/app/${endpoint} returned ${r.status}`,
      r.status,
      "APP_OP_FAILED",
    );
  }
  return parseEnvelope<AppEnvelope<unknown>>(r);
}

export const postAppStart = (
  device: string | null | undefined,
  component: string,
) => _postApp("start", device, { component });

export const postAppStop = (
  device: string | null | undefined,
  pkg: string,
) => _postApp("stop", device, { package: pkg });

export const postAppClearData = (
  device: string | null | undefined,
  pkg: string,
  opts: { allow_dangerous?: boolean } = {},
) =>
  _postApp("clear-data", device, {
    package: pkg,
    allow_dangerous: opts.allow_dangerous ?? false,
  });

export const postAppUninstall = (
  device: string | null | undefined,
  pkg: string,
  opts: { keep_data?: boolean; allow_dangerous?: boolean } = {},
) =>
  _postApp("uninstall", device, {
    package: pkg,
    keep_data: opts.keep_data ?? false,
    allow_dangerous: opts.allow_dangerous ?? false,
  });

export async function postAppInstall(
  device: string | null | undefined,
  file: File,
  opts: { replace?: boolean; grant_runtime?: boolean; downgrade?: boolean } = {},
): Promise<AppEnvelope<unknown>> {
  const p = new URLSearchParams();
  if (device) p.set("device", device);
  if (opts.replace === false) p.set("replace", "false");
  if (opts.grant_runtime) p.set("grant_runtime", "true");
  if (opts.downgrade) p.set("downgrade", "true");
  const fd = new FormData();
  fd.append("apk", file);
  const r = await fetch(`/api/app/install?${p.toString()}`, {
    method: "POST",
    body: fd,
  });
  if (!r.ok && r.status !== 422 && r.status !== 400 && r.status !== 503) {
    throw new AlbApiError(
      `POST /api/app/install returned ${r.status}`,
      r.status,
      "APP_INSTALL_FAILED",
    );
  }
  return parseEnvelope<AppEnvelope<unknown>>(r);
}

/* ─── Diag (bugreport / anr / tombstone) ───────────────────────── */

export interface DiagBugreportResult {
  zip_path: string;
  txt_path: string | null;
  duration_ms: number;
}

export interface DiagPullBundleResult {
  kind: string;
  count: number;
  files: string[];
}

export interface DiagEnvelope<T> {
  ok: boolean;
  data?: T;
  error?: { code: string; message: string; suggestion?: string };
  timing_ms?: number;
}

export interface DiagArtifactFile {
  path: string;
  name: string;
  size_bytes: number;
  mtime: number;
}

export interface DiagArtifactBundle {
  bundle: string;
  path: string;
  files: DiagArtifactFile[];
  count: number;
}

export interface DiagArtifactsResponse {
  ok: boolean;
  data: {
    bugreports: DiagArtifactFile[];
    anr: DiagArtifactBundle[];
    tombstones: DiagArtifactBundle[];
  };
}

export async function postDiagBugreport(
  device: string | null | undefined,
): Promise<DiagEnvelope<DiagBugreportResult>> {
  const r = await fetch(`/api/diag/bugreport${_qDevice(device)}`, {
    method: "POST",
  });
  if (!r.ok && r.status !== 422 && r.status !== 400 && r.status !== 503) {
    throw new AlbApiError(
      `POST /api/diag/bugreport returned ${r.status}`,
      r.status,
      "BUGREPORT_FAILED",
    );
  }
  return parseEnvelope<DiagEnvelope<DiagBugreportResult>>(r);
}

export async function postDiagAnr(
  device: string | null | undefined,
  body: { clear_after: boolean },
): Promise<DiagEnvelope<DiagPullBundleResult>> {
  const r = await fetch(`/api/diag/anr${_qDevice(device)}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok && r.status !== 422 && r.status !== 400 && r.status !== 503) {
    throw new AlbApiError(
      `POST /api/diag/anr returned ${r.status}`,
      r.status,
      "ANR_FAILED",
    );
  }
  return parseEnvelope<DiagEnvelope<DiagPullBundleResult>>(r);
}

export async function postDiagTombstone(
  device: string | null | undefined,
  body: { limit: number },
): Promise<DiagEnvelope<DiagPullBundleResult>> {
  const r = await fetch(`/api/diag/tombstone${_qDevice(device)}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok && r.status !== 422 && r.status !== 400 && r.status !== 503) {
    throw new AlbApiError(
      `POST /api/diag/tombstone returned ${r.status}`,
      r.status,
      "TOMBSTONE_FAILED",
    );
  }
  return parseEnvelope<DiagEnvelope<DiagPullBundleResult>>(r);
}

export async function fetchDiagArtifacts(
  device: string,
  signal?: AbortSignal,
): Promise<DiagArtifactsResponse> {
  const r = await fetch(
    `/api/diag/artifacts?device=${encodeURIComponent(device)}`,
    { signal },
  );
  if (!r.ok) {
    throw new AlbApiError(
      `GET /api/diag/artifacts returned ${r.status}`,
      r.status,
      "DIAG_ARTIFACTS_FAILED",
    );
  }
  return (await r.json()) as DiagArtifactsResponse;
}

/* ─── Log search (regex over workspace logs) ────────────────────── */

export interface LogSearchMatch {
  path: string;
  line_number: number;
  content: string;
}

export interface LogSearchData {
  pattern: string;
  matches: LogSearchMatch[];
  truncated: boolean;
  match_count: number;
}

export interface LogSearchResponse {
  ok: boolean;
  data?: LogSearchData;
  error?: { code: string; message: string; suggestion?: string };
  timing_ms?: number;
}

export async function fetchLogSearch(
  pattern: string,
  opts: { device?: string | null; max?: number } = {},
  signal?: AbortSignal,
): Promise<LogSearchResponse> {
  const params = new URLSearchParams({ pattern });
  if (opts.device) params.set("device", opts.device);
  if (opts.max != null) params.set("max", String(opts.max));
  const r = await fetch(`/api/log/search?${params.toString()}`, { signal });
  if (!r.ok && r.status !== 422 && r.status !== 400) {
    throw new AlbApiError(
      `GET /api/log/search returned ${r.status}`,
      r.status,
      "LOG_SEARCH_FAILED",
    );
  }
  return parseEnvelope<LogSearchResponse>(r);
}

export interface DmesgData {
  lines: number;
  errors: number;
  duration_captured_ms: number;
}

export interface DmesgResponse {
  ok: boolean;
  data?: DmesgData;
  error?: { code: string; message: string; suggestion?: string };
  artifacts?: string[];
  timing_ms?: number;
}

/** Collect a fresh kernel dmesg snapshot into the workspace (ARCH-1), so
 *  the Log Search tab has a dmesg artifact to search. Mirrors `alb dmesg`. */
export async function postDmesg(
  device: string | null | undefined,
  duration = 10,
): Promise<DmesgResponse> {
  const r = await fetch(`/api/log/dmesg${_qDevice(device)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ duration }),
  });
  if (!r.ok && r.status !== 422 && r.status !== 400 && r.status !== 503) {
    throw new AlbApiError(
      `POST /api/log/dmesg returned ${r.status}`,
      r.status,
      "DMESG_FAILED",
    );
  }
  return parseEnvelope<DmesgResponse>(r);
}

/* ─── Info panels (security / gpu / processes / …) — ARCH-2 ─────── */

export interface ApiSecurityInfo {
  verified_boot_state: string;
  avb_version: string;
  verity_mode: string;
  crypto_state: string;
  crypto_type: string;
  file_encryption: string;
  selinux_mode: string;
  selinux_policy_version: string;
  oem_unlock_allowed: boolean;
  oem_unlock_supported: boolean;
  adb_secure: boolean;
}

export interface ApiGpuInfo {
  name: string;
  vendor: string;
  renderer: string;
  freq_hz_current: number;
  freq_hz_max: number;
  freq_hz_min: number;
  governor: string;
  util_pct: number;
}

export interface ApiProcessEntry {
  pid: number;
  user: string;
  cpu_pct: number;
  mem_pct: number;
  rss_kb: number;
  name: string;
}

export interface ApiProcessesInfo {
  count: number;
  top_cpu: ApiProcessEntry[];
  top_mem: ApiProcessEntry[];
}

export interface InfoPanelEnvelope<T> {
  ok: boolean;
  data?: T;
  error?: { code: string; message: string; suggestion?: string };
  timing_ms?: number;
}

/** Fetch one device-info panel. `panel` ∈ security|gpu|processes|cpu|… */
export async function fetchInfoPanel<T>(
  panel: string,
  device: string | null | undefined,
  signal?: AbortSignal,
): Promise<InfoPanelEnvelope<T>> {
  const r = await fetch(`/api/info/${encodeURIComponent(panel)}${_qDevice(device)}`, {
    signal,
  });
  if (!r.ok && r.status !== 422 && r.status !== 400 && r.status !== 404 && r.status !== 503) {
    throw new AlbApiError(
      `GET /api/info/${panel} returned ${r.status}`,
      r.status,
      "INFO_PANEL_FAILED",
    );
  }
  return parseEnvelope<InfoPanelEnvelope<T>>(r);
}

/* ─── Power (battery / reboot / sleep-wake) ────────────────────── */

export interface BatteryData {
  level: number;
  scale: number;
  health: string;
  status: string;
  plugged: string;
  temperature_celsius: number;
  voltage_mv: number;
}

export interface PowerEnvelope<T> {
  ok: boolean;
  data?: T;
  error?: { code: string; message: string; suggestion?: string };
  timing_ms?: number;
}

function _qDevice(device: string | null | undefined): string {
  return device ? `?device=${encodeURIComponent(device)}` : "";
}

export async function fetchBattery(
  device: string | null | undefined,
  signal?: AbortSignal,
): Promise<PowerEnvelope<BatteryData>> {
  const r = await fetch(`/api/power/battery${_qDevice(device)}`, { signal });
  if (!r.ok && r.status !== 400) {
    throw new AlbApiError(
      `GET /api/power/battery returned ${r.status}`,
      r.status,
      "BATTERY_FETCH_FAILED",
    );
  }
  return parseEnvelope<PowerEnvelope<BatteryData>>(r);
}

export interface RebootRequest {
  mode: "normal" | "recovery" | "bootloader" | "fastboot" | "sideload";
  wait_boot?: boolean;
  timeout?: number;
  allow_dangerous?: boolean;
}

export interface RebootResult {
  mode: string;
  wait_boot_ms: number | null;
}

export async function postReboot(
  device: string | null | undefined,
  body: RebootRequest,
): Promise<PowerEnvelope<RebootResult>> {
  const r = await fetch(`/api/power/reboot${_qDevice(device)}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok && r.status !== 422 && r.status !== 400) {
    throw new AlbApiError(
      `POST /api/power/reboot returned ${r.status}`,
      r.status,
      "REBOOT_FAILED",
    );
  }
  return parseEnvelope<PowerEnvelope<RebootResult>>(r);
}

export interface SleepWakeRequest {
  cycles: number;
  hold_sec: number;
}

export interface SleepWakeResult {
  cycles: number;
  records: Array<{ cycle: number; duration_ms: number }>;
  total_ms: number;
}

export async function postSleepWake(
  device: string | null | undefined,
  body: SleepWakeRequest,
): Promise<PowerEnvelope<SleepWakeResult>> {
  const r = await fetch(`/api/power/sleep-wake${_qDevice(device)}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok && r.status !== 422 && r.status !== 400) {
    throw new AlbApiError(
      `POST /api/power/sleep-wake returned ${r.status}`,
      r.status,
      "SLEEP_WAKE_FAILED",
    );
  }
  return parseEnvelope<PowerEnvelope<SleepWakeResult>>(r);
}

/* ─── Doctor (env health snapshot) ─────────────────────────────── */

export type DoctorStatus = "ok" | "warn" | "err" | "skip";

export interface DoctorCheck {
  name: string;
  status: DoctorStatus;
  detail: string;
}

export interface DoctorLayer {
  name: string;
  status: DoctorStatus;
  checks: DoctorCheck[];
}

export interface DoctorPayload {
  layers: DoctorLayer[];
  summary: Record<DoctorStatus, number>;
}

export async function fetchDoctor(signal?: AbortSignal): Promise<DoctorPayload> {
  const r = await fetch("/api/doctor", { signal });
  if (!r.ok) {
    throw new AlbApiError(
      `GET /api/doctor returned ${r.status}`,
      r.status,
      "DOCTOR_FETCH_FAILED",
    );
  }
  return (await r.json()) as DoctorPayload;
}
