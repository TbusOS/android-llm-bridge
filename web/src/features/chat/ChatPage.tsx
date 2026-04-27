/**
 * Chat — agent loop conversation panel.
 */
import { useState } from "react";
import { RefreshCw, Trash2, Wrench } from "lucide-react";
import { useApp } from "../../stores/app";
import { ChatInput } from "./ChatInput";
import { MessageList } from "./MessageList";
import { useChatStream } from "./useChatStream";

export function ChatPage() {
  const lang = useApp((s) => s.lang);
  const backend = useApp((s) => s.backend);
  const model = useApp((s) => s.model);

  const [toolsOn, setToolsOn] = useState(true);

  const { turns, sessionId, isStreaming, send, cancel, retry, reset } =
    useChatStream({ backend, model, tools: toolsOn });

  const lastTurn = turns[turns.length - 1];
  const showThinking =
    isStreaming &&
    lastTurn?.role === "assistant" &&
    lastTurn.status === "pending";
  const canRetry =
    !isStreaming &&
    !!turns.find((t) => t.role === "user") &&
    (lastTurn?.status === "error" || lastTurn?.status === "cancelled");

  return (
    <section>
      <div className="section-head">
        <h1>{lang === "zh" ? "Chat 对话" : "Chat"}</h1>
        <span className="status-pill status-pill--wip">
          {lang === "zh" ? "已就绪" : "Ready"}
        </span>
        <span
          style={{
            marginLeft: "auto",
            display: "inline-flex",
            gap: "var(--space-2)",
            alignItems: "center",
          }}
        >
          <label
            className="pill-btn"
            style={{ cursor: "pointer", borderColor: "transparent" }}
            title={lang === "zh" ? "开关 MCP 工具" : "Toggle MCP tools"}
          >
            <input
              type="checkbox"
              checked={toolsOn}
              onChange={(e) => setToolsOn(e.target.checked)}
              disabled={isStreaming}
              style={{ marginRight: 4 }}
            />
            <Wrench size={13} />
            {lang === "zh" ? "工具" : "tools"}
          </label>
          <button
            type="button"
            className="pill-btn"
            onClick={retry}
            disabled={!canRetry}
            title={lang === "zh" ? "重试上一条" : "Retry last"}
          >
            <RefreshCw size={13} />
            {lang === "zh" ? "重试" : "Retry"}
          </button>
          <button
            type="button"
            className="pill-btn"
            onClick={reset}
            disabled={turns.length === 0 || isStreaming}
            title={lang === "zh" ? "清空会话" : "Clear conversation"}
          >
            <Trash2 size={13} />
            {lang === "zh" ? "清空" : "Clear"}
          </button>
        </span>
      </div>

      <p className="section-sub">
        {lang === "zh"
          ? "和能操作设备的 LLM 对话。token 流式、tool 调用可折叠、产物内联。"
          : "Talk to an LLM that can drive the device. Streaming tokens, expandable tool calls, inline artifacts."}
        <span
          style={{
            marginLeft: "var(--space-3)",
            fontFamily: "var(--font-mono)",
            fontSize: 12,
            color: "var(--anth-text-secondary)",
          }}
        >
          backend={backend}
          {model ? ` · model=${model}` : ""}
          {sessionId ? ` · session=${shortId(sessionId)}` : ""}
        </span>
      </p>

      <div
        className="mock-card"
        style={{
          minHeight: "min(560px, 60vh)",
          maxHeight: "calc(100vh - 280px)",
          overflowY: "auto",
        }}
      >
        <MessageList turns={turns} showPending={showThinking} />
      </div>

      <div style={{ marginTop: "var(--space-4)" }}>
        <ChatInput isStreaming={isStreaming} onSend={send} onCancel={cancel} />
      </div>
    </section>
  );
}

function shortId(id: string): string {
  return id.length > 16 ? `${id.slice(0, 8)}…${id.slice(-4)}` : id;
}
