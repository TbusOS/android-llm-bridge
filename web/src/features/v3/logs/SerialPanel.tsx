/**
 * Logs page: the serial tab. Read-only view of the bench's serial port,
 * plus captures to a file and their history.
 *
 * No adb device is needed: the hub forwards the bench's serial port
 * either way, and a board that is in the bootloader or has lost adb is
 * exactly when the serial console matters most.
 */
import { Link } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { Icon } from "../../../components/v3/Icon";
import { LogDialog } from "../../../components/v3/LogDialog";
import { Confirm, Dot, Empty, ErrorBanner, toast } from "../../../components/v3/ui";
import { deleteUartCapture, triggerUartCapture } from "../../../lib/api";
import { fetchBenchBoard } from "../../../lib/api3";
import { copyText } from "../../../lib/clipboard";
import { useDashboardQuery } from "../../../lib/dashboardQuery";
import { formatBytes } from "../../../lib/format";
import { ago, useT } from "../../../lib/i18n";
import { useApp } from "../../../stores/app";
import { useUartStream } from "../../inspect/useUartStream";
import { LineSplitter } from "./logcatModel";
import { capturesKey, type SerialCapture, textLines, useSerialCaptures, useSerialCaptureText } from "./serialCaptures";

/** Lines kept for the live view; the large view shows all of them. */
export const TERM_CAP = 5000;
const DUR_MIN = 1;
const DUR_MAX = 300;
const DUR_DEFAULT = 60;

type LastCapture = { ok: true; filename: string; lines: number; errors: number; owner: string | null } | { ok: false; error: string };

const keyOf = (c: SerialCapture) => `${c.owner ?? ""}/${c.name}`;
const stamp = () => new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);

export function SerialPanel({ device }: { device: string | null }) {
  const t = useT();
  const lang = useApp((s) => s.lang);
  const qc = useQueryClient();
  const { state, error, connect, disconnect, onBytes } = useUartStream();
  const [rows, setRows] = useState<string[]>([]);
  const buf = useRef<string[]>([]);
  const dirty = useRef(false);
  const board = useDashboardQuery({ queryKey: ["bench-board"], queryFn: ({ signal }) => fetchBenchBoard(signal), refetchMs: 30_000 });
  const term = useRef<HTMLPreElement>(null);

  const [durText, setDurText] = useState(String(DUR_DEFAULT));
  const dur = Math.max(DUR_MIN, Math.min(DUR_MAX, Math.round(Number(durText)) || DUR_DEFAULT));
  const [capturing, setCapturing] = useState(false);
  const [last, setLast] = useState<LastCapture | null>(null);
  const caps = useSerialCaptures(device);
  const [armed, setArmed] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  // What the large view shows: the live lines, or one capture file.
  const [view, setView] = useState<null | "live" | SerialCapture>(null);
  const opened = view && view !== "live" ? view : null;
  const capText = useSerialCaptureText(opened);

  useEffect(() => {
    const split = new LineSplitter();
    const off = onBytes((chunk) => {
      const ls = split.push(chunk);
      if (!ls.length) return;
      buf.current.push(...ls);
      if (buf.current.length > TERM_CAP) buf.current.splice(0, buf.current.length - TERM_CAP);
      dirty.current = true;
    });
    const id = window.setInterval(() => {
      if (!dirty.current) return;
      dirty.current = false;
      setRows(buf.current.slice());
    }, 250);
    return () => {
      off();
      window.clearInterval(id);
    };
  }, [onBytes]);

  const clear = () => {
    buf.current = [];
    dirty.current = false;
    setRows([]);
  };

  // Connect once per mount. `device` may be null, and the serial transport
  // does not use it to pick a port (alb/transport/factory.py), so an adb
  // device appearing mid-boot must not reconnect and wipe the boot log.
  const deviceRef = useRef(device);
  deviceRef.current = device;
  useEffect(() => {
    connect(deviceRef.current);
    return () => disconnect();
  }, [connect, disconnect]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: new rows are the trigger; land on the newest line
  useEffect(() => {
    const el = term.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [rows]);

  const clearAndReconnect = () => {
    clear();
    connect(device);
  };

  const copy = async () => {
    if (!rows.length) {
      toast(t("串口还没有输出。", "Nothing on the serial line yet."));
      return;
    }
    toast((await copyText(rows.join("\n"))) ? t(`已复制 ${rows.length} 行。`, `Copied ${rows.length} lines.`) : t("复制失败。", "Copy failed."));
  };

  const capture = async () => {
    setCapturing(true);
    setDurText(String(dur));
    try {
      const r = await triggerUartCapture(dur, device);
      if (r.ok) {
        setLast({ ok: true, filename: r.filename ?? "", lines: r.lines ?? 0, errors: r.errors ?? 0, owner: device });
      } else {
        setLast({ ok: false, error: r.error ?? t("抓取失败。", "Capture failed.") });
      }
    } catch (e) {
      setLast({ ok: false, error: e instanceof Error ? e.message : String(e) });
    } finally {
      setCapturing(false);
      void qc.invalidateQueries({ queryKey: capturesKey(device) });
    }
  };

  const remove = async (c: SerialCapture) => {
    setDeleting(true);
    try {
      await deleteUartCapture(c.name, c.owner);
      toast(t(`已删除 ${c.name}。`, `Deleted ${c.name}.`));
      if (opened && keyOf(opened) === keyOf(c)) setView(null);
      if (last?.ok && last.filename === c.name && last.owner === c.owner) setLast(null);
    } catch (e) {
      toast(t(`删除失败:${e instanceof Error ? e.message : String(e)}`, `Delete failed: ${e instanceof Error ? e.message : String(e)}`));
    } finally {
      setDeleting(false);
      setArmed(null);
      void qc.invalidateQueries({ queryKey: capturesKey(device) });
    }
  };

  const openLast = () => {
    if (!last?.ok || !last.filename) return;
    const known = caps.data?.find((c) => c.name === last.filename && c.owner === last.owner);
    setView(known ?? { name: last.filename, owner: last.owner, size_bytes: 0, mtime: Date.now() / 1000 });
  };

  const b = board.data;
  const port = b?.serial?.com ? `${b.serial.com}${b.serial.baud ? ` · ${b.serial.baud}` : ""}` : "";
  const tone = state === "ready" ? "ok" : state === "connecting" ? "busy" : state === "error" ? "bad" : "idle";
  const list = caps.data ?? [];
  const now = Date.now() / 1000;

  return (
    <div id="p-uart" role="tabpanel">
      <div className="log-pad log-tools">
        {b?.serial?.com ? (
          <span className="chip"><Dot tone={tone} /><b>{b.serial.com}</b><span>{b.serial.baud}</span></span>
        ) : (
          <span className="chip"><Dot tone={tone} /><span>{state === "ready" ? t("已连上", "connected") : state === "connecting" ? t("连接中…", "connecting…") : state === "ended" ? t("已结束", "ended") : t("未连接", "not connected")}</span></span>
        )}
        {b?.serial ? (
          <span className="t-body-small t-muted">{t(`${b.serial.readers} 个读者共用这个口`, `${b.serial.readers} readers share the port`)}</span>
        ) : null}
        <span className="grow" />
        <button type="button" className="icon-btn icon-btn--sm" aria-label={t("清屏", "Clear")} title={t("清屏", "Clear")} onClick={clear} disabled={!rows.length}>
          <Icon name="close" size="sm" />
        </button>
        <button type="button" className="icon-btn icon-btn--sm" aria-label={t("复制全部", "Copy all")} title={t("复制全部", "Copy all")} onClick={() => void copy()} disabled={!rows.length}>
          <Icon name="copy" size="sm" />
        </button>
        <button type="button" className="btn btn--text btn--sm" onClick={() => setView("live")}>
          <Icon name="open-full" size="sm" />
          <span>{t("放大查看", "Open large")}</span>
        </button>
        <Link className="btn btn--tonal btn--sm" to="/console"><Icon name="terminal" size="sm" /><span>{t("打开控制台输入", "Type into it")}</span></Link>
      </div>
      {error ? (
        <div className="log-note">
          <ErrorBanner action={<button type="button" className="btn btn--text btn--sm" onClick={clearAndReconnect}>{t("清屏并重连", "Clear and reconnect")}</button>}>
            {t("串口流断了:", "The serial stream failed: ")}{error}
          </ErrorBanner>
        </div>
      ) : state === "ended" ? (
        <div className="log-note">
          <div className="banner banner--warn">
            <Icon name="alert" />
            <p>{t("串口流结束了:串口线被拔掉,或台架代理断开了。", "The serial stream ended: the cable was unplugged or the bench agent dropped.")}</p>
            <div className="banner__actions">
              <button type="button" className="btn btn--text btn--sm" onClick={clearAndReconnect}>{t("清屏并重连", "Clear and reconnect")}</button>
            </div>
          </div>
        </div>
      ) : null}
      {rows.length === 0 ? (
        <Empty icon="plug" title={state === "ready" ? t("连上了,串口还没有输出", "Connected, nothing on the serial line yet") : t("等待串口", "Waiting for the serial port")}>
          {t(
            "板子往串口打印时,这里会逐行出现。不用选 adb 设备,这里读的是台架上的串口,板子停在 bootloader 或 adb 断了也能看。",
            "Lines appear here as the board prints to the console. No adb device is needed: this reads the bench's serial port, so it works while the board sits in the bootloader or adb is down.",
          )}
        </Empty>
      ) : (
        <pre className="term" ref={term} style={{ margin: "0 16px 8px", height: 380 }} onDoubleClick={() => setView("live")} title={t("双击放大", "Double-click to open large")}>
          {rows.join("\n")}
        </pre>
      )}
      {rows.length ? (
        <div className="log-status">
          <span>{t(`最近 ${rows.length.toLocaleString()} 行,最多留 ${TERM_CAP.toLocaleString()} 行`, `Last ${rows.length.toLocaleString()} lines, up to ${TERM_CAP.toLocaleString()} kept`)}</span>
        </div>
      ) : null}

      <section className="log-pad stack log-caps" aria-labelledby="uart-caps-h">
        <div className="log-tools">
          <h3 id="uart-caps-h" className="t-title-small">{t("抓到文件", "Capture to a file")}</h3>
          {list.length ? <span className="t-body-small t-muted">{t(`已有 ${list.length} 份`, `${list.length} so far`)}</span> : null}
          <span className="grow" />
          <label className="log-dur">
            <span>{t("时长", "Length")}</span>
            <input
              type="number"
              className="input"
              min={DUR_MIN}
              max={DUR_MAX}
              value={durText}
              disabled={capturing}
              onChange={(e) => setDurText(e.target.value)}
              aria-label={t("抓取时长(秒,1 到 300)", "Capture length (seconds, 1 to 300)")}
            />
            <span>{t("秒", "s")}</span>
          </label>
          <button type="button" className="btn btn--outlined btn--sm" disabled={capturing} onClick={() => void capture()}>
            {capturing ? <span className="spinner" style={{ width: 14, height: 14, borderWidth: 2 }} /> : <Icon name="record" size="sm" />}
            <span>{capturing ? t(`抓取中…(${dur} 秒)`, `Capturing… (${dur} s)`) : t(`抓取 ${dur} 秒`, `Capture ${dur} s`)}</span>
          </button>
          <button type="button" className="icon-btn icon-btn--sm" aria-label={t("刷新抓取记录", "Refresh captures")} title={t("刷新抓取记录", "Refresh captures")} onClick={() => void caps.refetch()} disabled={caps.isFetching}>
            <Icon name="refresh" size="sm" />
          </button>
        </div>

        {last?.ok ? (
          <div className="banner banner--ok" aria-live="polite">
            <Icon name="check-circle" />
            <p>
              {t(`抓好了:${last.filename},${last.lines} 行,其中 ${last.errors} 行带错误字样。`, `Captured ${last.filename}: ${last.lines} lines, ${last.errors} with error keywords.`)}
            </p>
            <div className="banner__actions">
              {last.filename ? <button type="button" className="btn btn--text btn--sm" onClick={openLast}>{t("打开", "Open")}</button> : null}
            </div>
          </div>
        ) : last ? (
          <ErrorBanner action={<button type="button" className="btn btn--text btn--sm" onClick={() => setLast(null)}>{t("知道了", "Dismiss")}</button>}>
            {t("抓取失败:", "Capture failed: ")}{last.error}
          </ErrorBanner>
        ) : null}

        {caps.isLoading ? (
          <div className="skeleton" style={{ height: 96 }} />
        ) : caps.isError ? (
          <ErrorBanner action={<button type="button" className="btn btn--text btn--sm" onClick={() => void caps.refetch()}>{t("重试", "Retry")}</button>}>
            {t("读不到抓取记录:", "Could not list the captures: ")}{caps.error instanceof Error ? caps.error.message : String(caps.error)}
          </ErrorBanner>
        ) : list.length === 0 ? (
          <p className="t-body-small t-muted log-caps__none">{t("还没有抓过。抓到的文件存在工作区,也能在「搜索全部记录」里搜到。", "No captures yet. Captured files go to the workspace and show up in Search all recordings.")}</p>
        ) : (
          <ul className="list">
            {list.map((c) => {
              const k = keyOf(c);
              return (
                <li className="list__item" key={k}>
                  <span className="list__lead"><Icon name="record" /></span>
                  <span className="list__title mono">{c.name}</span>
                  <span className="list__trail">
                    <button type="button" className="btn btn--text btn--sm" onClick={() => setView(c)} aria-label={t(`打开 ${c.name}`, `Open ${c.name}`)}>{t("打开", "Open")}</button>
                    <button type="button" className="btn btn--text btn--sm" onClick={() => setArmed(k)} disabled={deleting} aria-label={t(`删除 ${c.name}`, `Delete ${c.name}`)}>{t("删除", "Delete")}</button>
                  </span>
                  <span className="list__meta">
                    {formatBytes(c.size_bytes)} · {ago(lang, now - c.mtime)} · {c.owner ? <span className="mono">{c.owner}</span> : t("没有关联 adb 设备", "not tied to an adb device")}
                  </span>
                  {armed === k ? (
                    <div className="log-caps__confirm">
                      <Confirm
                        open
                        busy={deleting}
                        message={t(`删除 ${c.name}?文件会从工作区删掉,不能恢复。`, `Delete ${c.name}? The file is removed from the workspace and cannot be restored.`)}
                        confirmLabel={t("删除", "Delete")}
                        cancelLabel={t("取消", "Cancel")}
                        onConfirm={() => void remove(c)}
                        onCancel={() => setArmed(null)}
                      />
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <LogDialog
        open={view === "live"}
        onClose={() => setView(null)}
        title={t("串口输出", "Serial output")}
        sub={[port, t(`最近 ${rows.length} 行`, `last ${rows.length} lines`)].filter(Boolean).join(" · ")}
        lines={rows}
        fileName={`serial-${stamp()}.log`}
        emptyText={t("串口还没有输出。", "Nothing on the serial line yet.")}
      />
      <LogDialog
        open={!!opened}
        onClose={() => setView(null)}
        title={opened?.name ?? ""}
        sub={opened ? [opened.size_bytes ? formatBytes(opened.size_bytes) : "", opened.owner ?? t("没有关联 adb 设备", "not tied to an adb device")].filter(Boolean).join(" · ") : undefined}
        lines={capText.data ? textLines(capText.data.text) : []}
        loading={capText.isFetching}
        error={capText.isError ? (capText.error instanceof Error ? capText.error.message : String(capText.error)) : null}
        fileName={opened?.name ?? "serial.log"}
        onRefresh={() => void capText.refetch()}
        emptyText={t("这份抓取是空的。", "This capture is empty.")}
      />
    </div>
  );
}
