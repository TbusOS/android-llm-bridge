/**
 * Dashboard view-model wrapper around the raw `useAuditStream` hook.
 *
 * Maps `businessRaw` into `TimelineEventData[]` for the dashboard's
 * `<ActivityTimeline>`. Other consumers of the same WS:
 *
 *   - `useLiveSession(metricRaw)` consumes the metric buffer directly
 *   - `features/audit/AuditPage` reads `rawEvents` (merged) for its
 *     own chronological table projection
 *
 * Why wrapped instead of inlined back into the hook: lib/hooks/ MUST
 * NOT import features/. Pre-5/25 the mapper was inlined inside
 * useAuditStream — arch HIGH-4 surfaced the residual reverse-dep.
 */
import { useMemo } from "react";

import {
  useAuditStream,
  type AuditStreamRawViewModel,
  type UseAuditStreamOptions,
} from "../../lib/hooks/useAuditStream";
import { mapAuditToTimeline } from "./useAudit";
import type { TimelineEventData } from "./types";

export interface AuditTimelineViewModel extends AuditStreamRawViewModel {
  /** Mapped, ready-to-render timeline rows (newest first, capped). */
  events: TimelineEventData[];
}

export function useAuditTimeline(
  options: UseAuditStreamOptions = {},
): AuditTimelineViewModel {
  const vm = useAuditStream(options);
  const events = useMemo(
    () => vm.businessRaw.map(mapAuditToTimeline),
    [vm.businessRaw],
  );
  return { ...vm, events };
}
