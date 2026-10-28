/**
 * The two terminal panes of Console. Both reuse the existing session
 * hooks (features/inspect/useTerminalSession for `WS /terminal/ws`,
 * useUartStream for `WS /uart/stream`); only the chrome is new.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { HitlConfirmModal } from "../../../components/HitlConfirmModal";
import { Icon } from "../../../components/v3/Icon";
import { Confirm, Dot, ErrorBanner, type Tone, toast } from "../../../components/v3/ui";
import { breakIn, type NdjsonEvent } from "../../../lib/api3";
import { useT } from "../../../lib/i18n";
import { useApp } from "../../../stores/app";
import { type HitlRequest, useTerminalSession } from "../../inspect/useTerminalSession";
import { useUartStream } from "../../inspect/useUartStream";
import { copyToClipboard, downloadText, promptOf, useXterm } from "./useXterm";

/** What the page can ask a pane to do (saved commands "Run"). */
export interface PaneApi {
  /** Type text into the terminal; false when the session is not connected. */
  send: (text: string) => boolean;
  focus: () => void;
}

interface PaneProps {
  id: string;
  active: boolean;
  register: (id: string, api: PaneApi | null) => void;
}

const ENC = new TextEncoder();

function stamp(): string {
  return new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
}

/** Clear / copy / download buttons shared by both bars. */
function TermTools({ clear, copy, download }: { clear: () => void; copy: () => void; download: () => void }) {
  const t = useT();
  return (
    <>
      <button type="button" className="icon-btn icon-btn--sm" aria-label={t("清屏", "Clear")} title={t("清屏", "Clear")} onClick={clear}>
        <Icon name="close" size="sm" />
      </button>
      <button type="button" className="icon-btn icon-btn--sm" aria-label={t("复制", "Copy")} title={t("复制选中的文字,没选就复制全部", "Copy the selection, or everything if nothing is selected")} onClick={copy}>
        <Icon name="copy" size="sm" />
      </button>
      <button type="button" className="icon-btn icon-btn--sm" aria-label={t("下载记录", "Download transcript")} title={t("下载记录", "Download transcript")} onClick={download}>
        <Icon name="download" size="sm" />
      </button>
    </>
  );
}

function useTermActions(x: ReturnType<typeof useXterm>, name: string) {
  const t = useT();
  return {
    clear: () => x.clear(),
    copy: () => {
      const text = x.copyText();
      if (!text) {
        toast(t("终端里还没有内容。", "The terminal is empty."));
        return;
      }
      void copyToClipboard(text).then((ok) => toast(ok ? t("已复制。", "Copied.") : t("浏览器不让复制,请手动选中后用 Ctrl+C。", "The browser refused; select the text and press Ctrl+C.")));
    },
    download: () => {
      const text = x.transcript();
      if (!text) {
        toast(t("终端里还没有内容。", "The terminal is empty."));
        return;
      }
      if (!downloadText(`alb-${name}-${stamp()}.txt`, text)) toast(t("保存失败。", "Could not save the file."));
    },
  };
}

/* ---------------- adb shell ---------------- */

export function AdbPane({ id, active, register }: PaneProps) {
  const t = useT();
  const device = useApp((s) => s.device);
  const session = useTerminalSession();
  const { state, error, exitCode, connect, disconnect, sendBytes, sendResize, onBytes, onHitl, respondHitl } = session;
  const [pendingHitl, setPendingHitl] = useState<HitlRequest | null>(null);
  const [prompt, setPrompt] = useState<string | null>(null);
  const [usedDevice, setUsedDevice] = useState<string | null>(null);

  const x = useXterm({
    convertEol: false, // the PTY already sends CRLF
    cursorBlink: true,
    active,
    onData: (d) => sendBytes(d),
    onResize: (rows, cols) => sendResize(rows, cols),
  });
  const actions = useTermActions(x, "adb");

  useEffect(
    () =>
      onBytes((chunk) => {
        x.write(new Uint8Array(chunk));
      }),
    [onBytes, x],
  );

  // Prompt shown in the status bar: read the cursor line shortly after output settles.
  useEffect(() => {
    let timer = 0;
    const off = onBytes(() => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => setPrompt(promptOf(x.termRef.current)), 200);
    });
    return () => {
      window.clearTimeout(timer);
      off();
    };
  }, [onBytes, x]);

  useEffect(() => onHitl((req) => setPendingHitl(req)), [onHitl]);
  // A modal for a dead socket would be a silent no-op: drop it.
  useEffect(() => {
    if (state !== "ready" && pendingHitl) setPendingHitl(null);
  }, [state, pendingHitl]);

  useEffect(() => {
    if (state === "ready") {
      const { rows, cols } = x.size();
      sendResize(rows, cols);
    }
  }, [state, sendResize, x]);

  const start = useCallback(() => {
    if (!device) return;
    const { rows, cols } = x.size();
    setUsedDevice(device);
    setPrompt(null);
    connect({ device, rows, cols, readOnly: false });
    window.setTimeout(() => x.focus(), 50);
  }, [device, connect, x]);

  // Open the shell as soon as the page has a device (once per pane).
  const autoRef = useRef(false);
  useEffect(() => {
    if (device && !autoRef.current) {
      autoRef.current = true;
      start();
    }
  }, [device, start]);

  const live = state === "ready" || state === "connecting";
  useEffect(() => {
    register(id, {
      send: (text) => {
        if (state !== "ready") return false;
        sendBytes(text);
        x.focus();
        return true;
      },
      focus: () => x.focus(),
    });
    return () => register(id, null);
  }, [id, register, state, sendBytes, x]);

  const tone: Tone = state === "ready" ? "ok" : state === "connecting" ? "busy" : state === "error" ? "bad" : "idle";
  const root = prompt?.endsWith("#");
  const label =
    state === "ready"
      ? [t("已连接", "connected"), prompt ? (root ? "root" : t("普通用户", "shell user")) : null, prompt].filter(Boolean).join(" · ")
      : state === "connecting"
        ? t("连接中…", "connecting…")
        : state === "ended"
          ? t(`已结束${exitCode != null ? `(exit ${exitCode})` : ""}`, `ended${exitCode != null ? ` (exit ${exitCode})` : ""}`)
          : state === "error"
            ? t("出错", "error")
            : t("未连接", "not connected");

  return (
    <div id={`t-${id}`} role="tabpanel" hidden={!active} style={{ display: active ? "contents" : "none" }}>
      <div className="console__bar">
        <Dot tone={tone} />
        <span>{label}</span>
        {usedDevice && live ? <code className="mono">{usedDevice}</code> : null}
        {live ? (
          <button type="button" className="btn btn--text btn--sm" onClick={disconnect}>
            {t("断开", "Disconnect")}
          </button>
        ) : (
          <button type="button" className="btn btn--text btn--sm" onClick={start} disabled={!device}>
            {state === "idle" ? t("连接", "Connect") : t("重新连接", "Reconnect")}
          </button>
        )}
        <span className="row">
          <TermTools {...actions} />
        </span>
      </div>
      {!device ? (
        <div className="banner banner--warn console__note">
          <Icon name="alert" />
          <p>{t("没有选中的设备,adb shell 连不上。板子在 bootloader 或 adb 不通时,用串口页签。", "No device selected, so adb shell cannot connect. When the board is in its bootloader or adb is down, use the serial tab.")}</p>
          <span />
        </div>
      ) : null}
      {device && error ? (
        <div className="console__note">
          <ErrorBanner action={<button type="button" className="btn btn--text btn--sm" onClick={start}>{t("重试", "Retry")}</button>}>
            {error}
          </ErrorBanner>
        </div>
      ) : null}
      {device && !error && state === "ended" ? (
        <div className="banner banner--warn console__note">
          <Icon name="alert" />
          <p>{t("会话已结束。板子重启、adb 掉线或你输入了 exit 都会这样。", "The session ended: the board rebooted, adb dropped, or you typed exit.")}</p>
          <div className="banner__actions">
            <button type="button" className="btn btn--text btn--sm" onClick={start}>{t("重新连接", "Reconnect")}</button>
          </div>
        </div>
      ) : null}
      <div className="term term--x">
        <div className="term__host" ref={x.hostRef} />
      </div>
      <HitlConfirmModal
        open={pendingHitl !== null}
        title={t("命令需要审批", "Command needs your approval")}
        description={t(
          "服务端的守卫拦下了一条命令。批准一次只放这一次;批准本会话则这个 shell 里同类命令不再问。",
          "The server guard held back a command. Approve once lets this one through; approve for session lets matching commands through for the rest of this shell.",
        )}
        details={pendingHitl ? { command: <code>{pendingHitl.command}</code>, rule: pendingHitl.rule, reason: pendingHitl.reason } : undefined}
        cancelLabel={t("拒绝", "Deny")}
        approveLabel={t("本次批准", "Approve once")}
        approveSessionLabel={t("本会话批准", "Approve for session")}
        approveDanger
        onCancel={() => {
          respondHitl(false, false);
          setPendingHitl(null);
        }}
        onApprove={() => {
          respondHitl(true, false);
          setPendingHitl(null);
        }}
        onApproveSession={() => {
          respondHitl(true, true);
          setPendingHitl(null);
        }}
      />
    </div>
  );
}

/* ---------------- serial ---------------- */

export interface SerialInfo {
  com: string | null;
  baud: number | null;
  readers: number | null;
}

// Keystrokes reach the board only while this is on. A key pressed by
// accident during a boot stops U-Boot's autoboot countdown and leaves the
// board sitting at its prompt, so v2 kept typing off until asked; the
// choice is remembered per browser. Buttons (Ctrl+C, snippets, break-in)
// are deliberate and are not gated by it.
const TYPING_KEY = "alb.console.serialTyping";

function readTyping(): boolean {
  try {
    return localStorage.getItem(TYPING_KEY) === "1";
  } catch {
    return false;
  }
}

export function SerialPane({ id, active, register, info }: PaneProps & { info: SerialInfo }) {
  const t = useT();
  const device = useApp((s) => s.device);
  const { state, error, writeEnabled, connect, disconnect, onBytes, sendBytes } = useUartStream();
  const [askBreak, setAskBreak] = useState(false);
  const [breaking, setBreaking] = useState(false);
  const [typing, setTypingState] = useState(readTyping);
  const hinted = useRef(false);
  const setTyping = (on: boolean) => {
    setTypingState(on);
    hinted.current = false;
    try {
      localStorage.setItem(TYPING_KEY, on ? "1" : "0");
    } catch {
      // storage blocked: the switch still works for this page
    }
  };

  const x = useXterm({
    convertEol: true,
    cursorBlink: false,
    active,
    onData: (d) => {
      if (!writeEnabled) return;
      if (typing) {
        sendBytes(ENC.encode(d));
      } else if (!hinted.current) {
        hinted.current = true;
        note(t("键盘输入是关的,按键没有发给板子。要敲命令,先打开上面的「键盘输入」。", "Typing is off, so that key was not sent to the board. Turn on Typing above to type commands."));
      }
    },
  });
  const actions = useTermActions(x, "serial");

  useEffect(
    () =>
      onBytes((chunk) => {
        x.write(new Uint8Array(chunk));
      }),
    [onBytes, x],
  );

  const start = useCallback(() => {
    connect(device, { write: true });
  }, [connect, device]);

  const autoRef = useRef(false);
  useEffect(() => {
    if (!autoRef.current) {
      autoRef.current = true;
      start();
    }
  }, [start]);

  useEffect(() => {
    register(id, {
      send: (text) => {
        if (state !== "ready" || !writeEnabled) return false;
        sendBytes(ENC.encode(text));
        x.focus();
        return true;
      },
      focus: () => x.focus(),
    });
    return () => register(id, null);
  }, [id, register, state, writeEnabled, sendBytes, x]);

  function note(text: string) {
    x.write(`\r\n\x1b[2m[alb] ${text}\x1b[0m\r\n`);
  }
  const clearAndStart = () => {
    x.clear();
    start();
  };

  const doBreak = async () => {
    setBreaking(true);
    note(t("开始监听串口,重启后在倒计时里按键…", "Listening on the serial line; will press a key in the countdown after the reboot…"));
    const onEvent = (ev: NdjsonEvent) => {
      if (ev.ev === "progress" && ev.phase === "ready") {
        note(t(`监听已就绪,重启方式:${String(ev.reboot ?? "手动")}`, `Listener is up; reboot via: ${String(ev.reboot ?? "by hand")}`));
      }
    };
    try {
      const done = await breakIn(device, "auto", onEvent);
      if (done.ok) {
        note(t("已停在 bootloader 提示符。", "Stopped at the bootloader prompt."));
        toast(t("已停在 bootloader。", "Stopped in the bootloader."));
      } else {
        const e = done.error as { code?: string; message?: string; suggestion?: string } | undefined;
        note(`${e?.code ?? "ERROR"}: ${e?.message ?? ""}${e?.suggestion ? ` (${e.suggestion})` : ""}`);
        toast(`${e?.code ?? "ERROR"}`);
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      note(msg);
      toast(msg);
    } finally {
      setBreaking(false);
      setAskBreak(false);
    }
  };

  const tone: Tone = state === "ready" ? "ok" : state === "connecting" ? "busy" : state === "error" ? "bad" : "idle";
  const live = state === "ready" || state === "connecting";
  const link = [info.com, info.baud ? `${info.baud} baud` : null, info.readers != null ? t(`${info.readers} 个读者`, `${info.readers} reader(s)`) : null].filter(Boolean).join(" · ");
  const label =
    state === "ready" ? t("已连接", "connected") : state === "connecting" ? t("连接中…", "connecting…") : state === "ended" ? t("已结束", "ended") : state === "error" ? t("出错", "error") : t("未连接", "not connected");

  return (
    <div id={`t-${id}`} role="tabpanel" hidden={!active} style={{ display: active ? "contents" : "none" }}>
      <div className="console__bar">
        <Dot tone={tone} />
        <span>{label}</span>
        {link ? <span className="mono">{link}</span> : null}
        {state === "ready" && !writeEnabled ? <span className="pill pill--warn">{t("只读", "read-only")}</span> : null}
        {writeEnabled ? (
          <label className="row" title={t("关着时按键不会发给板子,防止误按停住开机倒计时", "While off, keys are not sent, so a stray key cannot stop the boot countdown")}>
            <input type="checkbox" className="switch" checked={typing} onChange={(e) => setTyping(e.target.checked)} aria-label={t("键盘输入", "Typing")} />
            <span>{t("键盘输入", "Typing")}</span>
          </label>
        ) : null}
        {live ? (
          <button type="button" className="btn btn--text btn--sm" onClick={disconnect}>
            {t("断开", "Disconnect")}
          </button>
        ) : (
          <button type="button" className="btn btn--text btn--sm" onClick={start}>
            {state === "idle" ? t("连接", "Connect") : t("重新连接", "Reconnect")}
          </button>
        )}
        <span className="row">
          <button type="button" className="btn btn--text btn--sm" onClick={() => sendBytes(new Uint8Array([3]))} disabled={!writeEnabled}>
            {t("发送 Ctrl+C", "Send Ctrl+C")}
          </button>
          <button type="button" className="btn btn--tonal btn--sm" onClick={() => setAskBreak(true)} disabled={breaking || state !== "ready"}>
            <Icon name="stop" size="sm" />
            {t("停在 bootloader", "Stop in bootloader")}
          </button>
          <TermTools {...actions} />
        </span>
      </div>
      {askBreak ? (
        <div className="console__note">
          <Confirm
            open
            busy={breaking}
            message={t(
              "重启板子,并在 bootloader 的倒计时里按键停下。板子会重启,adb 会断开,进行中的工作会丢。结果会写在下面的终端里。",
              "Reboot the board and press a key in the bootloader countdown. adb drops and unsaved work is lost. The result is written into the terminal below.",
            )}
            confirmLabel={t("重启并停下", "Reboot and stop")}
            cancelLabel={t("取消", "Cancel")}
            onConfirm={() => void doBreak()}
            onCancel={() => setAskBreak(false)}
          />
        </div>
      ) : null}
      {error ? (
        <div className="console__note">
          <ErrorBanner
            action={
              <>
                <button type="button" className="btn btn--text btn--sm" onClick={start}>{t("重试", "Retry")}</button>
                <button type="button" className="btn btn--text btn--sm" onClick={clearAndStart}>{t("清屏并重连", "Clear and reconnect")}</button>
              </>
            }
          >
            {error}
          </ErrorBanner>
        </div>
      ) : null}
      {!error && state === "ended" ? (
        <div className="banner banner--warn console__note">
          <Icon name="alert" />
          <p>{t("串口连接已结束:串口线被拔掉,或台架代理断开了。", "The serial stream ended: the cable was unplugged or the bench agent dropped.")}</p>
          <div className="banner__actions">
            <button type="button" className="btn btn--text btn--sm" onClick={start}>{t("重新连接", "Reconnect")}</button>
            <button type="button" className="btn btn--text btn--sm" onClick={clearAndStart}>{t("清屏并重连", "Clear and reconnect")}</button>
          </div>
        </div>
      ) : null}
      <div className="term term--x">
        <div className="term__host" ref={x.hostRef} />
      </div>
    </div>
  );
}
