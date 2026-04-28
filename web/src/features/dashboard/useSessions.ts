/**
 * Recent-sessions hook + mapping.
 *
 * Wraps `GET /sessions` with TanStack Query and maps the raw backend
 * payload into `RecentSessionData`, the shape the existing
 * `<RecentSessions>` card already understands. Keeping the mapping
 * pure means the (eventual) tests only need to exercise `mapToRecent`,
 * not the HTTP layer.
 */
import { useQuery } from "@tanstack/react-query";

import { fetchSessions, type SessionSummary } from "../../lib/api";
import type { RecentSessionData } from "./types";

const ONE_MIN_MS = 60_000;
const REFETCH_MS = 30_000;

/** "<utc-date>-<short-uuid>" → last 6 chars of the uuid for compact display. */
function shortIdOf(sessionId: string): string {
  const tail = sessionId.split("-").slice(-1)[0] ?? sessionId;
  return tail.slice(0, 6) || sessionId.slice(0, 6);
}

function deriveStatus(
  lastEventTs: string | null,
  now = Date.now(),
): RecentSessionData["status"] {
  if (!lastEventTs) return "ok";
  const ts = new Date(lastEventTs).getTime();
  if (Number.isNaN(ts)) return "ok";
  return now - ts < ONE_MIN_MS ? "live" : "ok";
}

export function mapToRecent(
  s: SessionSummary,
  now = Date.now(),
): RecentSessionData {
  const shortId = shortIdOf(s.session_id);
  const backend = s.backend || "?";
  const model = s.model || "?";
  const line = `${shortId} · ${backend} · ${model}`;
  return {
    id: s.session_id,
    glyph: (s.backend || "A").charAt(0).toUpperCase() || "A",
    message: line,
    messageZh: line,
    turns: s.turns,
    model: s.model || "—",
    status: deriveStatus(s.last_event_ts, now),
  };
}

export function useRecentSessions(limit = 100) {
  const q = useQuery({
    queryKey: ["sessions", limit],
    queryFn: ({ signal }) => fetchSessions(limit, signal),
    staleTime: REFETCH_MS,
    refetchInterval: REFETCH_MS,
  });
  const now = Date.now();
  return {
    sessions: q.data?.sessions.map((s) => mapToRecent(s, now)) ?? [],
    isLoading: q.isLoading,
    isError: q.isError,
    error: q.error,
  };
}
