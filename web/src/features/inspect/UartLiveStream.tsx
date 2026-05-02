/**
 * Live UART byte stream viewer (PR-C.b).
 *
 * Mounts an xterm.js terminal in a container <div>, plumbs WS-arrived
 * bytes into `term.write()`, and exposes Connect / Disconnect / Clear
 * controls. Uses the FitAddon so the terminal resizes to the panel.
 *
 * Pairs with <UartTab>'s "Capture" view (PR-C.a, REST-style) to give
 * users both eventless replay and live observation modes.
 */

import { useEffect, useRef, useState } from "react";
import { CircleStop, Eraser, Keyboard, Play } from "lucide-react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";

import { useApp } from "../../stores/app";
import { useUartStream } from "./useUartStream";

interface Props {
  device: string | null;
}

export function UartLiveStream({ device }: Props) {
  const lang = useApp((s) => s.lang);
  const { state, error, writeEnabled, connect, disconnect, onBytes, sendBytes } =
    useUartStream();
  // Toggle remembered across connect/disconnect — defaults OFF since
  // wrong byte at the wrong time can hang u-boot or sync to disk.
  const [writeMode, setWriteMode] = useState(false);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const enc = useRef(new TextEncoder());

  // Mount xterm once.
  // SECURITY (DEBT-027 / security audit 2026-05-02 LOW 5):
  // device UART bytes are forwarded raw into term.write(). xterm.js
  // interprets ANSI/OSC sequences. We DELIBERATELY use only the
  // default xterm options below — do NOT add `allowProposedApi: true`
  // or enable OSC 52 / OSC 4-104 handlers. With defaults, the only
  // OSC operations that fire are window-title (OSC 0/2, harmless)
  // and color queries (read-only). OSC 52 (clipboard write) is gated
  // behind allowProposedApi and stays off; turning it on would let a
  // hostile UART source silently rewrite the user's clipboard.
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

    // Resize on window resize — cheap, debounced via rAF.
    let raf = 0;
    const onResize = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        try {
          fit.fit();
        } catch {
          // ignore until container is laid out
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

  // Wire WS bytes → terminal.
  useEffect(() => {
    return onBytes((chunk) => {
      const term = termRef.current;
      if (!term) return;
      term.write(new Uint8Array(chunk));
    });
  }, [onBytes]);

  // Wire xterm keystrokes → WS write (bidirectional mode only).
  // Subscribe ONLY when write actually negotiated, so read-only
  // sessions don't accidentally fire sendBytes() no-ops on every key.
  useEffect(() => {
    const term = termRef.current;
    if (!term || !writeEnabled) return;
    const sub = term.onData((data: string) => {
      sendBytes(enc.current.encode(data));
    });
    return () => sub.dispose();
  }, [writeEnabled, sendBytes]);

  const onConnect = () => connect(device, { write: writeMode });
  const onClear = () => termRef.current?.clear();
  // Recover from `error` / `ended` cleanly — wipe stale bytes from
  // xterm before reopening the WS so the user doesn't conflate fresh
  // output with the pre-error log line that triggered the failure.
  const onClearAndReconnect = () => {
    termRef.current?.clear();
    connect(device, { write: writeMode });
  };

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

  return (
    <div className="uart-tab">
      <div className="uart-tab__bar">
        {state !== "ready" && state !== "connecting" ? (
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
            <Eraser size={12} style={{ verticalAlign: "-2px" }} />{" "}
            {lang === "zh" ? "清屏并重连" : "Clear & reconnect"}
          </button>
        )}
        <label className="uart-tab__write-toggle">
          <input
            type="checkbox"
            checked={writeMode}
            onChange={(e) => setWriteMode(e.target.checked)}
            disabled={state === "ready" || state === "connecting"}
          />
          <Keyboard size={12} style={{ verticalAlign: "-2px", marginLeft: 4 }} />{" "}
          {lang === "zh" ? "允许键盘输入" : "Allow input"}
          <span className="uart-tab__write-hint">
            {lang === "zh" ? "误键可能锁板" : "wrong byte can lock board"}
          </span>
        </label>
        <span className={stateClass[state]}>● {stateLabel[state]}</span>
        {writeEnabled && (
          <span className="uart-tab__state uart-tab__state--write-on">
            {lang === "zh" ? "可写入" : "WRITE"}
          </span>
        )}
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
