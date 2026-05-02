/**
 * MCP tool catalog hook.
 *
 * Wraps `GET /tools` with TanStack Query. The endpoint enumerates every
 * tool registered by FastMCP at startup, so the count is effectively
 * static for a given server build — we refetch once a minute purely to
 * notice operator-side reloads, not for live tracking.
 *
 * Closes DEBT-003 (KpiStrip MCP tools value previously hard-coded "21").
 */
import { useDashboardQuery } from "../../lib/dashboardQuery";
import { fetchTools, type ToolsResponse } from "../../lib/api";

const REFETCH_MS = 60_000;

export function useTools() {
  const q = useDashboardQuery<ToolsResponse>({
    queryKey: ["tools"],
    queryFn: ({ signal }) => fetchTools(signal),
    refetchMs: REFETCH_MS,
  });
  return {
    count: q.data?.count ?? 0,
    categoryCount: q.data?.categories.length ?? 0,
    isLoading: q.isLoading,
    isError: q.isError,
    error: q.error,
  };
}
