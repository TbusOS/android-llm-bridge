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
import { useEffect, useRef, useState } from "react";

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

  // 5/25 ui-f MID-2: chat log was a `.playground-chat__log` with
  // `overflow-y:auto` but no scroll-follow — long replies pushed new
  // tokens below the viewport while the user stared at the top. Now
  // we track a `stickToBottom` flag:
  //   - default true → every render scrolls to bottom
  //   - user scrolls up (more than 40 px above bottom) → stick=false,
  //     stops auto-scrolling so reading isn't interrupted
  //   - user scrolls back to bottom → stick=true again
  //   - sending a new prompt → force stick=true so the next reply
  //     starts from a known anchor
  const logRef = useRef<HTMLDivElement>(null);
  const barRef = useRef<HTMLFormElement>(null);
  const chatRef = useRef<HTMLElement>(null);
  const [stickToBottom, setStickToBottom] = useState(true);
  // Snapshot of the most recently sent prompt — used to repopulate
  // the input on error (5/25 ui-f LOW-2). Cleared by user typing
  // anything (we only restore when input is empty), so retry doesn't
  // clobber a follow-up edit.
  const lastPromptRef = useRef<string>("");

  // 5/25 ui-f MID-11: keep --chat-bar-height in sync with the actual
  // bar height (textarea has `resize: vertical`, so user-drag changes
  // it). Reads barRef.offsetHeight via ResizeObserver and writes the
  // CSS var on the parent so the absolute-positioned jump button
  // stays clear of the bar.
  useEffect(() => {
    const bar = barRef.current;
    const chat = chatRef.current;
    if (!bar || !chat) return;
    const apply = () => {
      chat.style.setProperty("--chat-bar-height", `${bar.offsetHeight}px`);
    };
    apply();
    const ro = new ResizeObserver(apply);
    ro.observe(bar);
    return () => ro.disconnect();
  }, []);

  const SCROLL_THRESHOLD_PX = 40;

  useEffect(() => {
    const el = logRef.current;
    if (!el) return;
    if (!stickToBottom) return;
    el.scrollTop = el.scrollHeight;
    // deps intentionally include the things that grow the log
    // (chat.delta during streaming · chat.status terminal hop · log
    // length on send/clear · stickToBottom toggle). Earlier versions
    // omitted deps entirely so every render wrote scrollTop — fine
    // functionally but every keystroke in the input box triggered
    // a DOM write. eslint-disable left in because we deliberately
    // omit any other React state that PlaygroundPage holds (model /
    // sampling knobs) since those don't change log height.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chat.delta, chat.status, log.length, stickToBottom]);

  const onLogScroll = () => {
    const el = logRef.current;
    if (!el) return;
    const distanceFromBottom =
      el.scrollHeight - el.clientHeight - el.scrollTop;
    setStickToBottom(distanceFromBottom <= SCROLL_THRESHOLD_PX);
  };

  const onSend = () => {
    const text = input.trim();
    if (!text || chat.status === "streaming") return;
    const next: ChatMessage[] = [...log, { role: "user", content: text }];
    setLog(next);
    lastPromptRef.current = text;
    setInput("");
    // Re-anchor to bottom: user is sending a new prompt, they want to
    // see the reply from the start.
    setStickToBottom(true);
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
  // and reset the chat hook. Effect deps cover both ends of the
  // stream lifecycle:
  //   - chat.status: primitive transition trigger (idle → streaming
  //     → done | error). Effect runs once per terminal hop.
  //   - chat.done: only mutated on terminal events (setDone in the
  //     hook's onJson + onCloseBeforeDone branches), so the object
  //     identity is stable during streaming. Including it covers the
  //     done payload read inside the effect.
  // chat.reset is intentionally NOT in deps — useCallback([]) keeps
  // its identity stable so we don't need to track it. The exhaustive-
  // deps disable below silences the lint about chat.reset; the alt
  // would be capturing the whole `chat` object which fires the effect
  // on every render (it's a fresh return object each call).
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

  // 5/25 ui-f LOW-2: on error, restore the user's prompt in the
  // input box so retry is one click instead of "scroll up, copy
  // from log, paste, edit". Only runs once per error transition
  // (chat.status as the trigger).
  useEffect(() => {
    if (chat.status === "error" && lastPromptRef.current) {
      setInput((cur) => (cur ? cur : lastPromptRef.current));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chat.status]);

  const onClear = () => {
    setLog([]);
    chat.reset();
    setStickToBottom(true);
  };

  // 5/25 ui-f MID-1: cancel during streaming used to drop the partial
  // delta silently — user lost the 200+ tokens that had already
  // arrived. We now stash whatever's accumulated into the log as a
  // (truncated) assistant message before tearing down the stream so
  // the output is at least visible / copyable.
  const onCancel = () => {
    if (chat.delta) {
      const trailer = lang === "zh" ? " [已取消]" : " [cancelled]";
      const partial = chat.delta;
      setLog((l) => [
        ...l,
        { role: "assistant", content: partial + trailer },
      ]);
    }
    chat.cancel();
  };

  const onJumpToBottom = () => {
    setStickToBottom(true);
    // Force the layout effect — without this the user can be parked
    // at a steady scroll position and clicking the button doesn't
    // trigger a render (state already true).
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
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

      <main className="playground-chat" ref={chatRef}>
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

        <div
          ref={logRef}
          className="playground-chat__log"
          onScroll={onLogScroll}
        >
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
              {/* 5/25 ui-f MID-1: 在错误条之前保留已流的 token · 之前
               *   error 一来 streaming 气泡消失 · 用户失去 200+ token
               *   的输出 · 也没回放路径。chat.delta 仍有上次终止前的
               *   累积 · 显示出来让用户至少能复制 / 看到。 */}
              {chat.delta && (
                <pre className="playground-msg__body playground-msg__partial">
                  {chat.delta}
                </pre>
              )}
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

        {!stickToBottom && (log.length > 0 || chat.status === "streaming") && (
          <button
            type="button"
            className="playground-chat__jump"
            onClick={onJumpToBottom}
            aria-label={
              lang === "zh" ? "回到最新消息" : "Jump to latest message"
            }
          >
            {lang === "zh" ? "回到最新 ↓" : "Latest ↓"}
          </button>
        )}

        <form
          ref={barRef}
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
                onCancel();
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
              onClick={onCancel}
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
