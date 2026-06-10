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
 *
 * AO-3 redesign (5/25 第三轮 audit HIGH-3 + HIGH-4):
 *   - `ChatMessage.meta` distinguishes "real assistant content" from
 *     "cancelled partial" / "error placeholder". `onSend` filters
 *     meta-tagged messages OUT before building the LLM messages
 *     payload, so cancelled / errored entries can't pollute the
 *     conversation context the model sees.
 *   - Terminal path (cancel / ws-close / ws-error / server error) is
 *     handled in ONE `useEffect` watching `chat.settled` (the
 *     discriminated union from useWsChatStream AO-1). Partial delta
 *     is stashed identically across cancel / error paths — no
 *     dual-render arm.
 *   - `.playground-msg__partial` arm and the `chat.status === "error"
 *     && chat.done` block are gone. `playground-msg--cancelled` /
 *     `playground-msg--errored` carry the visual difference.
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

/** UI-side message. `meta` tags messages that exist only for user
 *  display — partial cancel / error markers — so they're stripped
 *  before being sent to the LLM as conversation history. */
export interface ChatMessage {
  role: "user" | "assistant" | "system";
  content: string;
  meta?: { kind: "cancelled" | "errored" };
}

/** Project a log array down to the {role, content} shape the LLM API
 *  expects, with cancelled / errored markers removed. The LLM only
 *  sees real user prompts + real assistant replies.
 *
 *  Exported for unit testing in PlaygroundPage.test.ts — this is the
 *  critical security boundary that keeps cancel/error markers out of
 *  the model's context (5/25 第三轮 ui-f HIGH-1 fix). */
export function llmMessagesFrom(log: ChatMessage[]): Array<{
  role: "user" | "assistant" | "system";
  content: string;
}> {
  return log
    .filter((m) => !m.meta)
    .map((m) => ({ role: m.role, content: m.content }));
}

export function PlaygroundPage() {
  const lang = useApp((s) => s.lang);
  const backendsQ = useBackends();
  const backends = backendsQ.data?.backends ?? [];
  const [backend, setBackend] = useState<string>("");
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
  // Per-turn metrics survive chat.reset() here (UIF-01 第十轮): the
  // settled effect resets the stream right after promoting the done
  // content, so anything rendered off `chat.status === "done"` lives
  // for ≤1 frame. NOT stored on ChatMessage.meta — meta is the
  // "strip from LLM payload" marker and would drop the real reply
  // from conversation history.
  const [lastMetrics, setLastMetrics] = useState<{
    metrics: Record<string, unknown>;
    finishReason: string | null;
  } | null>(null);
  const [input, setInput] = useState<string>("");

  const chat = usePlaygroundChat();

  const logRef = useRef<HTMLDivElement>(null);
  const barRef = useRef<HTMLFormElement>(null);
  const chatRef = useRef<HTMLElement>(null);
  const [stickToBottom, setStickToBottom] = useState(true);
  const lastPromptRef = useRef<string>("");
  // Tracks the last `chat.settled` ref we processed in the terminal
  // effect — guards against the effect re-firing while we're inside
  // it (e.g. chat.reset() flushes settled→null which triggers another
  // render before the next start).
  const handledSettledRef = useRef<typeof chat.settled>(null);

  // 5/25 ui-f MID-11 (AM-1): keep --chat-bar-height in sync with the
  // actual bar height (textarea has `resize: vertical`, so user-drag
  // changes it). Reads barRef.offsetHeight via ResizeObserver and
  // writes the CSS var on the parent so the absolute-positioned jump
  // button stays clear of the bar.
  useEffect(() => {
    const bar = barRef.current;
    const chatEl = chatRef.current;
    if (!bar || !chatEl) return;
    const apply = () => {
      chatEl.style.setProperty("--chat-bar-height", `${bar.offsetHeight}px`);
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
    // deps cover what grows the log; we omit other PlaygroundPage
    // state (model / sampling knobs) on purpose.
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
    setStickToBottom(true);
    // New turn — the previous turn's metrics no longer describe the
    // bottom of the log.
    setLastMetrics(null);
    const req: PlaygroundRequest = {
      backend,
      model: model || null,
      messages: llmMessagesFrom(next),
      ...(system ? { system } : {}),
      temperature,
      top_p: topP,
      num_predict: numPredict,
    };
    chat.send(req);
  };

  // Terminal-path effect (AO-3). Replaces the prior split
  // [done-only useEffect + onCancel partial stash + .playground-msg
  // --error render-time branch] with ONE handler keyed on
  // chat.settled. Pushes meta-tagged log entries for cancelled /
  // errored paths so they're visible to the user but excluded from
  // future LLM payloads (HIGH-4 fix).
  useEffect(() => {
    const info = chat.settled;
    if (!info) {
      handledSettledRef.current = null;
      return;
    }
    if (handledSettledRef.current === info) return;
    handledSettledRef.current = info;

    if (info.kind === "done") {
      // success — promote the done.content to a real assistant turn.
      if (chat.done?.ok && chat.done.content) {
        const content = chat.done.content;
        setLog((l) => {
          const last = l[l.length - 1];
          if (last && last.role === "assistant" && last.content === content) {
            return l;
          }
          return [...l, { role: "assistant", content }];
        });
      }
      // Stash metrics BEFORE reset — reset nulls chat.done, and the
      // metrics block must outlive it (UIF-01).
      if (chat.done?.metrics) {
        setLastMetrics({
          metrics: chat.done.metrics,
          finishReason: chat.done.finish_reason || null,
        });
      }
      chat.reset();
      return;
    }

    if (info.kind === "cancelled") {
      if (chat.delta) {
        const partial = chat.delta;
        setLog((l) => [
          ...l,
          { role: "assistant", content: partial, meta: { kind: "cancelled" } },
        ]);
      }
      chat.reset();
      return;
    }

    // info.kind === "error" (source: server / ws-close / ws-error)
    // Stash partial (if any) + the error message as separate meta-
    // tagged entries. Both stay visible; neither feeds the next
    // messages payload.
    if (chat.delta) {
      const partial = chat.delta;
      setLog((l) => [
        ...l,
        { role: "assistant", content: partial, meta: { kind: "errored" } },
      ]);
    }
    if (chat.done?.error) {
      const e = chat.done.error;
      const msg = `${e.code}: ${e.message}`;
      setLog((l) => [
        ...l,
        { role: "assistant", content: msg, meta: { kind: "errored" } },
      ]);
    }
    // On error, restore the user's prompt so retry is one click
    // (ui-f LOW-2 preserved). Only restore when input is empty so a
    // follow-up edit isn't clobbered.
    if (lastPromptRef.current) {
      setInput((cur) => (cur ? cur : lastPromptRef.current));
    }
    chat.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chat.settled]);

  const onClear = () => {
    setLog([]);
    setLastMetrics(null);
    chat.reset();
    setStickToBottom(true);
  };

  const onJumpToBottom = () => {
    setStickToBottom(true);
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
          {log.map((m, i) => {
            const classes = ["playground-msg", `playground-msg--${m.role}`];
            if (m.meta?.kind === "cancelled") {
              classes.push("playground-msg--cancelled");
            } else if (m.meta?.kind === "errored") {
              classes.push("playground-msg--errored");
            }
            return (
              <div key={i} className={classes.join(" ")}>
                <span className="playground-msg__role">
                  {m.role}
                  {m.meta?.kind === "cancelled" &&
                    (lang === "zh"
                      ? " · 已取消（未发给模型）"
                      : " · cancelled (not sent to model)")}
                  {m.meta?.kind === "errored" &&
                    (lang === "zh"
                      ? " · 错误（未发给模型）"
                      : " · error (not sent to model)")}
                </span>
                <pre className="playground-msg__body">{m.content}</pre>
              </div>
            );
          })}
          {chat.status === "streaming" && (
            <div className="playground-msg playground-msg--assistant playground-msg--streaming">
              <span className="playground-msg__role">assistant</span>
              <pre className="playground-msg__body">
                {chat.delta}
                <span className="playground-msg__cursor">▍</span>
              </pre>
            </div>
          )}
          {lastMetrics && (
            <div className="playground-chat__metrics">
              {Object.entries(lastMetrics.metrics).map(([k, v]) => (
                <span key={k} className="playground-chat__metric">
                  {k}: {String(v)}
                </span>
              ))}
              {lastMetrics.finishReason && (
                <span className="playground-chat__metric">
                  finish: {lastMetrics.finishReason}
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
              if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
                e.preventDefault();
                if (chat.status !== "streaming") onSend();
                return;
              }
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
