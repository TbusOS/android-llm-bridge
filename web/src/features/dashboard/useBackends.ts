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
import { useQueries, useQuery } from "@tanstack/react-query";

import {
  fetchBackendHealth,
  fetchBackends,
  type ApiBackend,
  type BackendHealth,
  type BackendHealthReason,
  type BackendsResponse,
} from "../../lib/api";
import type { BackendCardData } from "./types";

const MANIFEST_REFETCH_MS = 60_000;
const HEALTH_REFETCH_MS = 15_000;
const HEALTH_REFETCH_ERROR_MS = 60_000;

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
  | { kind: "up"; latencyMs: number | null; model: string | null }
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

export function useBackends(): UseBackendsResult {
  const manifestQuery = useQuery<BackendsResponse>({
    queryKey: ["backends"],
    queryFn: ({ signal }) => fetchBackends(signal),
    staleTime: MANIFEST_REFETCH_MS,
    refetchInterval: MANIFEST_REFETCH_MS,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: false,
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

  const cards: BackendCardData[] = [];
  const runtime: Record<string, BackendRuntimeState> = {};
  for (let i = 0; i < apiBackends.length; i += 1) {
    const api = apiBackends[i];
    if (!api) continue;
    const hq = healthQueries[i];
    const health = (hq?.data as BackendHealth | undefined) ?? null;
    cards.push(mapApiBackendToCard(api, health));
    runtime[api.name] = deriveRuntimeState(
      api,
      health,
      hq?.isLoading ?? false,
      hq?.isError ?? false,
    );
  }

  return {
    backends: cards,
    runtime,
    isLoading: manifestQuery.isLoading,
    isError: manifestQuery.isError,
    error: manifestQuery.error,
  };
}
