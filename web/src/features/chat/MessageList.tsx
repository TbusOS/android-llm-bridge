/**
 * Render a list of ChatTurns in the .chat-window flexbox column.
 * Each turn is a .bubble (user / assistant), optionally preceded by
 * .tool-call cards and followed by .artifact-row chips.
 */
import { Paperclip, Slash, AlertTriangle } from "lucide-react";
import { useEffect, useRef } from "react";
import { useApp } from "../../stores/app";
import { ToolCallCard } from "./ToolCallCard";
import type { ChatTurn } from "./types";

interface Props {
  turns: ChatTurn[];
  showPending: boolean;
}

export function MessageList({ turns, showPending }: Props) {
  const lang = useApp((s) => s.lang);
  const tailRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    tailRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns, showPending]);

  if (turns.length === 0) {
    return (
      <div className="chat-empty">
        {lang === "zh"
          ? '起一个对话试试 —— 比如 "查一下当前设备的 IP" 或 "看一下电池状态"。'
          : 'Start a conversation — try “show me the device IP” or “check battery status”.'}
      </div>
    );
  }

  return (
    <div className="chat-window">
      {turns.map((t) => (
        <TurnView key={t.id} turn={t} lang={lang} />
      ))}
      {showPending && (
        <div className="thinking">
          <span className="typing-dots">
            <span /> <span /> <span />
          </span>{" "}
          {lang === "zh" ? "等待响应…" : "Waiting for the model…"}
        </div>
      )}
      <div ref={tailRef} />
    </div>
  );
}

function TurnView({ turn, lang }: { turn: ChatTurn; lang: "en" | "zh" }) {
  if (turn.role === "user") {
    return <div className="bubble bubble--user">{turn.content}</div>;
  }

  return (
    <>
      {turn.toolCalls.map((tc) => (
        <ToolCallCard key={tc.id} entry={tc} />
      ))}

      {turn.content && (
        <div className="bubble bubble--assistant">{turn.content}</div>
      )}

      {turn.artifacts.length > 0 && (
        <div className="artifact-row">
          {turn.artifacts.map((p) => (
            <ArtifactChip key={p} path={p} />
          ))}
        </div>
      )}

      {turn.error && (
        <ErrorBlock
          error={turn.error}
          cancelled={turn.status === "cancelled"}
          lang={lang}
        />
      )}

      {(turn.status === "done" || turn.status === "error") &&
        (turn.timingMs || turn.model) && (
          <div className="chat-meta">
            {turn.model && <span>{turn.model}</span>}
            {turn.model && turn.timingMs ? <span> · </span> : null}
            {turn.timingMs && <span>{(turn.timingMs / 1000).toFixed(2)}s</span>}
          </div>
        )}
    </>
  );
}

function ArtifactChip({ path }: { path: string }) {
  const name = path.split("/").pop() || path;
  return (
    <span className="artifact-chip" title={path}>
      <Paperclip size={12} />
      {name}
    </span>
  );
}

function ErrorBlock({
  error,
  cancelled,
  lang,
}: {
  error: { code: string; message: string; suggestion?: string };
  cancelled: boolean;
  lang: "en" | "zh";
}) {
  return (
    <div className={cancelled ? "chat-error is-cancelled" : "chat-error"}>
      {cancelled ? <Slash size={14} /> : <AlertTriangle size={14} />}
      <div style={{ minWidth: 0 }}>
        <div>
          <code>{error.code}</code>
        </div>
        <div style={{ wordBreak: "break-word" }}>{error.message}</div>
        {error.suggestion && (
          <div
            style={{
              marginTop: 4,
              color: "var(--anth-text-secondary)",
            }}
          >
            {lang === "zh" ? "提示：" : "Hint: "}
            {error.suggestion}
          </div>
        )}
      </div>
    </div>
  );
}
