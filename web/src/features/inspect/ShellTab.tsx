/**
 * adb shell PTY terminal tab (PR-E + v2 HITL modal).
 *
 * Bidirectional xterm.js: stdin from xterm.onData → ws.send, stdout
 * from ws bytes → term.write. Supports server-side resize via
 * sendResize() bound to the FitAddon's resize event.
 *
 * v2 (2026-05-02): hitl_request frames open the shared
 * <HitlConfirmModal>. Approve once / approve for session / deny
 * routes through respondHitl. Replaces v1 silent auto-deny.
 */

import { useEffect, useRef, useState } from "react";
import { CircleStop, Eraser, Play } from "lucide-react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";

import { HitlConfirmModal } from "../../components/HitlConfirmModal";
import { useApp } from "../../stores/app";
import { type HitlRequest, useTerminalSession } from "./useTerminalSession";

export function ShellTab() {
  const lang = useApp((s) => s.lang);
  const device = useApp((s) => s.device);
  const session = useTerminalSession();
  const {
    state,
    error,
    exitCode,
    connect,
    disconnect,
    sendBytes,
    sendResize,
    onBytes,
    onHitl,
    respondHitl,
  } = session;
  const [pendingHitl, setPendingHitl] = useState<HitlRequest | null>(null);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);

  // SECURITY (DEBT-027 / security audit 2026-05-02 LOW 5):
  // adb shell stdout flows raw into term.write() — same OSC injection
  // surface as UartLiveStream. Keep xterm options on defaults only.
  // Do NOT add `allowProposedApi: true` (enables OSC 52 clipboard
  // write) or expose `linkHandler` that opens external URLs without
  // a confirmation prompt. See L-027 for the broader HITL/OSC threat
  // model.
  useEffect(() => {
    if (!containerRef.current) return;
    const term = new Terminal({
      fontFamily: "var(--font-mono), Menlo, Consolas, monospace",
      fontSize: 12,
      lineHeight: 1.25,
      cursorBlink: true,
      convertEol: false, // PTY produces CRLF — let xterm interpret literally
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

    // Pipe user keystrokes → server.
    const dataDisposable = term.onData((data) => sendBytes(data));

    // Resize the server-side PTY when the visible window grows/shrinks.
    let raf = 0;
    const onResize = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        try {
          fit.fit();
          sendResize(term.rows, term.cols);
        } catch {
          // ignore early resize before container is laid out
        }
      });
    };
    window.addEventListener("resize", onResize);

    termRef.current = term;
    fitRef.current = fit;

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", onResize);
      dataDisposable.dispose();
      term.dispose();
      termRef.current = null;
      fitRef.current = null;
    };
  }, [sendBytes, sendResize]);

  // Wire incoming bytes → terminal.
  useEffect(() => {
    return onBytes((chunk) => {
      const term = termRef.current;
      if (!term) return;
      term.write(new Uint8Array(chunk));
    });
  }, [onBytes]);

  // Wire HITL prompts → modal. We keep at most one in flight; if the
  // server fires multiple back-to-back (rare — terminal_guard pauses
  // until response) the second overwrites the first only after the
  // first is responded to (handler clears state before opening the
  // next).
  useEffect(() => {
    return onHitl((req) => setPendingHitl(req));
  }, [onHitl]);

  // functional audit HIGH 8: WS dies while HITL modal is open ⇒ Approve
  // becomes a silent no-op (respondHitl bails when readyState != OPEN).
  // Clear the pending HITL on disconnect so user sees the session ended
  // pill instead of a frozen modal — they can re-Connect and re-issue.
  useEffect(() => {
    if (state !== "ready" && pendingHitl) setPendingHitl(null);
  }, [state, pendingHitl]);

  const closeHitl = (approve: boolean, allowSession: boolean) => {
    respondHitl(approve, allowSession);
    setPendingHitl(null);
  };

  // After session becomes ready, push current rows/cols once so the
  // server PTY matches the visible window.
  useEffect(() => {
    if (state === "ready") {
      const term = termRef.current;
      if (term) sendResize(term.rows, term.cols);
    }
  }, [state, sendResize]);

  const onConnect = () => {
    const term = termRef.current;
    connect({
      device,
      rows: term?.rows ?? 30,
      cols: term?.cols ?? 100,
      readOnly: false,
    });
    setTimeout(() => term?.focus(), 50);
  };
  const onClear = () => termRef.current?.clear();

  const stateLabel: Record<typeof state, string> = {
    idle: lang === "zh" ? "未连接" : "idle",
    connecting: lang === "zh" ? "连接中…" : "connecting…",
    ready: lang === "zh" ? "在线" : "live",
    ended: lang === "zh" ? `已结束${exitCode != null ? ` (exit ${exitCode})` : ""}` : `ended${exitCode != null ? ` (exit ${exitCode})` : ""}`,
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

      <HitlConfirmModal
        open={pendingHitl !== null}
        title={lang === "zh" ? "命令需要审批" : "Command needs your approval"}
        description={
          lang === "zh"
            ? "服务端 HITL 守卫拦下了一条危险命令。批准一次只放本次，整个会话则后续同型命令直通。"
            : "Server-side HITL guard blocked a dangerous command. Approve once for this run; approve for session lets matching commands through for the rest of this shell."
        }
        details={
          pendingHitl
            ? {
                command: <code>{pendingHitl.command}</code>,
                rule: pendingHitl.rule,
                reason: pendingHitl.reason,
              }
            : undefined
        }
        cancelLabel={lang === "zh" ? "拒绝" : "Deny"}
        approveLabel={lang === "zh" ? "本次批准" : "Approve once"}
        approveSessionLabel={
          lang === "zh" ? "本会话批准" : "Approve for session"
        }
        approveDanger
        onCancel={() => closeHitl(false, false)}
        onApprove={() => closeHitl(true, false)}
        onApproveSession={() => closeHitl(true, true)}
      />
    </div>
  );
}
