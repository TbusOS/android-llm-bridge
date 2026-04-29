/**
 * Audit live-stream hook.
 *
 * Connects to `WS /audit/stream`, which delivers a one-shot snapshot
 * followed by live increments. Auto-reconnect is handled by the
 * shared `lib/ws.ts` client; the server resends the snapshot on
 * every reconnect so React state always converges.
 *
 * Replaces the polling `useAudit` for the dashboard timeline. The
 * old hook is kept around for any future page that prefers a paged
 * HTTP read.
 *
 * Why DashboardPage opens TWO instances of this hook (one with
 * includeMetrics=false for the timeline, one with =true for the
 * LiveSession spark): see ADR-022 in .claude/knowledge/decisions.md —
 * timeline's pause/resume must NOT freeze the metric stream.
 *
 * Hook contract: options must contain only **primitive** values
 * (boolean / number / string). Passing functions or arrays will
 * trigger unbounded reconnects via the useEffect deps array.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { AuditEvent } from "../../lib/api";
import { connect, type WsClient } from "../../lib/ws";
import type { TimelineEventData } from "./types";
import { mapAuditToTimeline } from "./useAudit";

export type AuditStreamStatus = "connecting" | "open" | "closed" | "error";

/** Business-kind cap. Drives the visible Timeline length. */
const BUSINESS_CAP = 200;
/** Metric-kind cap. Aligned with SPARK_WINDOW in useLiveSession so the
 *  reducer always has enough samples to render a 60 s spark, but a
 *  long-running session's 1Hz tps_sample stream cannot push business
 *  events out of the buffer (DEBT-011 fix, F.7). */
const METRIC_CAP = 60;

/** Server-side authoritative set is `audit_route._DEFAULT_METRIC_KINDS`.
 *  When ADR-021 grows to add cmd_rate / push_rate, **double-write here**
 *  — missing it makes server treat the new kind as metric (filtered)
 *  while client treats it as business (eats into business cap, evicting
 *  real user/done events under sustained load). DEBT-013 candidate
 *  tracks pushing this list down from the server when `|kinds| ≥ 3`. */
const METRIC_KINDS: ReadonlySet<string> = new Set(["tps_sample"]);

function isMetricKind(kind: string): boolean {
  return METRIC_KINDS.has(kind);
}

interface SnapshotMessage {
  type: "snapshot";
  since: string;
  until: string;
  events: AuditEvent[];
}

interface EventMessage {
  type: "event";
  data: AuditEvent;
}

interface ControlAckMessage {
  type: "control_ack";
  action: string;
  paused: boolean;
}

type ServerMessage = SnapshotMessage | EventMessage | ControlAckMessage;

function isSnapshot(m: unknown): m is SnapshotMessage {
  return !!m && typeof m === "object" && (m as ServerMessage).type === "snapshot";
}
function isEvent(m: unknown): m is EventMessage {
  return !!m && typeof m === "object" && (m as ServerMessage).type === "event";
}
function isControlAck(m: unknown): m is ControlAckMessage {
  return !!m && typeof m === "object" && (m as ServerMessage).type === "control_ack";
}

export interface UseAuditStreamOptions {
  /** Opt in to metric kinds (e.g. tps_sample). Default false → server
   *  filters them out so the timeline UI stays readable. The Live
   *  session card opens a separate `useAuditStream({includeMetrics: true})`
   *  so its 1Hz tps_sample flow doesn't fight the timeline's pause. */
  includeMetrics?: boolean;
  /** Snapshot window in minutes. Default 30 (matches server default). */
  minutes?: number;
}

export interface AuditStreamViewModel {
  /** Mapped, ready-to-render timeline rows (newest first, capped). */
  events: TimelineEventData[];
  /** The same buffer as `events` but in raw form, for reducers like
   *  `useLiveSession` that need access to `data` payloads. */
  rawEvents: AuditEvent[];
  since: string | null;
  until: string | null;
  status: AuditStreamStatus;
  paused: boolean;
  pause: () => void;
  resume: () => void;
}

export function useAuditStream(
  options: UseAuditStreamOptions = {},
): AuditStreamViewModel {
  const { includeMetrics = false, minutes = 30 } = options;

  // Two parallel buffers so a high-rate metric stream cannot evict
  // business events (DEBT-011). Caps are tuned so each kind keeps
  // enough history for its own UI consumer:
  //   business → ActivityTimeline (200 rows)
  //   metric   → useLiveSession spark (60 samples ≈ 60 s @ 1 Hz)
  const [businessRaw, setBusinessRaw] = useState<AuditEvent[]>([]);
  const [metricRaw, setMetricRaw] = useState<AuditEvent[]>([]);
  const [since, setSince] = useState<string | null>(null);
  const [until, setUntil] = useState<string | null>(null);
  const [status, setStatus] = useState<AuditStreamStatus>("connecting");
  const [paused, setPaused] = useState(false);
  const clientRef = useRef<WsClient | null>(null);

  useEffect(() => {
    const client = connect("/audit/stream");
    clientRef.current = client;

    const unsubscribe = client.subscribe((wsEv) => {
      switch (wsEv.kind) {
        case "open":
          // Send the configuration message after EVERY open (including
          // reconnects). This relies on lib/ws.ts re-emitting "open"
          // to existing subscribers when the underlying socket
          // reconnects — if that contract changes, this re-config
          // breaks silently. The deps array below ensures the closure
          // captures the latest minutes / includeMetrics; reconnects
          // re-use the closure created by the most recent effect run.
          client.send({ minutes, include_metrics: includeMetrics });
          setStatus("open");
          break;
        case "close":
          setStatus("closed");
          break;
        case "error":
          setStatus("error");
          break;
        case "json": {
          const msg = wsEv.data;
          if (isSnapshot(msg)) {
            const business: AuditEvent[] = [];
            const metric: AuditEvent[] = [];
            for (const e of msg.events) {
              if (isMetricKind(e.kind)) metric.push(e);
              else business.push(e);
            }
            // Both setBusinessRaw + setMetricRaw + setSince + setUntil
            // fire in the same tick. React 18 batches these into a
            // single render, and the merged-rawEvents useMemo runs
            // exactly once per render, not per setState call.
            setBusinessRaw(business.slice(0, BUSINESS_CAP));
            setMetricRaw(metric.slice(0, METRIC_CAP));
            setSince(msg.since);
            setUntil(msg.until);
          } else if (isEvent(msg)) {
            const raw = msg.data;
            if (isMetricKind(raw.kind)) {
              setMetricRaw((prev) => [raw, ...prev].slice(0, METRIC_CAP));
            } else {
              setBusinessRaw((prev) => [raw, ...prev].slice(0, BUSINESS_CAP));
            }
          } else if (isControlAck(msg)) {
            setPaused(msg.paused);
          }
          break;
        }
        default:
          break;
      }
    });

    return () => {
      unsubscribe();
      client.close();
      clientRef.current = null;
    };
    // Re-create the connection if include_metrics or minutes changes —
    // simpler than sending control messages, and we don't expect these
    // to change at runtime.
  }, [includeMetrics, minutes]);

  const pause = useCallback(() => {
    if (includeMetrics) {
      // ADR-022: metric streams must follow device lifetime, not user
      // pause control. If you find yourself wanting this, you probably
      // want to gate the rendering, not the stream.
      console.warn(
        "useAuditStream({includeMetrics:true}).pause(): metric streams " +
          "should not be user-pausable; ignored.",
      );
      return;
    }
    clientRef.current?.send({ type: "control", action: "pause" });
  }, [includeMetrics]);
  const resume = useCallback(() => {
    if (includeMetrics) {
      console.warn(
        "useAuditStream({includeMetrics:true}).resume(): no-op (was never paused).",
      );
      return;
    }
    clientRef.current?.send({ type: "control", action: "resume" });
  }, [includeMetrics]);

  // Timeline rows: business only. `useMemo` so identity is stable for
  // child memo'd components.
  const events = useMemo(
    () => businessRaw.map(mapAuditToTimeline),
    [businessRaw],
  );
  // Reducer-friendly merged list (newest first by ts). With caps 200 +
  // 60 the merged array is ≤ 260 entries — sort cost is negligible.
  const rawEvents = useMemo(() => {
    if (metricRaw.length === 0) return businessRaw;
    if (businessRaw.length === 0) return metricRaw;
    const merged = [...businessRaw, ...metricRaw];
    merged.sort((a, b) => (a.ts < b.ts ? 1 : a.ts > b.ts ? -1 : 0));
    return merged;
  }, [businessRaw, metricRaw]);

  return { events, rawEvents, since, until, status, paused, pause, resume };
}
