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

export interface AuditEvent {
  ts: string;
  session_id: string;
  source: "chat" | "terminal";
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
  runs_on_cpu: boolean;
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
