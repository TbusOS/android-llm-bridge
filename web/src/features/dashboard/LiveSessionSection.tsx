/**
 * LiveSessionSection — owns the metric (`includeMetrics`) /audit/stream
 * subscription so the dashboard's 1 Hz `tps_sample` churn is contained
 * HERE (PERF-4) instead of re-rendering the whole DashboardPage + all six
 * sections every second. Only this small component (which genuinely needs
 * to update each tick) re-renders; devices / KPIs / recent sessions /
 * timeline / quick-actions stay put between metric ticks.
 */
import { useAuditStream } from "../../lib/hooks/useAuditStream";
import { LiveSessionCard } from "./LiveSessionCard";
import { useLiveSession } from "./useLiveSession";

export function LiveSessionSection() {
  const liveAudit = useAuditStream({ includeMetrics: true });
  const live = useLiveSession(liveAudit.rawEvents);
  return <LiveSessionCard data={live} streamStatus={liveAudit.status} />;
}
