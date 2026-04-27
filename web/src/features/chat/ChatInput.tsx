/**
 * Composer at the bottom of the Chat page.
 * Wraps a .chat-input frame; primary button is .anth-button, the Stop
 * action swaps to a danger-styled ghost variant.
 */
import { Send, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useApp } from "../../stores/app";

interface Props {
  isStreaming: boolean;
  onSend: (msg: string) => void;
  onCancel: () => void;
}

export function ChatInput({ isStreaming, onSend, onCancel }: Props) {
  const lang = useApp((s) => s.lang);
  const [value, setValue] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!isStreaming) ref.current?.focus();
  }, [isStreaming]);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 220)}px`;
  }, [value]);

  const submit = () => {
    const trimmed = value.trim();
    if (!trimmed || isStreaming) return;
    onSend(trimmed);
    setValue("");
  };

  return (
    <form
      className="chat-input"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <textarea
        ref={ref}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            submit();
          }
        }}
        rows={1}
        disabled={isStreaming}
        placeholder={
          isStreaming
            ? lang === "zh"
              ? "正在响应… 按 Stop 中断"
              : "Streaming… press Stop to interrupt"
            : lang === "zh"
              ? "提问、要求、或描述要做什么 (Enter 发送, Shift+Enter 换行)"
              : "Ask anything about the device (Enter to send, Shift+Enter for newline)"
        }
      />

      {isStreaming ? (
        <button
          type="button"
          className="anth-button"
          style={{
            background: "var(--anth-danger)",
            padding: "8px 18px",
            fontSize: 13,
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
          }}
          onClick={onCancel}
          aria-label="Stop"
        >
          <Square size={12} fill="currentColor" />
          {lang === "zh" ? "停止" : "Stop"}
        </button>
      ) : (
        <button
          type="submit"
          className="anth-button"
          style={{
            padding: "8px 18px",
            fontSize: 13,
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            opacity: value.trim() ? 1 : 0.5,
          }}
          disabled={!value.trim()}
          aria-label="Send"
        >
          <Send size={12} />
          {lang === "zh" ? "发送" : "Send"}
        </button>
      )}
    </form>
  );
}
