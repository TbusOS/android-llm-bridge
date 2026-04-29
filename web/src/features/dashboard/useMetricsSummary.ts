/**
 * Aggregated throughput summary hook.
 *
 * Wraps `GET /metrics/summary?window_seconds=N`. The endpoint scans the
 * `tps_sample` events on disk and returns mean / p50 / p95 / max / min
 * over the window — see ADR-021. Default window is 5 minutes (matching
 * the F.7 KPI label "5 分均 / 5m avg").
 *
 * Closes DEBT-004 (LLM throughput KPI previously stuck at "—").
 *
 * Important — semantic split with LiveSession:
 *   - LiveSessionCard.tps shows the *latest 1-second sample* ("now").
 *   - KPI here shows the *windowed mean* ("5m avg").
 * The two numbers will legitimately differ; UI must label both. See
 * `.claude/knowledge/review-feedback.md` (F.6 arch review #4).
 */
import { useQuery } from "@tanstack/react-query";

import {
  fetchMetricsSummary,
  type MetricsSummaryResponse,
} from "../../lib/api";

const REFETCH_MS = 30_000;
const DEFAULT_WINDOW_S = 300;

export function useMetricsSummary(windowSeconds = DEFAULT_WINDOW_S) {
  const q = useQuery<MetricsSummaryResponse>({
    queryKey: ["metrics-summary", windowSeconds],
    queryFn: ({ signal }) => fetchMetricsSummary(windowSeconds, signal),
    staleTime: REFETCH_MS,
    refetchInterval: REFETCH_MS,
  });
  const tps = q.data?.tps ?? null;
  return {
    windowSeconds: q.data?.window_s ?? windowSeconds,
    tps,
    sampleCount: q.data?.sample_count ?? 0,
    totalTokens: q.data?.total_tokens ?? 0,
    /** Mean rounded to 1 decimal — `null` when no samples in window. */
    meanRounded: tps ? Math.round(tps.mean * 10) / 10 : null,
    isLoading: q.isLoading,
    isError: q.isError,
    error: q.error,
  };
}
