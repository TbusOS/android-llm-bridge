/**
 * AuditPage — replaces the v1 StubPage at `/audit`.
 *
 * Renders the same WS snapshot+delta stream that Dashboard already
 * consumes (`useAuditStream`), but as a full chronological table with
 * source / kind filters, pause/resume, and live status indicator.
 *
 * Why reuse useAuditStream rather than fetching /audit on a timer:
 *   - server emits deltas; polling would race
 *   - Dashboard already pays the WS cost, second consumer on the same
 *     hook is essentially free (lib/ws.ts dedupes connect by path)
 */
import { useDeferredValue, useMemo, useState } from "react";

import { useApp } from "../../stores/app";
import { useAuditStream } from "../dashboard/useAuditStream";

const SOURCE_OPTIONS = ["all", "chat", "terminal"] as const;
type SourceFilter = (typeof SOURCE_OPTIONS)[number];

function shortSid(sid: string): string {
  return sid.length <= 10 ? sid : `${sid.slice(0, 8)}…`;
}

function formatTs(iso: string, lang: "en" | "zh"): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(lang === "zh" ? "zh-CN" : "en-US", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function statusBadge(
  status: ReturnType<typeof useAuditStream>["status"],
  paused: boolean,
  lang: "en" | "zh",
): { label: string; klass: string } {
  if (paused)
    return {
      label: lang === "zh" ? "已暂停" : "paused",
      klass: "audit-page__status--paused",
    };
  switch (status) {
    case "open":
      return {
        label: lang === "zh" ? "实时" : "live",
        klass: "audit-page__status--live",
      };
    case "connecting":
      return {
        label: lang === "zh" ? "连接中" : "connecting",
        klass: "audit-page__status--connecting",
      };
    case "closed":
      return {
        label: lang === "zh" ? "断开" : "closed",
        klass: "audit-page__status--closed",
      };
    case "error":
      return {
        label: lang === "zh" ? "错误" : "error",
        klass: "audit-page__status--error",
      };
  }
}

export function AuditPage() {
  const lang = useApp((s) => s.lang);
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>("all");
  const [kindFilter, setKindFilter] = useState("");
  const [includeMetrics, setIncludeMetrics] = useState(false);
  const [minutes, setMinutes] = useState(30);
  const deferredKind = useDeferredValue(kindFilter);

  const vm = useAuditStream({ includeMetrics, minutes });

  // rawEvents already includes metric events when includeMetrics=true.
  // Reverse to newest-first for the table view (the hook stores in
  // arrival order, oldest first).
  const allEvents = useMemo(() => [...vm.rawEvents].reverse(), [vm.rawEvents]);

  // Distinct kinds for filter dropdown — derived from current events
  // so unused kinds don't clutter the picker.
  const kindOptions = useMemo(() => {
    const s = new Set<string>();
    for (const e of allEvents) s.add(e.kind);
    return Array.from(s).sort();
  }, [allEvents]);

  const visible = useMemo(() => {
    return allEvents.filter((e) => {
      if (sourceFilter !== "all" && e.source !== sourceFilter) return false;
      if (deferredKind && !e.kind.includes(deferredKind.toLowerCase()))
        return false;
      return true;
    });
  }, [allEvents, sourceFilter, deferredKind]);

  const badge = statusBadge(vm.status, vm.paused, lang);

  return (
    <section className="audit-page">
      <header className="sessions-page__header">
        <div>
          <h1 className="h-title">{lang === "zh" ? "审计日志" : "Audit"}</h1>
          <p className="h-sub">
            {lang === "zh"
              ? "所有工具调用、HITL 决策、session 边界的 append-only 日志。"
              : "Append-only log of every tool call, HITL decision, and session boundary."}
          </p>
        </div>
        <div className="sessions-page__actions">
          <span className={`audit-page__status ${badge.klass}`} role="status">
            ● {badge.label}
          </span>
          <button
            type="button"
            className="sessions-page__refresh"
            onClick={() => (vm.paused ? vm.resume() : vm.pause())}
          >
            {vm.paused
              ? lang === "zh"
                ? "继续"
                : "resume"
              : lang === "zh"
                ? "暂停"
                : "pause"}
          </button>
        </div>
      </header>

      <div className="audit-page__filters">
        <label>
          {lang === "zh" ? "来源" : "source"}
          <select
            value={sourceFilter}
            onChange={(e) => setSourceFilter(e.target.value as SourceFilter)}
          >
            {SOURCE_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label>
          {lang === "zh" ? "类型" : "kind"}
          <input
            type="search"
            list="audit-kind-options"
            placeholder={lang === "zh" ? "如 hitl_deny" : "e.g. hitl_deny"}
            value={kindFilter}
            onChange={(e) => setKindFilter(e.target.value)}
          />
          <datalist id="audit-kind-options">
            {kindOptions.map((k) => (
              <option key={k} value={k} />
            ))}
          </datalist>
        </label>
        <label>
          {lang === "zh" ? "时间窗口" : "window"}
          <select
            value={minutes}
            onChange={(e) => setMinutes(Number(e.target.value))}
          >
            <option value={5}>5 min</option>
            <option value={30}>30 min</option>
            <option value={120}>2 h</option>
            <option value={720}>12 h</option>
          </select>
        </label>
        <label>
          <input
            type="checkbox"
            checked={includeMetrics}
            onChange={(e) => setIncludeMetrics(e.target.checked)}
          />{" "}
          {lang === "zh" ? "含 metric" : "include metrics"}
        </label>
        <span className="audit-page__count" role="status">
          {kindFilter || sourceFilter !== "all"
            ? lang === "zh"
              ? `${visible.length} / ${allEvents.length} 匹配`
              : `${visible.length} of ${allEvents.length} matches`
            : lang === "zh"
              ? `${allEvents.length} 条`
              : `${allEvents.length} events`}
        </span>
      </div>

      {visible.length === 0 ? (
        <div className="sessions-page__empty">
          {lang === "zh"
            ? "无匹配事件 — 跑一次 chat / terminal / inspect 操作再看。"
            : "No matching events — run a chat / terminal / inspect action."}
        </div>
      ) : (
        <div className="audit-table" role="table">
          <div className="audit-table__head" role="row">
            <span role="columnheader">
              {lang === "zh" ? "时间" : "ts"}
            </span>
            <span role="columnheader">source</span>
            <span role="columnheader">kind</span>
            <span role="columnheader">session</span>
            <span role="columnheader">
              {lang === "zh" ? "摘要" : "summary"}
            </span>
          </div>
          {visible.map((e, i) => (
            <div
              key={`${e.ts}-${e.session_id}-${e.kind}-${i}`}
              className="audit-table__row"
              role="row"
              data-source={e.source}
              data-kind={e.kind}
            >
              <span className="audit-table__ts" role="cell">
                {formatTs(e.ts, lang)}
                {e.ts_approx ? "~" : ""}
              </span>
              <span className="audit-table__source" role="cell">
                {e.source}
              </span>
              <span className="audit-table__kind" role="cell">
                {e.kind}
              </span>
              <span className="audit-table__sid" role="cell">
                {shortSid(e.session_id)}
              </span>
              <span className="audit-table__summary" role="cell">
                {e.summary || "(empty)"}
              </span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
