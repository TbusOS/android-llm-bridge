/**
 * Dashboard-specific view-model projections (pure functions).
 *
 * `lib/hooks/useDevices` and `lib/hooks/useAuditStream` return raw
 * server shapes (ApiDevice[] / AuditEvent[]). Dashboard consumers
 * project those raw shapes into the view-models its components want
 * (`DeviceCardData`, `TimelineEventData`).
 *
 * Why this file (not a wrapper hook): N=1 consumer (DashboardPage) for
 * each projection. ADR-043 says wrap into a hook only at N ≥ 2 — at
 * N=1 a pure function + inline `useMemo` is cleaner than a thin hook
 * (no extra render boundary, no API surface, no test mock layer).
 *
 * Security contract (5/25 sec LOW-3): AuditEvent.summary etc. are
 * routed to React string children via `TimelineEventData`; React
 * auto-escapes string children, so the producer can emit raw shell
 * command lines / model output safely. If you ever introduce HTML /
 * Markdown rendering on top of these parts, escape HERE.
 */
import type { ApiDevice, AuditEvent } from "../../lib/api";
import {
  transportFromName,
  transportLabel,
  statusFrom,
} from "../../lib/deviceFormat";
import type { DeviceCardData, TimelineEventData } from "./types";

// ─── device → DeviceCardData ───────────────────────────────────────

export function mapToDeviceCard(
  transportName: string | null,
  d: ApiDevice,
): DeviceCardData {
  const transport = transportFromName(transportName);
  const status = statusFrom(d.state);
  const modelLine = [d.product, d.model].filter(Boolean).join(" · ");
  return {
    id: d.serial,
    name: d.serial,
    modelLine,
    transport,
    transportLabel: transportLabel(transport),
    status,
    cpu: null,
    cpuTrend: [],
    cpuColor: "blue",
    tempC: null,
    tempTrend: [],
    tempColor: "blue",
    offlineNote: status === "offline" ? d.state : undefined,
  };
}

// ─── audit event → TimelineEventData ───────────────────────────────

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
  const summary = e.summary || "(empty)";
  const sid = shortSid(e.session_id);
  const approx = e.ts_approx ? "~" : "";
  const parts: TimelineEventData["parts"] = [
    { kind: "text", value: `${summary} · ` },
    { kind: "em", value: sid },
    ...(approx ? [{ kind: "text" as const, value: approx }] : []),
  ];
  return {
    time: timeOf(e.ts),
    dot: dotFor(e.source, e.kind),
    parts,
    partsZh: parts,
  };
}
