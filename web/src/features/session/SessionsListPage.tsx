/**
 * SessionsListPage — replaces the v1 StubPage at `/sessions`.
 *
 * Renders `GET /sessions?limit=50` as a clickable table. Each row links
 * to the matching `/sessions/$sessionId` detail page that already
 * exists. Local text filter narrows by session_id / backend / model /
 * device — debounced via `useDeferredValue` (same pattern as AppTab
 * PackageList from commit C / D).
 *
 * Pagination is server-driven by `limit` only today; the list endpoint
 * has no offset cursor yet (DEBT-039 calls that out). The "Show more"
 * button bumps `limit` by 50 in 50-up to 500 cap. Anything beyond goes
 * through the upcoming pagination refactor.
 */
import { Link } from "@tanstack/react-router";
import { useDeferredValue, useMemo, useState } from "react";

import { useApp } from "../../stores/app";
import type { SessionSummary } from "../../lib/api";
import { useSessions } from "./useSessions";

const PAGE_STEP = 50;
const MAX_LIMIT = 500;

function formatRel(iso: string | null, lang: "en" | "zh"): string {
  if (!iso) return lang === "zh" ? "—" : "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const delta = Date.now() - d.getTime();
  const m = Math.floor(delta / 60_000);
  if (m < 1) return lang === "zh" ? "刚刚" : "just now";
  if (m < 60) return lang === "zh" ? `${m} 分钟前` : `${m} min ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return lang === "zh" ? `${h} 小时前` : `${h} h ago`;
  const days = Math.floor(h / 24);
  return lang === "zh" ? `${days} 天前` : `${days} d ago`;
}

function matches(s: SessionSummary, q: string): boolean {
  if (!q) return true;
  const t = q.toLowerCase();
  return (
    s.session_id.toLowerCase().includes(t) ||
    s.backend.toLowerCase().includes(t) ||
    s.model.toLowerCase().includes(t) ||
    (s.device ?? "").toLowerCase().includes(t)
  );
}

export function SessionsListPage() {
  const lang = useApp((s) => s.lang);
  const [limit, setLimit] = useState(PAGE_STEP);
  const [filter, setFilter] = useState("");
  const deferredFilter = useDeferredValue(filter);

  const { data, isLoading, isError, error, isFetching, refetch } =
    useSessions(limit);

  const allSessions = data?.sessions ?? [];
  const visible = useMemo(
    () => allSessions.filter((s) => matches(s, deferredFilter)),
    [allSessions, deferredFilter],
  );

  return (
    <section>
      <header className="sessions-page__header">
        <div>
          <h1 className="h-title">
            {lang === "zh" ? "会话历史" : "Sessions"}
          </h1>
          <p className="h-sub">
            {lang === "zh"
              ? "所有 JSONL agent session 的浏览 / 搜索 / 回放。"
              : "Browse / search / replay every JSONL agent session."}
          </p>
        </div>
        <div className="sessions-page__actions">
          <input
            type="search"
            placeholder={
              lang === "zh"
                ? "筛选 session_id / backend / model / device"
                : "filter id / backend / model / device"
            }
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="sessions-page__filter"
            aria-label={lang === "zh" ? "筛选" : "filter"}
          />
          <button
            type="button"
            className="sessions-page__refresh"
            disabled={isFetching}
            onClick={() => {
              void refetch();
            }}
          >
            {isFetching
              ? lang === "zh"
                ? "刷新中…"
                : "refreshing…"
              : lang === "zh"
                ? "刷新"
                : "refresh"}
          </button>
        </div>
      </header>

      {isLoading && (
        <div className="sessions-page__empty" role="status">
          {lang === "zh" ? "正在读取…" : "Loading…"}
        </div>
      )}

      {isError && (
        <div className="sessions-page__empty" role="alert">
          {lang === "zh" ? "读取失败：" : "Failed: "}
          {error instanceof Error ? error.message : String(error)}
        </div>
      )}

      {!isLoading && !isError && allSessions.length === 0 && (
        <div className="sessions-page__empty">
          {lang === "zh"
            ? "暂无会话 — 跑一次 Chat 或 Terminal 试试。"
            : "No sessions yet — try Chat or Terminal."}
        </div>
      )}

      {visible.length > 0 && (
        <div className="sessions-table" role="table">
          <div className="sessions-table__head" role="row">
            <span role="columnheader">
              {lang === "zh" ? "会话" : "session"}
            </span>
            <span role="columnheader">
              {lang === "zh" ? "创建时间" : "created"}
            </span>
            <span role="columnheader">backend · model</span>
            <span role="columnheader">device</span>
            <span role="columnheader">
              {lang === "zh" ? "轮次" : "turns"}
            </span>
            <span role="columnheader">
              {lang === "zh" ? "最近活动" : "last event"}
            </span>
          </div>
          {visible.map((s) => (
            <Link
              key={s.session_id}
              to="/sessions/$sessionId"
              params={{ sessionId: s.session_id }}
              className="sessions-table__row"
              role="row"
            >
              <span className="sessions-table__id" role="cell">
                {s.session_id}
              </span>
              <span className="sessions-table__cell" role="cell">
                {s.created
                  ? new Date(s.created).toLocaleString(
                      lang === "zh" ? "zh-CN" : "en-US",
                      {
                        year: "numeric",
                        month: "2-digit",
                        day: "2-digit",
                        hour: "2-digit",
                        minute: "2-digit",
                        hour12: false,
                      },
                    )
                  : "—"}
              </span>
              <span className="sessions-table__cell" role="cell">
                {s.backend} · {s.model}
              </span>
              <span className="sessions-table__cell" role="cell">
                {s.device ?? "—"}
              </span>
              <span className="sessions-table__cell" role="cell">
                {s.turns}
              </span>
              <span className="sessions-table__cell" role="cell">
                {formatRel(s.last_event_ts, lang)}
              </span>
            </Link>
          ))}
        </div>
      )}

      {visible.length > 0 && (
        <div className="sessions-page__footer">
          <span className="sessions-page__count" role="status">
            {deferredFilter
              ? lang === "zh"
                ? `${visible.length} / ${allSessions.length} 匹配`
                : `${visible.length} of ${allSessions.length} matches`
              : lang === "zh"
                ? `${allSessions.length} 个会话`
                : `${allSessions.length} sessions`}
          </span>
          {allSessions.length >= limit && limit < MAX_LIMIT && (
            <button
              type="button"
              className="sessions-page__more"
              disabled={isFetching}
              onClick={() => setLimit((l) => Math.min(MAX_LIMIT, l + PAGE_STEP))}
            >
              {lang === "zh"
                ? `多看 ${PAGE_STEP} 个`
                : `Show ${PAGE_STEP} more`}
            </button>
          )}
        </div>
      )}
    </section>
  );
}
