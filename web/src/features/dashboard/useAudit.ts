/**
 * Audit timeline hook + mapping.
 *
 * Wraps `GET /audit` (default 30-minute window) and projects each
 * event into the `TimelineEventData` shape that <ActivityTimeline>
 * already understands.
 *
 * `<ActivityTimeline>` renders `text` via dangerouslySetInnerHTML,
 * so the mapping MUST escape any user-controlled string before
 * embedding it. Backend `summary` may contain raw shell command
 * lines / model output — never trustable.
 */
import { useQuery } from "@tanstack/react-query";

import { fetchAudit, type AuditEvent } from "../../lib/api";
import type { TimelineEventData } from "./types";

const REFETCH_MS = 10_000;

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function dotFor(
  source: AuditEvent["source"],
  kind: string,
): TimelineEventData["dot"] {
  if (source === "terminal") {
    if (kind === "deny" || kind === "hitl_deny") return "err";
    if (kind === "hitl_approve") return "orange";
    return "ok";
  }
  if (source === "chat") {
    if (kind === "tool") return "orange";
    if (kind === "user") return "muted";
    return "ok";
  }
  return "muted";
}

/** Hours-minutes-seconds in the user's local timezone. */
export function timeOf(ts: string): string {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts.slice(11, 19);
  const h = d.getHours().toString().padStart(2, "0");
  const m = d.getMinutes().toString().padStart(2, "0");
  const s = d.getSeconds().toString().padStart(2, "0");
  return `${h}:${m}:${s}`;
}

/** Last 6 chars of "<utc-date>-<short-uuid>" for compact display. */
function shortSid(sid: string): string {
  const tail = sid.split("-").slice(-1)[0] ?? sid;
  return tail.slice(0, 6) || sid.slice(0, 6);
}

export function mapAuditToTimeline(e: AuditEvent): TimelineEventData {
  const safe = escapeHtml(e.summary || "(empty)");
  const sid = escapeHtml(shortSid(e.session_id));
  const approx = e.ts_approx ? "~" : "";
  const text = `${safe} · <em>${sid}</em>${approx}`;
  return {
    time: timeOf(e.ts),
    dot: dotFor(e.source, e.kind),
    text,
    textZh: text,
  };
}

export interface AuditViewModel {
  events: TimelineEventData[];
  since: string | null;
  until: string | null;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
}

export function useAudit(minutes = 30, limit = 200): AuditViewModel {
  const q = useQuery({
    queryKey: ["audit", minutes, limit],
    queryFn: ({ signal }) => fetchAudit(minutes, limit, signal),
    staleTime: REFETCH_MS,
    refetchInterval: REFETCH_MS,
    refetchIntervalInBackground: false,
  });
  return {
    events: q.data?.events.map(mapAuditToTimeline) ?? [],
    since: q.data?.since ?? null,
    until: q.data?.until ?? null,
    isLoading: q.isLoading,
    isError: q.isError,
    error: q.error,
  };
}
