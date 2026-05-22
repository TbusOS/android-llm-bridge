/**
 * PlaygroundPage — replaces the v1 StubPage at `/playground`.
 *
 * Two-column layout (DEBT-046 to add right metrics rail later):
 *   LEFT  — backend / model selector + sampling knobs (temp / top_p /
 *           num_predict / system prompt)
 *   MAIN  — chat panel: messages list + input box + send/cancel.
 *           Per-turn metrics (tokens/s, finish_reason) currently
 *           render inline under the done message.
 *
 * Narrow viewports (<900 px): rail stacks above the chat (see CSS
 * `.playground-page` media query).
 *
 * Wires `usePlayground` for catalog data and `usePlaygroundChat` for
 * the WS streaming protocol. Multi-turn: the parent keeps the message
 * log; the hook owns only the in-flight request.
 */
import { useEffect, useState } from "react";

import { useApp } from "../../stores/app";
import {
  useBackendModels,
  useBackends,
} from "./usePlayground";
import {
  usePlaygroundChat,
  type PlaygroundRequest,
} from "./usePlaygroundChat";

interface ChatMessage {
  role: "user" | "assistant" | "system";
  content: string;
}

export function PlaygroundPage() {
  const lang = useApp((s) => s.lang);
  const backendsQ = useBackends();
  const backends = backendsQ.data?.backends ?? [];
  const [backend, setBackend] = useState<string>("");
  // Auto-pick the first registered backend once the list arrives.
  useEffect(() => {
    if (!backend && backends.length > 0) {
      setBackend(backends[0]!.name);
    }
  }, [backend, backends]);

  const modelsQ = useBackendModels(backend || null);
  const models = modelsQ.data?.models ?? [];
  const [model, setModel] = useState<string>("");

  const [temperature, setTemperature] = useState<number>(0.7);
  const [topP, setTopP] = useState<number>(0.9);
  const [numPredict, setNumPredict] = useState<number>(512);
  const [system, setSystem] = useState<string>("");

  const [log, setLog] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState<string>("");

  const chat = usePlaygroundChat();

  const onSend = () => {
    const text = input.trim();
    if (!text || chat.status === "streaming") return;
    const next: ChatMessage[] = [...log, { role: "user", content: text }];
    setLog(next);
    setInput("");
    const req: PlaygroundRequest = {
      backend,
      model: model || null,
      messages: next,
      ...(system ? { system } : {}),
      temperature,
      top_p: topP,
      num_predict: numPredict,
    };
    chat.send(req);
  };

  // When the stream finishes, append the assistant message to the log
  // and reset the chat hook. deps capture only what we actually read:
  // - chat.status (primitive transition trigger)
  // - chat.done (the payload we read on done)
  // Using the full `chat` object would refire the effect every render
  // because the hook returns a fresh object each call (state + new
  // useCallback identity on send/cancel/reset). `chat.reset` is stable
  // because the hook builds it with useCallback([]) — safe to call
  // without including in deps.
  useEffect(() => {
    if (chat.status !== "done" || !chat.done || !chat.done.ok) return;
    const content = chat.done.content;
    if (!content) {
      chat.reset();
      return;
    }
    setLog((l) => {
      const last = l[l.length - 1];
      if (last && last.role === "assistant" && last.content === content) return l;
      return [...l, { role: "assistant", content }];
    });
    chat.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chat.status, chat.done]);

  const onClear = () => {
    setLog([]);
    chat.reset();
  };

  return (
    <section className="playground-page">
      <aside className="playground-rail">
        <h2 className="playground-rail__title">
          {lang === "zh" ? "后端 / 模型" : "backend · model"}
        </h2>

        <label className="playground-rail__field">
          <span>backend</span>
          <select
            value={backend}
            onChange={(e) => {
              setBackend(e.target.value);
              setModel("");
            }}
            disabled={backendsQ.isLoading}
          >
            {backendsQ.isLoading && <option>loading…</option>}
            {backends.map((b) => (
              <option key={b.name} value={b.name}>
                {b.name}
                {b.host_compute_type ? ` · ${b.host_compute_type}` : ""}
              </option>
            ))}
          </select>
        </label>

        <label className="playground-rail__field">
          <span>model</span>
          {models.length > 0 ? (
            <select
              value={model}
              onChange={(e) => setModel(e.target.value)}
            >
              <option value="">{lang === "zh" ? "(默认)" : "(default)"}</option>
              {models.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          ) : (
            <input
              type="text"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder={
                modelsQ.isLoading
                  ? "loading…"
                  : modelsQ.isError
                    ? lang === "zh"
                      ? "拉不到 · 手填"
                      : "fetch failed · type"
                    : lang === "zh"
                      ? "手填模型名"
                      : "type model name"
              }
            />
          )}
        </label>

        <h2 className="playground-rail__title playground-rail__title--mt">
          {lang === "zh" ? "采样" : "sampling"}
        </h2>

        <label className="playground-rail__field">
          <span>temperature ({temperature.toFixed(2)})</span>
          <input
            type="range"
            min={0}
            max={2}
            step={0.05}
            value={temperature}
            onChange={(e) => setTemperature(Number(e.target.value))}
          />
        </label>

        <label className="playground-rail__field">
          <span>top_p ({topP.toFixed(2)})</span>
          <input
            type="range"
            min={0}
            max={1}
            step={0.01}
            value={topP}
            onChange={(e) => setTopP(Number(e.target.value))}
          />
        </label>

        <label className="playground-rail__field">
          <span>num_predict</span>
          <input
            type="number"
            min={1}
            max={8192}
            value={numPredict}
            onChange={(e) =>
              setNumPredict(Math.max(1, Number(e.target.value) || 512))
            }
          />
        </label>

        <label className="playground-rail__field">
          <span>system</span>
          <textarea
            rows={3}
            value={system}
            onChange={(e) => setSystem(e.target.value)}
            placeholder={
              lang === "zh"
                ? "system prompt · 可空"
                : "system prompt · optional"
            }
          />
        </label>
      </aside>

      <main className="playground-chat">
        <header className="playground-chat__head">
          <h1 className="h-title">
            {lang === "zh" ? "Playground 调试台" : "Playground"}
          </h1>
          <button
            type="button"
            className="playground-chat__clear"
            onClick={onClear}
            disabled={chat.status === "streaming"}
          >
            {lang === "zh" ? "清空" : "clear"}
          </button>
        </header>

        <div className="playground-chat__log">
          {log.length === 0 && chat.status === "idle" && (
            <div className="playground-chat__empty">
              {lang === "zh"
                ? "没有消息 · 试试在下方输入。"
                : "No messages yet — type below to start."}
            </div>
          )}
          {log.map((m, i) => (
            <div
              key={i}
              className={`playground-msg playground-msg--${m.role}`}
            >
              <span className="playground-msg__role">{m.role}</span>
              <pre className="playground-msg__body">{m.content}</pre>
            </div>
          ))}
          {chat.status === "streaming" && (
            <div className="playground-msg playground-msg--assistant playground-msg--streaming">
              <span className="playground-msg__role">assistant</span>
              <pre className="playground-msg__body">
                {chat.delta}
                <span className="playground-msg__cursor">▍</span>
              </pre>
            </div>
          )}
          {chat.status === "error" && chat.done && (
            <div className="playground-msg playground-msg--error">
              <span className="playground-msg__role">error</span>
              <pre className="playground-msg__body">
                {chat.done.error?.code ?? "ERROR"}:{" "}
                {chat.done.error?.message ?? "(no message)"}
              </pre>
            </div>
          )}
          {chat.status === "done" && chat.done && chat.done.metrics && (
            <div className="playground-chat__metrics">
              {Object.entries(chat.done.metrics).map(([k, v]) => (
                <span key={k} className="playground-chat__metric">
                  {k}: {String(v)}
                </span>
              ))}
              {chat.done.finish_reason && (
                <span className="playground-chat__metric">
                  finish: {chat.done.finish_reason}
                </span>
              )}
            </div>
          )}
        </div>

        <form
          className="playground-chat__bar"
          onSubmit={(e) => {
            e.preventDefault();
            onSend();
          }}
        >
          <textarea
            className="playground-chat__input"
            rows={2}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              // Ctrl/⌘+Enter → send. Plain Enter inserts newline.
              if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
                e.preventDefault();
                if (chat.status !== "streaming") onSend();
                return;
              }
              // ESC during streaming → cancel. Keyboard users would
              // otherwise be locked out (textarea was previously
              // `disabled` which dropped focus, so ESC never fired).
              if (e.key === "Escape" && chat.status === "streaming") {
                e.preventDefault();
                chat.cancel();
              }
            }}
            placeholder={
              chat.status === "streaming"
                ? lang === "zh"
                  ? "生成中 · Esc 取消"
                  : "Streaming… · Esc to cancel"
                : lang === "zh"
                  ? "输入消息 · ⌘+Enter 发送"
                  : "Message… · ⌘+Enter to send"
            }
            // readOnly instead of disabled — keeps focus on the input
            // so Esc can fire (disabled drops focus to body, dead).
            readOnly={chat.status === "streaming"}
            aria-readonly={chat.status === "streaming"}
          />
          {chat.status === "streaming" ? (
            <button
              type="button"
              className="playground-chat__cancel"
              onClick={chat.cancel}
            >
              {lang === "zh" ? "取消" : "cancel"}
            </button>
          ) : (
            <button
              type="submit"
              className="playground-chat__send"
              disabled={!input.trim() || !backend}
            >
              {lang === "zh" ? "发送" : "send"}
            </button>
          )}
        </form>
      </main>
    </section>
  );
}
