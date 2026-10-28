/**
 * The assistant: one conversation shared by the side sheet and the Chat
 * page (mounted once in the shell), rendered as user / tool / answer
 * blocks. Model source "host" and "api" talk through /chat/ws; "board"
 * shows the on-device entry instead of a chat (no on-board backend yet).
 */
import { createContext, type ReactNode, useContext, useEffect, useMemo, useRef, useState } from "react";

import type { ChatTurn, ToolCallEntry } from "../../features/chat/types";
import { useChatStream, type UseChatStreamResult } from "../../features/chat/useChatStream";
import { type OnDeviceProbe, probeOnDevice } from "../../lib/api3";
import { useT } from "../../lib/i18n";
import { useApp } from "../../stores/app";
import { useShell } from "../../stores/shell";
import { Icon } from "./Icon";
import { Pill, toast } from "./ui";

/* ---------------- shared conversation ---------------- */

interface AssistantApi extends UseChatStreamResult {
  backend: string;
  ask: (text: string) => void;
}

const Ctx = createContext<AssistantApi | null>(null);

/** Cloud backends the hub knows; anything else counts as a host model. */
const API_BACKENDS = new Set(["openai-compat", "anthropic", "openai"]);

export function AssistantProvider({ children }: { children: ReactNode }) {
  const appBackend = useApp((s) => s.backend);
  const model = useApp((s) => s.model);
  const source = useShell((s) => s.source);
  const agentMode = useShell((s) => s.agentMode);
  const backend = source === "api" ? (API_BACKENDS.has(appBackend) ? appBackend : "openai-compat") : API_BACKENDS.has(appBackend) ? "ollama" : appBackend;
  const chat = useChatStream({ backend, model, tools: agentMode });
  const context = useShell((s) => s.context);
  const clearContext = useShell((s) => s.clearContext);
  const api = useMemo<AssistantApi>(
    () => ({
      ...chat,
      backend,
      ask: (text: string) => {
        const ctx = context?.detail ? `${context.detail}\n\n` : context ? `[${context.en}]\n\n` : "";
        chat.send(ctx + text);
        if (context) clearContext();
      },
    }),
    [chat, backend, context, clearContext],
  );
  return <Ctx.Provider value={api}>{children}</Ctx.Provider>;
}

export function useAssistant(): AssistantApi {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAssistant needs <AssistantProvider>");
  return v;
}

/* ---------------- rendering ---------------- */

function Paragraphs({ text }: { text: string }) {
  // Plain text only: fenced code becomes a terminal block, the rest
  // paragraphs. Nothing is interpreted as HTML.
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

function ToolCard({ call }: { call: ToolCallEntry }) {
  const t = useT();
  const running = call.status === "running";
  const secs = call.endedAt ? ((call.endedAt - call.startedAt) / 1000).toFixed(1) : null;
  const args = Object.entries(call.arguments ?? {})
    .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(" ");
  const result = call.result === undefined ? "" : typeof call.result === "string" ? call.result : JSON.stringify(call.result, null, 2);
  return (
    <div className="tool">
      {running ? <span className="spinner" style={{ width: 16, height: 16, borderWidth: 2 }} /> : <Icon name="check-circle" size="sm" />}
      <code>
        {call.name}
        {args ? ` ${args}` : ""}
      </code>
      <span className="pill">{running ? t("运行中", "running") : `${secs ?? "—"} s`}</span>
      {result ? (
        <details>
          <summary>{t("看输出", "Show output")}</summary>
          <pre className="term">{result.length > 4000 ? `${result.slice(0, 4000)}\n…` : result}</pre>
        </details>
      ) : null}
    </div>
  );
}

function Turn({ turn }: { turn: ChatTurn }) {
  const t = useT();
  if (turn.role === "user") {
    return (
      <div className="msg msg--user">
        <Paragraphs text={turn.content} />
      </div>
    );
  }
  return (
    <>
      {turn.toolCalls.map((c) => (
        <ToolCard key={c.id} call={c} />
      ))}
      {turn.status === "pending" && !turn.content ? (
        <div className="msg msg--ai">
          <div className="msg__who">
            <span className="spinner" style={{ width: 14, height: 14, borderWidth: 2 }} />
            {t("思考中…", "Thinking…")}
          </div>
        </div>
      ) : null}
      {turn.content || turn.error ? (
        <div className="msg msg--ai">
          <div className="msg__who">
            <Icon name="robot" size="sm" />
            {turn.model ?? ""}
            {turn.timingMs ? ` · ${(turn.timingMs / 1000).toFixed(1)} s` : ""}
          </div>
          {turn.content ? <Paragraphs text={turn.content} /> : null}
          {turn.error ? (
            <div className="banner banner--error">
              <Icon name="error" />
              <p>
                <b>{turn.error.code}</b> {turn.error.message}
                {turn.error.suggestion ? <><br />{turn.error.suggestion}</> : null}
              </p>
              <span />
            </div>
          ) : null}
        </div>
      ) : null}
    </>
  );
}

export function Conversation({ className }: { className?: string }) {
  const { turns } = useAssistant();
  const t = useT();
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "end" });
  }, [turns]);
  return (
    <div className={`convo ${className ?? ""}`}>
      {turns.length === 0 ? (
        <div className="empty">
          <Icon name="spark" />
          <h3>{t("问这块板子", "Ask about this board")}</h3>
          <p>
            {t(
              "比如:上一次开机为什么崩溃?最近有没有 ANR?开了 Agent 模式,模型会自己读日志、调用 alb 工具。",
              "For example: why did the last boot crash? Any ANRs lately? With agent mode on, the model reads logs and runs alb tools itself.",
            )}
          </p>
        </div>
      ) : (
        turns.map((turn) => <Turn key={turn.id} turn={turn} />)
      )}
      <div ref={end} />
    </div>
  );
}

export function Composer({ rows = 2, className }: { rows?: number; className?: string }) {
  const t = useT();
  const { ask, isStreaming, cancel, backend, reset, turns } = useAssistant();
  const model = useApp((s) => s.model);
  const context = useShell((s) => s.context);
  const clearContext = useShell((s) => s.clearContext);
  const [text, setText] = useState("");
  const submit = () => {
    const v = text.trim();
    if (!v || isStreaming) return;
    ask(v);
    setText("");
  };
  return (
    <form
      className={`composer ${className ?? ""}`}
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      {context ? (
        <div className="row">
          <button type="button" className="ctx" onClick={clearContext} title={t("点击移除", "Click to remove")}>
            @ {t(context.zh, context.en)} ×
          </button>
        </div>
      ) : null}
      <label className="visually-hidden" htmlFor="assist-q">
        {t("消息", "Message")}
      </label>
      <textarea
        id="assist-q"
        className="textarea"
        rows={rows}
        value={text}
        placeholder={t("问这块板子…Enter 发送,Shift+Enter 换行", "Ask about this board… Enter sends, Shift+Enter adds a line")}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            submit();
          }
        }}
      />
      <div className="row row--between">
        <span className="t-body-small t-muted mono">
          {model ?? backend} · {backend}
        </span>
        <div className="row">
          {turns.length ? (
            <button type="button" className="btn btn--text btn--sm" onClick={reset}>
              {t("新对话", "New chat")}
            </button>
          ) : null}
          {isStreaming ? (
            <button type="button" className="btn btn--outlined btn--sm" onClick={cancel}>
              <Icon name="stop" size="sm" />
              {t("停止", "Stop")}
            </button>
          ) : (
            <button type="submit" className="btn btn--filled btn--sm" disabled={!text.trim()}>
              <Icon name="send" size="sm" />
              {t("发送", "Send")}
            </button>
          )}
        </div>
      </div>
    </form>
  );
}

/* ---------------- model source + on-device ---------------- */

export function SourceSwitch({ className }: { className?: string }) {
  const t = useT();
  const source = useShell((s) => s.source);
  const setSource = useShell((s) => s.setSource);
  const opts: [typeof source, string, string, string][] = [
    ["host", "monitor", "本机", "Host"],
    ["api", "link", "云端 API", "Cloud API"],
    ["board", "chip", "板上", "On board"],
  ];
  return (
    <div className={`seg seg--sm ${className ?? ""}`} role="group" aria-label={t("模型来源", "Model source")}>
      {opts.map(([k, ic, zh, en]) => (
        <button key={k} type="button" aria-pressed={source === k} onClick={() => setSource(k)}>
          <Icon name={ic} size="sm" />
          {t(zh, en)}
        </button>
      ))}
    </div>
  );
}

export function OnDevicePanel() {
  const t = useT();
  const device = useApp((s) => s.device);
  const [busy, setBusy] = useState(false);
  const [found, setFound] = useState<OnDeviceProbe[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const probe = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await probeOnDevice(device);
      if (r.ok && r.data) setFound(r.data);
      else setErr(`${r.error?.code ?? "ERROR"} ${r.error?.message ?? ""}`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  const servers = found?.filter((f) => f.ok) ?? [];
  return (
    <div className="ondev">
      <h3>{t("和板子上跑的模型对话", "Talk to a model running on the board")}</h3>
      <p>
        {t(
          "用来试端侧推理:效果、首字延迟、每秒 token 数。alb 经设备端口通道连到板上的推理服务,台架 agent 不用改。",
          "For trying on-device inference: quality, first-token latency, tokens per second. alb reaches the board's local server through the device port channel; the bench agent does not change.",
        )}
      </p>
      <ul className="list">
        {(found ?? [
          { port: 8080, ok: false, kind: null, models: [], error: null },
          { port: 8000, ok: false, kind: null, models: [], error: null },
          { port: 11434, ok: false, kind: null, models: [], error: null },
        ]).map((p) => (
          <li className="list__item" key={p.port}>
            <span className="list__lead">
              <Icon name="chip" />
            </span>
            <span className="list__title mono">tcp:{p.port}</span>
            <span className="list__trail">
              {found === null ? <Pill>{t("未探测", "not probed")}</Pill> : p.ok ? <Pill tone="ok">{p.kind}</Pill> : <Pill>{t("没有服务", "nothing")}</Pill>}
            </span>
            <span className="list__meta">{p.ok ? p.models.join(" · ") || t("没有模型", "no models") : p.error ?? (p.port === 11434 ? "Ollama" : "llama.cpp / OpenAI-compatible")}</span>
          </li>
        ))}
      </ul>
      {err ? (
        <div className="banner banner--error">
          <Icon name="error" />
          <p>{err}</p>
          <span />
        </div>
      ) : null}
      {servers.length ? (
        <div className="banner banner--ok">
          <Icon name="check-circle" />
          <p>
            {t(
              `找到 ${servers.length} 个板上服务。直接对话还要在 hub 里登记这个后端,下一版接上。`,
              `Found ${servers.length} server(s) on the board. Chatting with it needs the backend registered in the hub; that comes next.`,
            )}
          </p>
          <span />
        </div>
      ) : null}
      <div>
        <button type="button" className="btn btn--tonal btn--sm" onClick={probe} disabled={busy}>
          {busy ? <span className="spinner" style={{ width: 14, height: 14, borderWidth: 2 }} /> : <Icon name="search" size="sm" />}
          {t("探测板上服务", "Look for a server on the board")}
        </button>
      </div>
    </div>
  );
}

/* ---------------- side sheet ---------------- */

export function AssistantSheet({ onOpenFull }: { onOpenFull: () => void }) {
  const t = useT();
  const open = useShell((s) => s.sheetOpen);
  const close = useShell((s) => s.closeSheet);
  const source = useShell((s) => s.source);
  const agentMode = useShell((s) => s.agentMode);
  const setAgentMode = useShell((s) => s.setAgentMode);
  const device = useApp((s) => s.device);
  if (!open) return null;
  return (
    <aside className="sheet" aria-label={t("助手", "Assistant")}>
      <div className="sheet__head">
        <Icon name="spark" />
        <h2>{t("助手", "Assistant")}</h2>
        <span className="pill pill--info">{device ?? t("未选设备", "no device")}</span>
        <button type="button" className="icon-btn icon-btn--sm" aria-label={t("全屏打开", "Open full screen")} title={t("全屏打开", "Open full screen")} onClick={onOpenFull}>
          <Icon name="external" size="sm" />
        </button>
        <button type="button" className="icon-btn icon-btn--sm" aria-label={t("关闭", "Close")} onClick={close}>
          <Icon name="close" size="sm" />
        </button>
      </div>
      <SourceSwitch className="sheet__src" />
      <label className="sheet__agent">
        <input type="checkbox" className="switch" checked={agentMode} onChange={(e) => {
          setAgentMode(e.target.checked);
          toast(e.target.checked ? t("Agent 模式开:模型可以调用 alb 工具。", "Agent mode on: the model may call alb tools.") : t("Agent 模式关:只聊天,不调用工具。", "Agent mode off: chat only."));
        }} />
        <span>
          <b>{t("Agent 模式", "Agent mode")}</b>
          <small>{t("模型可以调用 alb 工具读日志、跑命令,每次调用都显示在对话里。", "The model may call alb tools to read logs and run commands; every call shows in the chat.")}</small>
        </span>
      </label>
      {source === "board" ? (
        <div className="sheet__body">
          <OnDevicePanel />
        </div>
      ) : (
        <>
          <div className="sheet__body">
            <Conversation />
          </div>
          <div className="sheet__input">
            <Composer />
          </div>
        </>
      )}
    </aside>
  );
}
