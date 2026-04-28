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
import { useCallback, useEffect, useRef, useState } from "react";

import type { AuditEvent } from "../../lib/api";
import { connect, type WsClient } from "../../lib/ws";
import type { TimelineEventData } from "./types";
import { mapAuditToTimeline } from "./useAudit";

export type AuditStreamStatus = "connecting" | "open" | "closed" | "error";

const MAX_EVENTS = 200;

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

  const [events, setEvents] = useState<TimelineEventData[]>([]);
  const [rawEvents, setRawEvents] = useState<AuditEvent[]>([]);
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
            setEvents(msg.events.map(mapAuditToTimeline));
            setRawEvents(msg.events);
            setSince(msg.since);
            setUntil(msg.until);
          } else if (isEvent(msg)) {
            const raw = msg.data;
            const ev = mapAuditToTimeline(raw);
            setEvents((prev) => [ev, ...prev].slice(0, MAX_EVENTS));
            setRawEvents((prev) => [raw, ...prev].slice(0, MAX_EVENTS));
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

  return { events, rawEvents, since, until, status, paused, pause, resume };
}
