/**
 * Data for the overview cards and the top-bar model chip: LLM backends
 * with live health, the alb-wide counts behind the summary tiles, and
 * helpers that turn the device list (and why it may be empty) into words.
 *
 * Query keys match the other readers of the same endpoints (`backends`,
 * `sessions`, `tools`, `device-details`) so the cache is shared.
 */
import { useQueries } from "@tanstack/react-query";

import {
  type ApiBackend,
  type BackendHealth,
  type BackendsResponse,
  type BackendThroughputResponse,
  fetchBackendHealth,
  fetchBackends,
  fetchBackendThroughput,
  fetchMetricsSummary,
  fetchSessions,
  fetchTools,
  type MetricsSummaryResponse,
  type SessionsResponse,
  type ToolsResponse,
} from "./api";
import { useDashboardQuery } from "./dashboardQuery";
import type { DevicesRawViewModel } from "./hooks/useDevices";
import type { T } from "./i18n";

/* ---------------- LLM backends ---------------- */

export type BackendState =
  | { kind: "planned" }
  | { kind: "unprobed" }
  | { kind: "loading" }
  | { kind: "error" }
  | { kind: "up"; latencyMs: number | null; model: string | null }
  | { kind: "down"; reason: string | null; error: string | null };

export interface BackendView {
  name: string;
  /** Registry description or first requirement, shown when no model is known. */
  about: string;
  compute: string;
  state: BackendState;
}

/** Registry entry + last probe → one state. `no_probe` is "registered,
 *  nobody wired a probe": unknown, not down. */
export function backendState(api: ApiBackend, health: BackendHealth | undefined, loading: boolean, failed: boolean): BackendState {
  if (api.status === "planned") return { kind: "planned" };
  if (failed) return { kind: "error" };
  if (!health) return loading ? { kind: "loading" } : { kind: "unprobed" };
  if (health.reachable === true) return { kind: "up", latencyMs: typeof health.latency_ms === "number" ? health.latency_ms : null, model: health.model };
  if (health.reachable === false) {
    if (health.reason === "no_probe") return { kind: "unprobed" };
    return { kind: "down", reason: health.reason ?? null, error: health.error ?? null };
  }
  return { kind: "unprobed" };
}

/** A few words per backend state, for the top-bar tooltip and the overview card. */
export function backendWord(t: T, s: BackendState): string {
  switch (s.kind) {
    case "up":
      return s.latencyMs !== null ? t(`可达 · ${s.latencyMs} ms`, `reachable · ${s.latencyMs} ms`) : t("可达", "reachable");
    case "down":
      return t(`连不上${s.reason ? ` · ${s.reason}` : ""}`, `down${s.reason ? ` · ${s.reason}` : ""}`);
    case "error":
      return t("探测请求失败", "probe request failed");
    case "loading":
      return t("检测中", "checking");
    case "planned":
      return t("计划中,还没实现", "planned, not built yet");
    default:
      return t("已注册,没有探测", "registered, not probed");
  }
}

export type OverallBackend = "up" | "down" | "checking" | "unknown" | "none";

/** One word for the whole set: any reachable backend means the assistant
 *  can answer, so "up" wins over a second backend being down. */
export function overallBackend(r: { isLoading: boolean; isError: boolean; backends: readonly BackendView[] }): OverallBackend {
  if (r.isError) return "down";
  if (r.isLoading) return "checking";
  const live = r.backends.filter((b) => b.state.kind !== "planned");
  if (live.length === 0) return "none";
  if (live.some((b) => b.state.kind === "up")) return "up";
  if (live.some((b) => b.state.kind === "loading")) return "checking";
  if (live.some((b) => b.state.kind === "down" || b.state.kind === "error")) return "down";
  return "unknown";
}

const HEALTH_MS = 15_000;
const HEALTH_ERROR_MS = 60_000;

export function useBackends() {
  const manifest = useDashboardQuery<BackendsResponse>({ queryKey: ["backends"], queryFn: ({ signal }) => fetchBackends(signal), refetchMs: 60_000 });
  const list = manifest.data?.backends ?? [];
  // One probe per backend so a slow one does not hold up the others;
  // planned backends are never probed. Same background gating as
  // useDashboardQuery, written out because the interval depends on the
  // last result (back off while a probe keeps failing).
  const probes = useQueries({
    queries: list.map((b) => ({
      queryKey: ["backend-health", b.name],
      queryFn: ({ signal }: { signal?: AbortSignal }) => fetchBackendHealth(b.name, signal),
      enabled: manifest.isSuccess && b.status !== "planned",
      staleTime: HEALTH_MS,
      refetchInterval: (q: { state: { error: unknown } }) => (q.state.error ? HEALTH_ERROR_MS : HEALTH_MS),
      refetchIntervalInBackground: false,
      refetchOnWindowFocus: false,
      retry: 1,
    })),
  });
  const backends: BackendView[] = list.map((b, i) => {
    const p = probes[i];
    return {
      name: b.name,
      about: b.description || b.requires[0] || "",
      compute: b.host_compute_type,
      state: backendState(b, p?.data as BackendHealth | undefined, p?.isLoading ?? false, p?.isError ?? false),
    };
  });
  return { backends, isLoading: manifest.isLoading, isError: manifest.isError, error: manifest.error, refetch: manifest.refetch };
}

/** Per-backend tok/s over the last 5 minutes, for the card sparklines. */
export function useBackendThroughput() {
  return useDashboardQuery<BackendThroughputResponse>({
    queryKey: ["backend-throughput"],
    queryFn: ({ signal }) => fetchBackendThroughput(300, 15, signal),
    refetchMs: 20_000,
  });
}

/* ---------------- summary tiles ---------------- */

export function useMetricsSummary() {
  return useDashboardQuery<MetricsSummaryResponse>({ queryKey: ["metrics-summary", 300], queryFn: ({ signal }) => fetchMetricsSummary(300, signal), refetchMs: 30_000 });
}
export function useToolCatalog() {
  return useDashboardQuery<ToolsResponse>({ queryKey: ["tools"], queryFn: ({ signal }) => fetchTools(signal), refetchMs: 60_000 });
}
export function useSessionList() {
  return useDashboardQuery<SessionsResponse>({ queryKey: ["sessions", 100], queryFn: ({ signal }) => fetchSessions(100, signal), refetchMs: 30_000 });
}

/** A session with an event in the last minute counts as running. */
export function runningSessions(r: SessionsResponse | undefined, now = Date.now()): number {
  return (r?.sessions ?? []).filter((s) => {
    const ts = s.last_event_ts ? Date.parse(s.last_event_ts) : Number.NaN;
    return !Number.isNaN(ts) && now - ts < 60_000;
  }).length;
}

/* ---------------- device list in words ---------------- */

/** Server transport class name → the word people use for it. */
export function transportLabel(name: string | null | undefined): string {
  if (!name) return "adb";
  if (/ssh/i.test(name)) return "ssh";
  if (/serial|uart/i.test(name)) return "uart";
  if (/hybrid/i.test(name)) return "hybrid";
  return "adb";
}

export type DeviceListState =
  | { kind: "loading" }
  /** GET /devices itself failed: the alb service is down or unreachable. */
  | { kind: "fetch"; message: string }
  /** The service answered ok=false: the transport (adb server, tunnel) is broken. */
  | { kind: "transport"; message: string }
  | { kind: "empty" }
  | { kind: "ok" };

export function deviceListState(v: DevicesRawViewModel): DeviceListState {
  if (v.isError) return { kind: "fetch", message: v.error instanceof Error ? v.error.message : String(v.error ?? "") };
  if (v.isLoading) return { kind: "loading" };
  if (v.backendError) return { kind: "transport", message: v.backendError };
  return v.devices.length ? { kind: "ok" } : { kind: "empty" };
}

/** Title and next step for a list that has nothing usable in it. Shared
 *  by the top-bar menu and the overview so both say the same thing. */
export function deviceListCopy(t: T, s: DeviceListState, transport: string): { title: string; body: string } {
  if (s.kind === "fetch") {
    return {
      title: t("读不到设备列表", "Can't read the device list"),
      body: t(
        `GET /devices 请求失败(${s.message})。alb 服务可能没在跑,或者这个网页和服务之间断开了。`,
        `GET /devices failed (${s.message}). The alb service may be down, or this page lost its connection to it.`,
      ),
    };
  }
  if (s.kind === "transport") {
    return {
      title: t(`${transport} 用不了`, `${transport} is not working`),
      body: t(
        `服务有回应,但 ${transport} 报错:${s.message}。常见原因是 adb server 没启动,或者 hub 连不上台架。`,
        `The service answered, but ${transport} reported: ${s.message}. Usually the adb server is not running, or the hub cannot reach the bench.`,
      ),
    };
  }
  if (s.kind === "loading") return { title: t("正在读取设备列表…", "Reading the device list…"), body: "" };
  return {
    title: t(`${transport} 里没有设备`, `No devices in ${transport}`),
    body: t(
      "可以试试:把板子插到台架上;在台架电脑上启动 adb(adb start-server);或者看看台架隧道是否连着。",
      "Try this: plug the board into the bench, start adb on the bench computer (adb start-server), or check that the bench tunnel is up.",
    ),
  };
}
