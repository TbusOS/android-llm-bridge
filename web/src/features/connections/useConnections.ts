/**
 * Connection Center data hook — fetches the remote-agent + forwarder
 * snapshot via `GET /agent/status` (P2). Polls every 5 s so the page
 * reflects an agent dialing in / dropping without a manual refresh.
 */
import { useQuery } from "@tanstack/react-query";

import { fetchAgentStatus } from "../../lib/api";

export function useConnections() {
  return useQuery({
    queryKey: ["agent-status"],
    queryFn: ({ signal }) => fetchAgentStatus(signal),
    refetchInterval: 5000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: false,
  });
}
