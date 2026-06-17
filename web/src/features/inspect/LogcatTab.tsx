/**
 * Live logcat stream viewer (PR-D).
 *
 * Mirror of <UartLiveStream> but for adb logcat. Adds a filter input
 * (typed `*:E` / `Tag:V *:S` / etc) so users can scope the stream.
 *
 * v2 (functional LOW-5): debounced auto-reconnect on filter edit.
 * Changing filter while live no longer requires Disconnect → Connect;
 * 600 ms after the last keystroke the hook reconnects with the new
 * filter spec. `lastAppliedFilter` ref guards against the state-churn
 * self-loop that an `[filter, state]` effect would otherwise cause
 * (state ready→connecting→ready re-triggers the effect; we skip when
 * the filter hasn't actually changed since the last applied value).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { CircleStop, Eraser, Pause, Play } from "lucide-react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";

import { useApp } from "../../stores/app";
import { useLogcatStream } from "./useLogcatStream";

const FILTER_DEBOUNCE_MS = 600;
const APPLIED_PILL_MS = 1500;

// Single-letter logcat severity threshold. "" means no level constraint
// (full filter input is authoritative); any other value composes
// `*:<LEVEL>` on top of (or in lieu of) the freeform filter.
const LEVELS = ["", "V", "D", "I", "W", "E", "F"] as const;
type Level = (typeof LEVELS)[number];

function composeFilter(level: Level, freeform: string): string {
  const f = freeform.trim();
  if (!level) return f;
  // If user wrote their own filter, prepend the level — `*:<LEVEL>` is
  // documented to combine with per-tag specs sanely.
  return f ? `${f} *:${level}` : `*:${level}`;
}

export function LogcatTab() {
  const lang = useApp((s) => s.lang);
  const device = useApp((s) => s.device);
  const [filter, setFilter] = useState("");
  const [level, setLevel] = useState<Level>("");
  // Pause = stop writing to xterm. Bytes continue flowing on the WS and
  // are dropped (no buffer) — matches user expectation of "freeze the
  // screen so I can read", not "give me the full backlog later".
  const [paused, setPaused] = useState(false);
  const { state, error, connect, disconnect, onBytes } = useLogcatStream();
  // Tracks the filter spec the current live stream was started with.
  // Used to skip reconnect when the effect re-runs purely because of
  // state churn (ready→connecting→ready) without a real edit.
  const lastAppliedFilter = useRef<string>("");
  // ui-fluency 2026-05-07 LOW-2: positive feedback after debounce
  // settles. `appliedAt` stores the most-recent reconnect timestamp;
  // a derived `justApplied` flag drives a short-lived "applied" pill
  // so the user can see the new filter took effect.
  const [appliedAt, setAppliedAt] = useState<number>(0);
  const [justApplied, setJustApplied] = useState(false);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const term = new Terminal({
      fontFamily: "var(--font-mono), Menlo, Consolas, monospace",
      fontSize: 12,
      lineHeight: 1.25,
      cursorBlink: false,
      convertEol: true,
      scrollback: 5000,
      theme: {
        background: "#1e1e1e",
        foreground: "#e0e0e0",
        cursor: "#e0e0e0",
      },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(containerRef.current);
    fit.fit();
    termRef.current = term;
    fitRef.current = fit;

    let raf = 0;
    const onResize = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        try {
          fit.fit();
        } catch {
          // ignore
        }
      });
    };
    window.addEventListener("resize", onResize);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", onResize);
      term.dispose();
      termRef.current = null;
      fitRef.current = null;
    };
  }, []);

  useEffect(() => {
    return onBytes((chunk) => {
      if (paused) return;
      const term = termRef.current;
      if (!term) return;
      term.write(new Uint8Array(chunk));
    });
  }, [onBytes, paused]);

  const onConnect = useCallback(() => {
    const f = composeFilter(level, filter);
    lastAppliedFilter.current = f;
    connect({ device, filter: f || null });
  }, [connect, device, filter, level]);
  const onClear = () => termRef.current?.clear();
  // UI-5: recover from error / ended cleanly — wipe stale bytes from xterm
  // before reopening so fresh output isn't conflated with the pre-error
  // line that triggered the failure (mirrors UartLiveStream).
  const onClearAndReconnect = () => {
    termRef.current?.clear();
    onConnect();
  };

  // Debounced auto-reconnect: when the user edits filter while a stream
  // is already live, wait FILTER_DEBOUNCE_MS for typing to settle then
  // reconnect with the new spec. Only when state==='ready' — not in
  // idle/connecting/error/ended (avoids reconnect loops + lets users
  // recover from errors manually).
  useEffect(() => {
    if (state !== "ready") return;
    const composed = composeFilter(level, filter);
    if (composed === lastAppliedFilter.current) return;
    const t = window.setTimeout(() => {
      lastAppliedFilter.current = composed;
      connect({ device, filter: composed || null });
      setAppliedAt(Date.now());
    }, FILTER_DEBOUNCE_MS);
    return () => window.clearTimeout(t);
  }, [filter, level, state, device, connect]);

  // Show "applied" pill for APPLIED_PILL_MS after a successful debounce
  // reconnect, then fade. Tied to appliedAt so each reconnect resets the
  // timer.
  useEffect(() => {
    if (!appliedAt) return;
    setJustApplied(true);
    const t = window.setTimeout(
      () => setJustApplied(false),
      APPLIED_PILL_MS,
    );
    return () => window.clearTimeout(t);
  }, [appliedAt]);

  const stateLabel: Record<typeof state, string> = {
    idle: lang === "zh" ? "未连接" : "idle",
    connecting: lang === "zh" ? "连接中…" : "connecting…",
    ready: lang === "zh" ? "实时" : "live",
    ended: lang === "zh" ? "已结束" : "ended",
    error: lang === "zh" ? "错误" : "error",
  };

  const stateClass: Record<typeof state, string> = {
    idle: "uart-tab__state",
    connecting: "uart-tab__state uart-tab__state--warn",
    ready: "uart-tab__state uart-tab__state--ok",
    ended: "uart-tab__state",
    error: "uart-tab__state uart-tab__state--err",
  };

  const isLive = state === "ready" || state === "connecting";

  return (
    <div className="uart-tab">
      <div className="uart-tab__bar">
        <label className="uart-tab__duration">
          {lang === "zh" ? "Level" : "Level"}
          <select
            value={level}
            onChange={(e) => setLevel(e.target.value as Level)}
          >
            <option value="">{lang === "zh" ? "(全部)" : "(all)"}</option>
            <option value="V">V verbose</option>
            <option value="D">D debug</option>
            <option value="I">I info</option>
            <option value="W">W warn</option>
            <option value="E">E error</option>
            <option value="F">F fatal</option>
          </select>
        </label>
        <label className="uart-tab__duration" style={{ minWidth: 220 }}>
          {lang === "zh" ? "Filter" : "Filter"}
          <input
            type="text"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="MyTag:V  /  Foo:* Bar:S"
            style={{ width: 200 }}
          />
        </label>
        {state === "ready" && composeFilter(level, filter) !== lastAppliedFilter.current && (
          <span
            className="uart-tab__last"
            role="status"
            aria-live="polite"
          >
            {lang === "zh" ? "应用中…" : "applying…"}
          </span>
        )}
        {justApplied && composeFilter(level, filter) === lastAppliedFilter.current && (
          <span
            className="uart-tab__last uart-tab__last--ok"
            role="status"
            aria-live="polite"
          >
            {lang === "zh" ? "已应用" : "applied"}
          </span>
        )}
        {!isLive ? (
          <button
            type="button"
            className="btn btn--primary"
            onClick={onConnect}
            disabled={!device}
          >
            <Play size={12} className="icon-inline" />{" "}
            {lang === "zh" ? "连接" : "Connect"}
          </button>
        ) : (
          <button type="button" className="btn" onClick={disconnect}>
            <CircleStop size={12} className="icon-inline" />{" "}
            {lang === "zh" ? "断开" : "Disconnect"}
          </button>
        )}
        {isLive && (
          <button
            type="button"
            className="btn"
            onClick={() => setPaused((p) => !p)}
            aria-pressed={paused}
            title={paused ? "resume rendering" : "freeze the screen"}
          >
            {paused ? (
              <>
                <Play size={12} className="icon-inline" />{" "}
                {lang === "zh" ? "继续" : "Resume"}
              </>
            ) : (
              <>
                <Pause size={12} className="icon-inline" />{" "}
                {lang === "zh" ? "暂停" : "Pause"}
              </>
            )}
          </button>
        )}
        <button type="button" className="btn" onClick={onClear}>
          <Eraser size={12} className="icon-inline" />{" "}
          {lang === "zh" ? "清屏" : "Clear"}
        </button>
        {(state === "error" || state === "ended") && (
          <button
            type="button"
            className="btn btn--primary"
            onClick={onClearAndReconnect}
            disabled={!device}
            title={
              lang === "zh"
                ? "清屏并重连，避免残留字节误读"
                : "wipe stale bytes and reopen the stream"
            }
          >
            <Eraser size={12} className="icon-inline" />{" "}
            {lang === "zh" ? "清屏并重连" : "Clear & reconnect"}
          </button>
        )}
        <span className={stateClass[state]}>● {stateLabel[state]}</span>
        {error && (
          <span className="uart-tab__last uart-tab__last--err">{error}</span>
        )}
        {!device && (
          <span className="uart-tab__last">
            {lang === "zh" ? "未选设备" : "no device selected"}
          </span>
        )}
      </div>

      <div className="uart-tab__body uart-tab__body--single">
        <div ref={containerRef} className="uart-tab__xterm" />
      </div>
    </div>
  );
}
