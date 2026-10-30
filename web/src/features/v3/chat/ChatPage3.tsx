/**
 * Chat (v3): the assistant conversation full screen. The thread is the same
 * conversation the side sheet shows (useAssistant, mounted once in the
 * shell); older sessions open read-only. Baseline: docs/webui-preview-v3-chat.html.
 *
 * The agent loop has no approval step before tools that change the board,
 * so tools are shown as "runs" when agent mode is on, never as "asks".
 */
import { useQuery } from "@tanstack/react-query";
import { type ReactNode, useEffect, useState } from "react";

import { Composer, Conversation, OnDevicePanel, SourceSwitch, useAssistant } from "../../../components/v3/Assistant";
import { Icon } from "../../../components/v3/Icon";
import { CardHead, Empty, ErrorBanner, Menu, PageHead, Pill, toast } from "../../../components/v3/ui";
import { copyText } from "../../../lib/clipboard";
import {
  fetchBackendModels,
  fetchBackends,
  fetchSessionDetail,
  fetchSessions,
  fetchTools,
  type SessionDetailResponse,
  type SessionMessage,
  type SessionSummary,
  type ToolEntry,
} from "../../../lib/api";
import { useDashboardQuery } from "../../../lib/dashboardQuery";
import { useT } from "../../../lib/i18n";
import { useApp } from "../../../stores/app";
import { useShell } from "../../../stores/shell";
import { ToolCard } from "./ToolCard";
import { parseResult } from "./toolText";
import "./chat.css";

const API_BACKENDS = new Set(["openai-compat", "anthropic", "openai"]);
/** Sessions whose first message is fetched to give them a title. */

/* ---------------- helpers ---------------- */

function firstLine(text: string, max = 48): string {
  const s = (text.split("\n").find((l) => l.trim()) ?? "").trim();
  return s.length > max ? `${s.slice(0, max)}…` : s;
}

export type DayGroup = "today" | "yesterday" | "earlier";

export function dayGroup(iso: string | null, now = new Date()): DayGroup {
  if (!iso) return "earlier";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "earlier";
  const start = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((start(now) - start(d)) / 86_400_000);
  return diff <= 0 ? "today" : diff === 1 ? "yesterday" : "earlier";
}

function hhmm(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function Paras({ text }: { text: string }) {
  // Plain text only; fenced code becomes a terminal block. Nothing is read as HTML.
  const parts = text.split(/```(?:\w+)?\n?/);
  return (
    <>
      {parts.map((p, i) =>
        i % 2 === 1 ? (
          <pre className="term" key={i}>
            {p.replace(/\n$/, "")}
          </pre>
        ) : (
          p
            .split(/\n{2,}/)
            .filter((s) => s.trim())
            .map((para, j) => <p key={`${i}-${j}`}>{para}</p>)
        ),
      )}
    </>
  );
}

/** Text handed to a new conversation so it can carry on from an old one. */
export function seedFrom(d: SessionDetailResponse, lang: "zh" | "en"): string {
  const lines: string[] = [];
  for (const m of d.messages) {
    if ((m.role !== "user" && m.role !== "assistant") || !m.content?.trim()) continue;
    const who = m.role === "user" ? (lang === "zh" ? "用户" : "User") : lang === "zh" ? "助手" : "Assistant";
    const body = m.content.length > 700 ? `${m.content.slice(0, 700)}…` : m.content;
    lines.push(`${who}: ${body}`);
  }
  let out = "";
  for (let i = lines.length - 1; i >= 0; i--) {
    const next = `${lines[i]}\n${out}`;
    if (next.length > 4000) break;
    out = next;
  }
  const head = lang === "zh" ? "下面是之前一次对话的记录(较早的部分已省略),请接着它回答。" : "Below is the record of an earlier conversation (older parts left out). Carry on from it.";
  return `${head}\n\n${out}`.trimEnd();
}

/* ---------------- page ---------------- */

export function ChatPage3() {
  const t = useT();
  const a = useAssistant();
  const source = useShell((s) => s.source);
  const [selected, setSelected] = useState<string | null>(null); // null = the live conversation
  const [search, setSearch] = useState("");

  const sessions = useDashboardQuery({
    queryKey: ["chat3-sessions"],
    queryFn: ({ signal }) => fetchSessions(60, signal),
    refetchMs: 20_000,
  });
  // A finished answer creates / touches a session: refresh the list.
  const { refetch } = sessions;
  useEffect(() => {
    if (!a.isStreaming) void refetch();
  }, [a.isStreaming, refetch]);

  const list = sessions.data?.sessions ?? [];
  const liveFirst = a.turns.find((x) => x.role === "user")?.content;
  const liveTitle = liveFirst ? firstLine(liveFirst) : t("新对话", "New chat");
  const liveInList = a.sessionId ? list.some((s) => s.session_id === a.sessionId) : false;
  // `title` comes with the list (the first user line, read by the hub), so
  // naming N sessions no longer costs N detail requests.
  const titleOf = (s: SessionSummary) =>
    s.session_id === a.sessionId && liveFirst
      ? firstLine(liveFirst)
      : s.title
        ? firstLine(s.title)
        : `${t("对话", "Chat")} ${hhmm(s.last_event_ts ?? s.created) || s.session_id.slice(-6)}`;

  const q = search.trim().toLowerCase();
  const shown = list.filter((s) => !q || `${titleOf(s)} ${s.model} ${s.backend} ${s.session_id}`.toLowerCase().includes(q));
  const groups: Record<DayGroup, SessionSummary[]> = { today: [], yesterday: [], earlier: [] };
  for (const s of shown) groups[dayGroup(s.last_event_ts ?? s.created)].push(s);
  const showLiveRow = a.turns.length > 0 && !liveInList && (!q || liveTitle.toLowerCase().includes(q));

  const newChat = () => {
    a.reset();
    setSelected(null);
  };
  const open = (s: SessionSummary) => setSelected(s.session_id === a.sessionId ? null : s.session_id);

  const groupLabel: Record<DayGroup, string> = { today: t("今天", "Today"), yesterday: t("昨天", "Yesterday"), earlier: t("更早", "Earlier") };

  const liveSelected = selected === null;
  const row = (key: string, current: boolean, icon: string, title: string, meta: string, onClick: () => void) => (
    <li className="list__item list__item--action" key={key} aria-current={current ? "true" : undefined}>
      <span className="list__lead">
        <Icon name={icon} />
      </span>
      <button type="button" className="list__title chat__pick" onClick={onClick}>
        {title}
      </button>
      <span className="list__trail" />
      <span className="list__meta">{meta}</span>
    </li>
  );

  return (
    <>
      <PageHead
        eyebrow={t("助手", "Assistant")}
        title={t("对话", "Chat")}
        desc={t(
          "用大白话问板子的事。开了 Agent 模式,模型会自己读日志、调用 alb 工具;工具直接执行,不会先问你,包括会改动板子的那几个。",
          "Ask about the board in plain words. With agent mode on, the model reads logs and runs alb tools on its own; tools run without asking first, including the ones that change the board.",
        )}
        actions={
          <button type="button" className="btn btn--filled" onClick={newChat} disabled={a.turns.length === 0 && selected === null} title={a.turns.length === 0 && selected === null ? t("现在就是一段新对话", "This already is a new chat") : undefined}>
            <Icon name="plus" />
            {t("新对话", "New chat")}
          </button>
        }
      />
      <div className="chat">
        {/* conversations */}
        <article className="card chat__list" style={{ padding: 8 }}>
          <label className="input--search" style={{ margin: 4 }}>
            <Icon name="search" size="sm" />
            <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder={t("搜索对话", "Search chats")} aria-label={t("搜索对话", "Search chats")} />
          </label>
          {sessions.isError ? (
            <div style={{ padding: 4 }}>
              <ErrorBanner action={<button type="button" className="btn btn--text btn--sm" onClick={() => void sessions.refetch()}>{t("重试", "Retry")}</button>}>
                {t("读不到对话列表:", "Cannot load the chat list: ")}
                {sessions.error?.message}
              </ErrorBanner>
            </div>
          ) : null}
          {sessions.isLoading ? (
            <div className="row" style={{ padding: 12 }}>
              <span className="spinner" style={{ width: 16, height: 16, borderWidth: 2 }} />
              {t("读取中…", "Loading…")}
            </div>
          ) : null}
          {showLiveRow ? (
            <>
              <div className="chat__day">{t("进行中", "Now")}</div>
              <ul className="list">{row("live", liveSelected, "chat", liveTitle, t("这次对话 · 还没存档", "this chat · not saved yet"), () => setSelected(null))}</ul>
            </>
          ) : null}
          {(["today", "yesterday", "earlier"] as const).map((g) =>
            groups[g].length ? (
              <div key={g}>
                <div className="chat__day">{groupLabel[g]}</div>
                <ul className="list">
                  {groups[g].map((s) => {
                    const isLive = s.session_id === a.sessionId;
                    const kind = API_BACKENDS.has(s.backend) ? t("云端 API", "cloud API") : t("本机", "host");
                    return row(
                      s.session_id,
                      isLive ? liveSelected : selected === s.session_id,
                      API_BACKENDS.has(s.backend) ? "link" : "chat",
                      titleOf(s),
                      `${hhmm(s.last_event_ts ?? s.created)} · ${kind} · ${s.model || s.backend} · ${t(`${s.turns} 条`, `${s.turns} msgs`)}`,
                      () => open(s),
                    );
                  })}
                </ul>
              </div>
            ) : null,
          )}
          {!sessions.isLoading && !sessions.isError && shown.length === 0 && !showLiveRow ? (
            <Empty icon="chat" title={q ? t("没有匹配的对话", "No matching chats") : t("还没有对话", "No chats yet")}>
              {q ? t("换个关键词试试。", "Try another word.") : t("在右边提第一个问题,对话会出现在这里。", "Ask the first question on the right; the chat shows up here.")}
            </Empty>
          ) : null}
        </article>

        {/* thread */}
        {liveSelected ? (
          <LiveThread title={liveTitle} onNew={newChat} />
        ) : (
          <ReadOnlyThread
            id={selected}
            fallbackTitle={list.find((s) => s.session_id === selected) ? titleOf(list.find((s) => s.session_id === selected) as SessionSummary) : (selected ?? "")}
            onContinue={(title, seed) => {
              a.reset();
              useShell.setState({ context: { zh: `承接:${title}`, en: `Continuing: ${title}`, detail: seed } });
              setSelected(null);
              toast(t("已开始新对话,会带上这次对话的内容。", "New chat started; it carries this conversation as context."));
            }}
            onBack={() => setSelected(null)}
          />
        )}

        {/* side */}
        <div className="stack chat__side">
          {source === "board" ? null : <ModelCard />}
          {source === "board" ? <SourceOnlyCard /> : null}
          <ToolsCard />
          <article className="card">
            <CardHead title={t("板上模型", "On-board model")} />
            <OnDevicePanel />
          </article>
        </div>
      </div>
    </>
  );
}

/* ---------------- thread ---------------- */

function LiveThread({ title, onNew }: { title: string; onNew: () => void }) {
  const t = useT();
  const a = useAssistant();
  const device = useApp((s) => s.device);
  const model = useApp((s) => s.model);
  const source = useShell((s) => s.source);
  const copyAll = () => {
    const text = a.turns.map((x) => `${x.role === "user" ? t("用户", "User") : t("助手", "Assistant")}: ${x.content}`).join("\n\n");
    void copyText(text).then((ok) => toast(ok ? t("已复制对话。", "Conversation copied.") : t("浏览器不让复制。", "The browser refused to copy.")));
  };
  return (
    <article className="card chat__thread">
      <div className="card__head">
        <h2>{title}</h2>
        <Pill tone="info">{device ?? t("未选设备", "no device")}</Pill>
        <Pill>
          {model ?? a.backend} · {source === "api" ? t("云端 API", "cloud API") : source === "board" ? t("板上", "on board") : t("本机", "host")}
        </Pill>
        <div className="card__actions">
          <Menu
            align="end"
            trigger={(p) => (
              <button type="button" className="icon-btn icon-btn--sm" aria-label={t("更多", "More")} {...p}>
                <Icon name="more" size="sm" />
              </button>
            )}
            items={[
              { key: "new", icon: "plus", label: t("新对话", "New chat"), onSelect: onNew },
              { key: "copy", icon: "copy", label: t("复制整段对话", "Copy the conversation"), disabled: a.turns.length === 0, onSelect: copyAll },
            ]}
          />
        </div>
      </div>
      {source === "board" ? (
        <div className="convo">
          <Empty icon="chip" title={t("板上模型还不能直接对话", "Chatting with the on-board model is not available yet")}>
            {t("右边的「板上模型」可以探测板子上有没有推理服务。要对话,先把模型来源切回本机或云端 API。", "Use the on-board model card on the right to look for a server on the board. To chat, switch the source back to host or cloud API.")}
          </Empty>
        </div>
      ) : (
        <>
          <Conversation />
          <div className="row chat__ctxrow">
            {device ? <span className="ctx">@ {device}</span> : <span className="ctx">{t("没有选中设备", "no device selected")}</span>}
          </div>
          <Composer rows={3} className="chat__composer" />
        </>
      )}
    </article>
  );
}

function ReadOnlyThread({
  id,
  fallbackTitle,
  onContinue,
  onBack,
}: {
  id: string | null;
  fallbackTitle: string;
  onContinue: (title: string, seed: string) => void;
  onBack: () => void;
}) {
  const t = useT();
  const lang = useApp((s) => s.lang);
  const q = useQuery({
    queryKey: ["chat3-session", id],
    queryFn: ({ signal }) => fetchSessionDetail(id as string, signal),
    enabled: !!id,
    staleTime: 60_000,
    retry: false,
  });
  const d = q.data;
  const firstUser = d?.messages.find((m) => m.role === "user" && m.content?.trim());
  const title = firstUser?.content ? firstLine(firstUser.content) : fallbackTitle;
  return (
    <article className="card chat__thread">
      <div className="card__head">
        <h2>{title}</h2>
        <Pill>{t("只读", "read-only")}</Pill>
        {d ? (
          <Pill>
            {d.model || d.backend}
          </Pill>
        ) : null}
        {d?.device ? <Pill tone="info">{d.device}</Pill> : null}
        <div className="card__actions">
          <button type="button" className="btn btn--text btn--sm" onClick={onBack}>
            {t("回到当前对话", "Back to current chat")}
          </button>
        </div>
      </div>
      {q.isLoading ? (
        <div className="convo">
          <div className="row">
            <span className="spinner" style={{ width: 16, height: 16, borderWidth: 2 }} />
            {t("读取中…", "Loading…")}
          </div>
        </div>
      ) : q.isError ? (
        <div className="convo">
          <ErrorBanner action={<button type="button" className="btn btn--text btn--sm" onClick={() => void q.refetch()}>{t("重试", "Retry")}</button>}>
            {t("读不到这次对话:", "Cannot load this chat: ")}
            {q.error instanceof Error ? q.error.message : String(q.error)}
          </ErrorBanner>
        </div>
      ) : d && d.messages.length === 0 ? (
        <div className="convo">
          <Empty icon="chat" title={t("这次对话是空的", "This chat is empty")} />
        </div>
      ) : d ? (
        <>
          <div className="convo">
            <Messages messages={d.messages} model={d.model} />
          </div>
          <div className="banner chat__readonly">
            <Icon name="info" />
            <p>{t("这是以前的对话,不能在这里接着发消息。「继续」会开一个新对话,并把这次的内容当背景带过去。", "This is an earlier chat and cannot take new messages. Continue opens a new chat that carries this one as background.")}</p>
            <div className="banner__actions">
              <button type="button" className="btn btn--filled btn--sm" onClick={() => onContinue(title, seedFrom(d, lang))}>
                {t("继续", "Continue")}
              </button>
            </div>
          </div>
        </>
      ) : null}
    </article>
  );
}

function Messages({ messages, model }: { messages: SessionMessage[]; model: string }): ReactNode {
  const results = new Map<string, string>();
  for (const m of messages) if (m.role === "tool" && m.tool_call_id) results.set(m.tool_call_id, m.content ?? "");
  return messages.map((m, i) => {
    if (m.role === "user") {
      return (
        <div className="msg msg--user" key={i}>
          <Paras text={m.content ?? ""} />
        </div>
      );
    }
    if (m.role !== "assistant") return null;
    return (
      <div key={i} className="chat__turn">
        {(m.tool_calls ?? []).map((c, j) => {
          const out = c.id ? results.get(c.id) : undefined;
          return <ToolCard key={c.id ?? j} name={c.name ?? "tool"} args={c.arguments} result={out ? parseResult(out) : undefined} />;
        })}
        {m.content?.trim() ? (
          <div className="msg msg--ai">
            <div className="msg__who">
              <Icon name="robot" size="sm" />
              {model}
            </div>
            <Paras text={m.content} />
          </div>
        ) : null}
      </div>
    );
  });
}

/* ---------------- side column ---------------- */

function SourceOnlyCard() {
  const t = useT();
  return (
    <article className="card">
      <CardHead title={t("模型", "Model")} />
      <SourceSwitch />
      <p className="t-body-small t-muted" style={{ margin: 0 }}>
        {t("板上模型还没有登记成对话后端,先在下面的「板上模型」里探测。", "The on-board model is not registered as a chat backend yet; probe the board in the On-board model card below.")}
      </p>
    </article>
  );
}

function ModelCard() {
  const t = useT();
  const a = useAssistant();
  const source = useShell((s) => s.source);
  const model = useApp((s) => s.model);
  const setBackend = useApp((s) => s.setBackend);
  const setModel = useApp((s) => s.setModel);

  const backends = useQuery({ queryKey: ["chat3-backends"], queryFn: ({ signal }) => fetchBackends(signal), staleTime: 60_000, retry: false });
  const models = useQuery({
    queryKey: ["chat3-models", a.backend],
    queryFn: ({ signal }) => fetchBackendModels(a.backend, signal),
    staleTime: 30_000,
    retry: false,
  });

  const names = (backends.data?.backends ?? [])
    .filter((b) => b.status !== "planned" && (source === "api" ? API_BACKENDS.has(b.name) : !API_BACKENDS.has(b.name)))
    .map((b) => b.name);
  if (!names.includes(a.backend)) names.unshift(a.backend);
  const modelList = models.data?.models ?? [];
  const options = model && !modelList.includes(model) ? [model, ...modelList] : modelList;
  const current = backends.data?.backends.find((b) => b.name === a.backend);

  return (
    <article className="card">
      <CardHead title={t("模型", "Model")} />
      <SourceSwitch />
      {backends.isError ? (
        <ErrorBanner>{t("读不到后端列表:", "Cannot load backends: ")}{backends.error instanceof Error ? backends.error.message : ""}</ErrorBanner>
      ) : backends.isLoading ? (
        <div className="row"><span className="spinner" style={{ width: 16, height: 16, borderWidth: 2 }} />{t("读取中…", "Loading…")}</div>
      ) : names.length === 0 ? (
        <Empty icon="server" title={t("没有可用的后端", "No backends")}>
          {t("hub 没有登记这一类后端。", "The hub has no backend of this kind registered.")}
        </Empty>
      ) : (
        <>
          <label className="field">
            <span className="field__label">{t("后端", "Backend")}</span>
            <select
              className="select"
              value={a.backend}
              onChange={(e) => {
                setBackend(e.target.value);
                setModel(null);
              }}
            >
              {names.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span className="field__label">{t("模型", "Model")}</span>
            <select className="select" value={model ?? ""} onChange={(e) => setModel(e.target.value || null)} disabled={models.isLoading}>
              <option value="">{t("后端默认", "backend default")}</option>
              {options.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </label>
          {models.isError ? (
            <p className="t-body-small t-muted" style={{ margin: 0 }}>
              {t("读不到这个后端的模型列表(服务可能没开)。", "Cannot list this backend's models (the service may be down).")}
            </p>
          ) : null}
          {current && !current.supports_tool_calls ? (
            <p className="t-body-small t-muted" style={{ margin: 0 }}>
              {t("这个后端不支持工具调用,Agent 模式对它没有效果。", "This backend does not support tool calls; agent mode has no effect on it.")}
            </p>
          ) : null}
        </>
      )}
      <p className="t-body-small t-muted" style={{ margin: 0 }}>
        {t("上下文用量:后端没有回报。", "Context usage: the backend does not report it.")}
      </p>
      {source === "api" ? (
        <p className="t-body-small t-muted" style={{ margin: 0 }}>
          {t("用云端 API 时,你的提问和工具读到的日志都会发到云端服务。", "With a cloud API, your questions and the logs tools read are sent to the cloud service.")}
        </p>
      ) : null}
    </article>
  );
}

/** Tools that can change the board, judged by name (display hint only). */
export const CHANGES_BOARD = /(reboot|install|uninstall|clear_data|app_stop|push|rsync|flash|root|remount|shell|uart_send|uart_shell|sleep_wake)/;

const CAT_ICON: Record<string, string> = { logs: "logs", log: "logs", diag: "bug", diagnose: "bug", shell: "terminal", app: "apps", power: "power", file: "folder", files: "folder", device: "phone", uart: "plug", serial: "plug", ui: "monitor", info: "info" };

function ToolsCard() {
  const t = useT();
  const agentMode = useShell((s) => s.agentMode);
  const setAgentMode = useShell((s) => s.setAgentMode);
  const tools = useQuery({ queryKey: ["chat3-tools"], queryFn: ({ signal }) => fetchTools(signal), staleTime: 5 * 60_000, retry: false });

  const byCat = new Map<string, ToolEntry[]>();
  for (const x of tools.data?.tools ?? []) byCat.set(x.category, [...(byCat.get(x.category) ?? []), x]);

  return (
    <article className="card">
      <CardHead
        title={t("工具", "Tools")}
        sub={t("Agent 模式", "agent mode")}
        actions={
          <input
            type="checkbox"
            className="switch"
            checked={agentMode}
            aria-label={t("Agent 模式", "Agent mode")}
            onChange={(e) => {
              setAgentMode(e.target.checked);
              toast(e.target.checked ? t("Agent 模式开:模型可以调用 alb 工具。", "Agent mode on: the model may call alb tools.") : t("Agent 模式关:只聊天,不调用工具。", "Agent mode off: chat only."));
            }}
          />
        }
      />
      {tools.isLoading ? (
        <div className="row"><span className="spinner" style={{ width: 16, height: 16, borderWidth: 2 }} />{t("读取中…", "Loading…")}</div>
      ) : tools.isError ? (
        <ErrorBanner action={<button type="button" className="btn btn--text btn--sm" onClick={() => void tools.refetch()}>{t("重试", "Retry")}</button>}>
          {t("读不到工具列表:", "Cannot load the tool list: ")}
          {tools.error instanceof Error ? tools.error.message : ""}
        </ErrorBanner>
      ) : byCat.size === 0 ? (
        <Empty icon="wrench" title={t("没有登记的工具", "No tools registered")} />
      ) : (
        <ul className="list tools-list">
          {[...byCat.entries()].map(([cat, items]) => {
            const risky = items.filter((x) => CHANGES_BOARD.test(x.name));
            const names = items.slice(0, 2).map((x) => x.name).join(" · ") + (items.length > 2 ? ` +${items.length - 2}` : "");
            return (
              <li className="list__item" key={cat} title={items.map((x) => x.name).join("\n")}>
                <span className="list__lead">
                  <Icon name={CAT_ICON[cat] ?? "wrench"} size="sm" />
                </span>
                <code className="list__title">{names}</code>
                <span className="list__trail">{agentMode ? <Pill tone="ok">{t("直接跑", "runs")}</Pill> : <Pill>{t("未开启", "off")}</Pill>}</span>
                <span className="list__meta">
                  {cat}
                  {risky.length ? (
                    <>
                      {" · "}
                      <Pill tone="warn" title={risky.map((x) => x.name).join("\n")}>
                        {t(`${risky.length} 个会改动板子`, `${risky.length} change the board`)}
                      </Pill>
                    </>
                  ) : null}
                </span>
              </li>
            );
          })}
        </ul>
      )}
      <p className="t-body-small t-muted" style={{ margin: 0 }}>
        {t("工具调用前不会再问你;每次调用都显示在对话里。", "Tools run without asking first; every call shows in the chat.")}
      </p>
    </article>
  );
}
