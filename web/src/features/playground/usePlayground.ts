/**
 * Playground data hooks — backend list + per-backend model catalog.
 *
 * Both queries use `useQuery`; the model query is gated on `enabled:
 * !!backend` so flipping backends doesn't fire a stale request.
 */
import { useQuery } from "@tanstack/react-query";

import { fetchBackendModels, fetchBackends } from "../../lib/api";

export function useBackends() {
  return useQuery({
    queryKey: ["backends"],
    staleTime: 60_000,
    queryFn: ({ signal }) => fetchBackends(signal),
  });
}

export function useBackendModels(backend: string | null) {
  return useQuery({
    queryKey: ["backend-models", backend],
    enabled: !!backend,
    staleTime: 60_000,
    queryFn: ({ signal }) => fetchBackendModels(backend!, signal),
  });
}
