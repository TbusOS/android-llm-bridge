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
