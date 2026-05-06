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
import { CircleStop, Eraser, Play } from "lucide-react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";

import { useApp } from "../../stores/app";
import { useLogcatStream } from "./useLogcatStream";

const FILTER_DEBOUNCE_MS = 600;

export function LogcatTab() {
  const lang = useApp((s) => s.lang);
  const device = useApp((s) => s.device);
  const [filter, setFilter] = useState("");
  const { state, error, connect, disconnect, onBytes } = useLogcatStream();
  // Tracks the filter spec the current live stream was started with.
  // Used to skip reconnect when the effect re-runs purely because of
  // state churn (ready→connecting→ready) without a real edit.
  const lastAppliedFilter = useRef<string>("");

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
      const term = termRef.current;
      if (!term) return;
      term.write(new Uint8Array(chunk));
    });
  }, [onBytes]);

  const onConnect = useCallback(() => {
    const f = filter.trim();
    lastAppliedFilter.current = f;
    connect({ device, filter: f || null });
  }, [connect, device, filter]);
  const onClear = () => termRef.current?.clear();

  // Debounced auto-reconnect: when the user edits filter while a stream
  // is already live, wait FILTER_DEBOUNCE_MS for typing to settle then
  // reconnect with the new spec. Only when state==='ready' — not in
  // idle/connecting/error/ended (avoids reconnect loops + lets users
  // recover from errors manually).
  useEffect(() => {
    if (state !== "ready") return;
    const trimmed = filter.trim();
    if (trimmed === lastAppliedFilter.current) return;
    const t = window.setTimeout(() => {
      lastAppliedFilter.current = trimmed;
      connect({ device, filter: trimmed || null });
    }, FILTER_DEBOUNCE_MS);
    return () => window.clearTimeout(t);
  }, [filter, state, device, connect]);

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
        <label className="uart-tab__duration" style={{ minWidth: 220 }}>
          {lang === "zh" ? "Filter" : "Filter"}
          <input
            type="text"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="*:E  /  MyTag:V *:S"
            style={{ width: 200 }}
          />
        </label>
        {state === "ready" && filter.trim() !== lastAppliedFilter.current && (
          <span className="uart-tab__last">
            {lang === "zh" ? "应用中…" : "applying…"}
          </span>
        )}
        {!isLive ? (
          <button
            type="button"
            className="btn btn--primary"
            onClick={onConnect}
            disabled={!device}
          >
            <Play size={12} style={{ verticalAlign: "-2px" }} />{" "}
            {lang === "zh" ? "连接" : "Connect"}
          </button>
        ) : (
          <button type="button" className="btn" onClick={disconnect}>
            <CircleStop size={12} style={{ verticalAlign: "-2px" }} />{" "}
            {lang === "zh" ? "断开" : "Disconnect"}
          </button>
        )}
        <button type="button" className="btn" onClick={onClear}>
          <Eraser size={12} style={{ verticalAlign: "-2px" }} />{" "}
          {lang === "zh" ? "清屏" : "Clear"}
        </button>
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
