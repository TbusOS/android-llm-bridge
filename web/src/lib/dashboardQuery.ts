/**
 * Polled dashboard query wrapper (DEBT-025 / L-025 enforcement).
 *
 * Encapsulates the 2 background-gating flags every polled `useQuery`
 * hook MUST set, so that:
 *
 *   - hidden browser tabs stop firing zero-value polling
 *   - returning focus doesn't fire a redundant refetch (we already
 *     have `refetchInterval` covering staleness)
 *
 * Usage:
 *
 *   const q = useDashboardQuery<MyResponse>({
 *     queryKey: ["sessions", limit],
 *     queryFn: ({ signal }) => fetchSessions(limit, signal),
 *     refetchMs: 30_000,
 *   });
 *
 * `refetchMs` is the only required field beyond `queryKey` /
 * `queryFn`; staleTime defaults to the same value so a freshly-
 * mounted component doesn't immediately refetch what the cache
 * just got. Override per-hook if you want a different staleness
 * policy via `staleMs`, or use the escape hatch `refetchInterval`
 * if you need TanStack's function-form (e.g. error-state ramp-up).
 *
 * Why a wrapper and not a checklist:
 *   - 6 of 7 polled hooks shipped 2026-04-26~05-01 forgot
 *     `refetchIntervalInBackground:false`. Only useBackends had it
 *     because it was written with the rule already in mind.
 *   - perf-audit caught the leak after 5 days. cost: ~720 useless
 *     HTTP req/h × N tabs while users had the dashboard open in
 *     background.
 *   - L-025: "code-reviewer must grep useQuery({refetchInterval}) +
 *     ensure background gate" → wrapper is the structural fix.
 *
 * Multi-query case (useBackends.healthQueries) stays on raw
 * `useQueries` because it needs a dynamic `refetchInterval` based on
 * per-query error state — that doesn't fit the wrapper's narrow API.
 * Callers in that situation must remember the 2 flags manually; the
 * lint reminder in `useBackends.ts:142-145` is how we keep that path
 * honest until N=2 multi-query users justify a `useDashboardQueries`.
 */

import {
  useQuery,
  type UseQueryOptions,
  type UseQueryResult,
} from "@tanstack/react-query";

export interface DashboardQueryOptions<T>
  extends Omit<
    UseQueryOptions<T, Error, T, readonly unknown[]>,
    "staleTime" | "refetchInterval" | "refetchIntervalInBackground" | "refetchOnWindowFocus"
  > {
  /** Polling cadence in ms. Becomes both `refetchInterval` and the
   *  default `staleTime` (override the latter via `staleMs`). */
  refetchMs: number;
  /** Override staleness — defaults to `refetchMs`. Useful when you
   *  want longer "fresh" semantics than the polling rhythm. */
  staleMs?: number;
}

export function useDashboardQuery<T>(
  opts: DashboardQueryOptions<T>,
): UseQueryResult<T, Error> {
  const { refetchMs, staleMs, ...rest } = opts;
  return useQuery<T, Error, T, readonly unknown[]>({
    staleTime: staleMs ?? refetchMs,
    refetchInterval: refetchMs,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: false,
    ...rest,
  });
}
