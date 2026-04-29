/**
 * LLM backends hook.
 *
 * Wraps `GET /playground/backends` (the registry-side manifest, see
 * `src/alb/infra/registry.py BACKENDS`). Maps the registered shape
 * onto the existing `BackendCardData` so `<LlmBackendCards>` can swap
 * mock data for a single hook call.
 *
 * Closes DEBT-002 (LlmBackendCards previously rendered MOCK_BACKENDS).
 *
 * Important — what this hook does NOT cover:
 *   - Runtime health (latency / throughput / errors / spark): the
 *     registry only knows whether a backend is implemented (`beta`)
 *     or planned. There is no live health endpoint yet, so those
 *     fields stay `undefined`; LlmBackendCards already renders "—"
 *     for missing values.
 *   - Active model: the spec doesn't carry the operator's chosen
 *     `ALB_OLLAMA_MODEL` etc. UI shows the registry description in
 *     its place until a runtime config endpoint exists.
 */
import { useQuery } from "@tanstack/react-query";

import {
  fetchBackends,
  type ApiBackend,
  type BackendsResponse,
} from "../../lib/api";
import type { BackendCardData } from "./types";

const REFETCH_MS = 60_000;

export function mapApiBackendToCard(api: ApiBackend): BackendCardData {
  // status: "beta" → registered + implemented → show as "up" so the UI
  // surfaces latency/throughput slots (currently "—" until a runtime
  // health endpoint lands). "planned" → "unconfigured" routes the
  // card into the secondary stat layout (status / last used / budget).
  const status: BackendCardData["status"] =
    api.status === "beta" ? "up" : "unconfigured";
  return {
    name: api.name,
    // No registry-side model field; show the description as a hint
    // so users see *why* a backend is paused/planned without having
    // to read the API doc.
    model: api.description || api.requires[0] || "",
    status,
    spark: [],
  };
}

export function useBackends() {
  const q = useQuery<BackendsResponse>({
    queryKey: ["backends"],
    queryFn: ({ signal }) => fetchBackends(signal),
    staleTime: REFETCH_MS,
    refetchInterval: REFETCH_MS,
  });
  return {
    backends: (q.data?.backends ?? []).map(mapApiBackendToCard),
    isLoading: q.isLoading,
    isError: q.isError,
    error: q.error,
  };
}
