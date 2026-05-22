/**
 * useSessions — list-side query for `GET /sessions`.
 *
 * Pairs with `useSessionDetail` (single session replay) and powers the
 * `SessionsListPage`. Default 5-minute staleTime — sessions barely
 * change once stopped, polling tighter only hurts.
 */
import { useQuery } from "@tanstack/react-query";
import { fetchSessions } from "../../lib/api";

const STALE_MS = 5 * 60 * 1000;

export function useSessions(limit = 50) {
  return useQuery({
    queryKey: ["sessions", limit],
    staleTime: STALE_MS,
    queryFn: ({ signal }) => fetchSessions(limit, signal),
  });
}
