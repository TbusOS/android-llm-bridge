/**
 * Session detail hook — fetches `/sessions/{id}` for the replay page.
 * Sessions are immutable once written, so a generous staleTime keeps
 * navigation snappy.
 */
import { useQuery } from "@tanstack/react-query";

import { fetchSessionDetail } from "../../lib/api";

export function useSessionDetail(sessionId: string | undefined | null) {
  return useQuery({
    queryKey: ["session-detail", sessionId],
    enabled: !!sessionId,
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
    queryFn: ({ signal }) => {
      if (!sessionId) throw new Error("missing session id");
      return fetchSessionDetail(sessionId, signal);
    },
  });
}
