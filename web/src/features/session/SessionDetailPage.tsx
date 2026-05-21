/**
 * SessionDetailPage — replay of a single ChatSession.
 *
 * Reads `/sessions/{id}` and renders the messages.jsonl stream as
 * role-based bubbles (user / assistant / tool) plus tool-call cards.
 * Mockup baseline: `docs/webui-preview-v2-session-detail.html` —
 * class names here must match it verbatim (L-028 mockup baseline).
 *
 * Pure read-only: no replay/fork actions yet — those would warrant
 * their own task (DEBT candidate: "session replay/fork wire-up").
 */
import { Link, useParams } from "@tanstack/react-router";

import { useApp } from "../../stores/app";
import type { SessionMessage } from "../../lib/api";
import { useSessionDetail } from "./useSessionDetail";

function formatCreated(iso: string | null, lang: "en" | "zh"): string {
  if (!iso) return lang === "zh" ? "未知" : "unknown";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    // Render in the viewer's local timezone — the previous
    // `toISOString().slice(0, 16)` showed UTC silently, which is wrong
    // for anyone outside UTC (a 14:00 PST session showed as 22:00).
    return d.toLocaleString(lang === "zh" ? "zh-CN" : "en-US", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
  } catch {
    return iso;
  }
}

function MetaItem({ label, value }: { label: string; value: string }) {
  return (
    <span className="session-meta__item">
      <span className="session-meta__label">{label}</span> {value}
    </span>
  );
}

function MessageNode({ msg, lang }: { msg: SessionMessage; lang: "en" | "zh" }) {
  const role = msg.role;
  if (role === "user") {
    return (
      <div className="session-bubble session-bubble--user">
        <span className="session-bubble__role">{lang === "zh" ? "用户" : "User"}</span>
        {msg.content ?? ""}
      </div>
    );
  }
  if (role === "tool") {
    const tag = msg.name ? `${lang === "zh" ? "工具" : "Tool"} · ${msg.name}` : (lang === "zh" ? "工具" : "Tool");
    return (
      <div className="session-bubble session-bubble--tool">
        <span className="session-bubble__role">{tag}</span>
        {msg.content ?? ""}
      </div>
    );
  }
  // assistant — may have content, may have tool_calls, may have both.
  return (
    <>
      {(msg.tool_calls ?? []).map((tc, i) => (
        <div
          key={tc.id ?? `${tc.name ?? "tool"}-${i}`}
          className="session-tool-call"
        >
          <div className="session-tool-call__head">
            <span className="session-tool-call__name">{tc.name ?? "?"}</span>
            {tc.id && <span className="session-tool-call__id">{tc.id}</span>}
          </div>
          <pre className="session-tool-call__args">
            {typeof tc.arguments === "string"
              ? tc.arguments
              : JSON.stringify(tc.arguments ?? {}, null, 2)}
          </pre>
        </div>
      ))}
      {msg.content && (
        <div className="session-bubble session-bubble--assistant">
          <span className="session-bubble__role">
            {lang === "zh" ? "助手" : "Assistant"}
          </span>
          {msg.content}
        </div>
      )}
    </>
  );
}

export function SessionDetailPage() {
  const lang = useApp((s) => s.lang);
  const { sessionId } = useParams({ strict: false }) as { sessionId?: string };
  const { data, isLoading, isError, error } = useSessionDetail(sessionId);

  return (
    <main className="session-page" role="main">
      <nav className="session-page__nav" aria-label="breadcrumb">
        <Link to="/dashboard">
          ← {lang === "zh" ? "返回 Dashboard" : "Back to Dashboard"}
        </Link>
      </nav>

      <header className="session-page__heading">
        <h1>
          {lang === "zh" ? "会话" : "Session"} <code>{sessionId}</code>
        </h1>
        {data && (
          <div className="session-meta">
            <MetaItem
              label={lang === "zh" ? "创建" : "created"}
              value={formatCreated(data.created, lang)}
            />
            <MetaItem
              label={lang === "zh" ? "后端" : "backend"}
              value={data.backend || "—"}
            />
            <MetaItem
              label={lang === "zh" ? "模型" : "model"}
              value={data.model || "—"}
            />
            <MetaItem
              label={lang === "zh" ? "设备" : "device"}
              value={data.device || "—"}
            />
            <MetaItem
              label={lang === "zh" ? "轮数" : "turns"}
              value={String(data.turns)}
            />
          </div>
        )}
      </header>

      {isLoading && (
        <div className="session-empty">
          {lang === "zh" ? "正在加载会话…" : "Loading session…"}
        </div>
      )}

      {isError && (
        <div className="session-empty" role="alert">
          {lang === "zh" ? "加载失败：" : "Failed to load: "}
          {error instanceof Error ? error.message : String(error)}
        </div>
      )}

      {data && data.messages.length === 0 && (
        <div className="session-empty">
          {lang === "zh"
            ? "这条会话还没有任何消息记录。"
            : "This session has no messages recorded yet."}
        </div>
      )}

      {data && data.messages.length > 0 && (
        <section className="session-stream" aria-label="messages">
          {data.messages.map((m, i) => (
            <MessageNode key={i} msg={m} lang={lang} />
          ))}
        </section>
      )}
    </main>
  );
}
