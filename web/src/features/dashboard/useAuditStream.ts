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

export function useAuditStream(): AuditStreamViewModel {
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
  }, []);

  const pause = useCallback(() => {
    clientRef.current?.send({ type: "control", action: "pause" });
  }, []);
  const resume = useCallback(() => {
    clientRef.current?.send({ type: "control", action: "resume" });
  }, []);

  return { events, rawEvents, since, until, status, paused, pause, resume };
}
