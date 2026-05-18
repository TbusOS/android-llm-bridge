/**
 * Doctor route data hook — fetches the six-layer health snapshot via
 * `GET /api/doctor`. Cheap & deterministic; no auto-refetch (user clicks
 * Refresh from the panel).
 */
import { useQuery } from "@tanstack/react-query";

import { fetchDoctor } from "../../lib/api";

export function useDoctor() {
  return useQuery({
    queryKey: ["doctor"],
    staleTime: 30_000,
    refetchOnWindowFocus: false,
    queryFn: ({ signal }) => fetchDoctor(signal),
  });
}
