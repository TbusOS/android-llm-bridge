/**
 * LLM backends hook — registry manifest + parallel health probes.
 *
 * Two layers of TanStack Query:
 *   1. `useQuery(["backends"])` lists the registered backends (static
 *      manifest from `/playground/backends`, refetched once per minute).
 *   2. `useQueries` fans out to `/playground/backends/{name}/health`,
 *      one query per backend, refetched every 15 s. Each health query
 *      is independent so a slow / failed probe on one backend doesn't
 *      stall the others. `planned` backends are deliberately *not*
 *      probed — registry already tells us they're unimplemented and a
 *      cheap server-side short-circuit still costs an HTTP round-trip
 *      per dashboard tab × 4/min.
 *
 * Refetch policy (from agents perf review):
 *   - `refetchIntervalInBackground: false` — hidden tabs don't probe
 *     (can save 10-100× requests for users who tab away overnight)
 *   - `refetchOnWindowFocus: false` — interval already covers it; an
 *     extra refetch on every focus would cause N parallel requests
 *     when a user switches tabs
 *   - `retry: 1` — a probe failure IS the signal we want to render
 *     (down · probe_failed); retrying 3× hides transient causes and
 *     adds noise
 *   - error-state backoff: refetch every 60 s (not 15 s) once the
 *     last probe errored, recovers to 15 s on first success
 *
 * Status mapping:
 *   - manifest status='planned' → BackendCard 'unconfigured' kind=planned
 *   - reachable=true → kind=up with latencyMs (may be null) + model
 *   - reachable=false + reason='no_probe' → kind=unprobed (registered
 *     but no concrete health probe wired — distinct from "down")
 *   - reachable=false + other reason → kind=down with reason + error
 *   - reachable=null → kind=unprobed (server reserved future state)
 *
 * Closes DEBT-002 (was MOCK_BACKENDS) + DEBT-017 (runtime health gap).
 */
import { useQueries } from "@tanstack/react-query";

import { useDashboardQuery } from "../../lib/dashboardQuery";

import {
  fetchBackendHealth,
  fetchBackends,
  fetchBackendThroughput,
  type ApiBackend,
  type BackendHealth,
  type BackendHealthReason,
  type BackendsResponse,
  type BackendThroughputResponse,
} from "../../lib/api";
import type { BackendCardData } from "./types";

const MANIFEST_REFETCH_MS = 60_000;
const HEALTH_REFETCH_MS = 15_000;
const HEALTH_REFETCH_ERROR_MS = 60_000;
// Throughput is a live near-window view; poll a touch slower than health
// (it's a cheap in-memory read server-side, but the spark only moves at
// the 1 Hz sample cadence so sub-20s polling buys nothing).
const THROUGHPUT_REFETCH_MS = 20_000;

/** Pure mapping kept exported for unit tests (DEBT-012 follow-up). */
export function mapApiBackendToCard(
  api: ApiBackend,
  health: BackendHealth | null,
): BackendCardData {
  if (api.status === "planned") {
    return {
      name: api.name,
      model: api.description || api.requires[0] || "",
      status: "unconfigured",
    };
  }
  return {
    name: api.name,
    model: health?.model || api.description || api.requires[0] || "",
    status: "up",
  };
}

export type BackendRuntimeState =
  | { kind: "planned" }
  | { kind: "unprobed" }
  | {
      kind: "up";
      latencyMs: number | null;
      model: string | null;
      /** Per-backend throughput series (mean tok/s per bucket, oldest →
       * newest) for the be-card sparkline (ADR-049). Undefined until the
       * read model has data for this backend (cold start → no spark). */
      throughput?: number[];
    }
  | {
      kind: "down";
      reason: BackendHealthReason | null | undefined;
      error: string | null | undefined;
    }
  | { kind: "loading" }
  | { kind: "error" };

/** Pure derivation kept exported for unit tests. */
export function deriveRuntimeState(
  api: ApiBackend,
  health: BackendHealth | null,
  isLoading: boolean,
  isError: boolean,
): BackendRuntimeState {
  if (api.status === "planned") return { kind: "planned" };
  if (isError) return { kind: "error" };
  if (!health) return isLoading ? { kind: "loading" } : { kind: "unprobed" };
  if (health.reachable === true) {
    return {
      kind: "up",
      latencyMs:
        typeof health.latency_ms === "number" ? health.latency_ms : null,
      model: health.model,
    };
  }
  if (health.reachable === false) {
    // 'no_probe' is "registered, no concrete probe wired" — render
    // as unprobed, not as a down state, so the user sees a neutral
    // "runtime: unknown" instead of a red 'down' card.
    if (health.reason === "no_probe") return { kind: "unprobed" };
    return { kind: "down", reason: health.reason, error: health.error };
  }
  return { kind: "unprobed" };
}

export interface UseBackendsResult {
  backends: BackendCardData[];
  runtime: Record<string, BackendRuntimeState>;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
}

/** Coarse status for the global LLM pill (UI-2). "up" if any backend is
 *  reachable, "down" if the manifest failed or every probeable backend is
 *  down/errored, "checking" while we genuinely don't know yet. Kept pure +
 *  exported for unit tests. */
export type OverallLlmStatus = "up" | "down" | "checking";

export function deriveOverallBackendStatus(
  r: UseBackendsResult,
): OverallLlmStatus {
  if (r.isError) return "down";
  if (r.isLoading) return "checking";
  const states = Object.values(r.runtime).filter((s) => s.kind !== "planned");
  if (states.some((s) => s.kind === "up")) return "up";
  if (states.some((s) => s.kind === "loading")) return "checking";
  if (states.some((s) => s.kind === "down" || s.kind === "error")) {
    return "down";
  }
  return "checking"; // only unprobed / nothing registered → unknown
}

export function useBackends(): UseBackendsResult {
  const manifestQuery = useDashboardQuery<BackendsResponse>({
    queryKey: ["backends"],
    queryFn: ({ signal }) => fetchBackends(signal),
    refetchMs: MANIFEST_REFETCH_MS,
  });
  const apiBackends = manifestQuery.data?.backends ?? [];

  // One health query per *non-planned* backend. Planned ones short-
  // circuit on the server too, but skipping the request entirely
  // saves one TCP round-trip per tab × 4/min and keeps the network
  // panel readable.
  const healthQueries = useQueries({
    queries: apiBackends.map((api) => ({
      queryKey: ["backend-health", api.name],
      queryFn: ({ signal }: { signal?: AbortSignal }) =>
        fetchBackendHealth(api.name, signal),
      enabled: manifestQuery.isSuccess && api.status !== "planned",
      staleTime: HEALTH_REFETCH_MS,
      refetchInterval: (query: { state: { error: unknown } }) =>
        query.state.error ? HEALTH_REFETCH_ERROR_MS : HEALTH_REFETCH_MS,
      refetchIntervalInBackground: false,
      refetchOnWindowFocus: false,
      retry: 1,
    })),
  });

  // Per-backend throughput series (ADR-049). One batch query for all
  // backends — it's a cheap read-model read, not a per-backend probe that
  // can hang, so no fan-out isolation needed. Folded into the runtime
  // state below so the be-card has a single state machine (no separate
  // "throughput loading" tearing against the health state).
  const throughputQuery = useDashboardQuery<BackendThroughputResponse>({
    queryKey: ["backend-throughput"],
    queryFn: ({ signal }) => fetchBackendThroughput(300, 15, signal),
    refetchMs: THROUGHPUT_REFETCH_MS,
  });
  const throughput = throughputQuery.data?.backends ?? {};

  const cards: BackendCardData[] = [];
  const runtime: Record<string, BackendRuntimeState> = {};
  for (let i = 0; i < apiBackends.length; i += 1) {
    const api = apiBackends[i];
    if (!api) continue;
    const hq = healthQueries[i];
    const health = (hq?.data as BackendHealth | undefined) ?? null;
    cards.push(mapApiBackendToCard(api, health));
    const rt = deriveRuntimeState(
      api,
      health,
      hq?.isLoading ?? false,
      hq?.isError ?? false,
    );
    // Only a reachable backend gets a spark; attach its series when the
    // read model has one.
    const series = throughput[api.name]?.samples;
    runtime[api.name] =
      rt.kind === "up" && series && series.length > 0
        ? { ...rt, throughput: series }
        : rt;
  }

  return {
    backends: cards,
    runtime,
    isLoading: manifestQuery.isLoading,
    isError: manifestQuery.isError,
    error: manifestQuery.error,
  };
}
