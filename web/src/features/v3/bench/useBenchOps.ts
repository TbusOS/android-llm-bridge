import { type BenchOpsView, fetchBenchOps } from "../../../lib/api3";
import { useDashboardQuery } from "../../../lib/dashboardQuery";

/** The connected bench agent's operation table (GET /api/bench/ops). */
export function useBenchOps() {
  return useDashboardQuery<BenchOpsView>({ queryKey: ["bench-ops"], queryFn: ({ signal }) => fetchBenchOps(signal), refetchMs: 15_000 });
}
