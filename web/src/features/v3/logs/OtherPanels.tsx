/** Logs page: dmesg, serial and search-all-recordings tabs. */
import { Link } from "@tanstack/react-router";
import { type CSSProperties, useEffect, useMemo, useRef, useState } from "react";

import { Icon } from "../../../components/v3/Icon";
import { Dot, Empty, ErrorBanner, toast } from "../../../components/v3/ui";
import { fetchLogSearch, type LogSearchMatch, postDmesg, triggerUartCapture } from "../../../lib/api";
import { fetchBenchBoard, fetchWorkspace } from "../../../lib/api3";
import { useDashboardQuery } from "../../../lib/dashboardQuery";
import { useT } from "../../../lib/i18n";
import { useUartStream } from "../../inspect/useUartStream";
import { LineSplitter } from "./logcatModel";

/* ---------------- dmesg ---------------- */

interface KLine {
  t: string;
  level: "I" | "W" | "E" | "F";
  text: string;
}

const KMSG = /^(?:<\d+>)?\[\s*(\d+\.\d+)\]\s?(.*)$/;

export function parseDmesg(text: string): KLine[] {
  const out: KLine[] = [];
  for (const raw of text.split(/\r?\n/)) {
    if (!raw.trim()) continue;
    const m = KMSG.exec(raw);
    const body = m ? (m[2] as string) : raw;
    const level = /Kernel panic|Unable to handle kernel|Oops|BUG:/.test(body) ? "F" : /\b(error|fail(ed|ure)?)\b/i.test(body) ? "E" : /\b(warn(ing)?|denied)\b/i.test(body) ? "W" : "I";
    out.push({ t: m ? `[${(m[1] as string).padStart(12, " ")}]` : "", level, text: body });
  }
  return out;
}

async function readDmesg(device: string): Promise<{ lines: KLine[]; truncated: boolean }> {
  const r = await postDmesg(device, 5);
  if (!r.ok) throw new Error(`${r.error?.code ?? "ERROR"}: ${r.error?.message ?? ""}`);
  const dir = `devices/${device}/logs`;
  const names = (await fetchWorkspace(dir)).filter((e) => !e.is_dir && e.name.endsWith("-dmesg.txt")).map((e) => e.name).sort();
  const newest = names[names.length - 1];
  if (!newest) throw new Error("no dmesg file was written");
  const p = await fetch(`/workspace/files/preview/${[...dir.split("/"), newest].map(encodeURIComponent).join("/")}?max_bytes=1048576`);
  if (!p.ok) throw new Error(`preview returned ${p.status}`);
  const body = (await p.json()) as { text: string; truncated: boolean };
  return { lines: parseDmesg(body.text), truncated: body.truncated };
}

export function DmesgPanel({ device }: { device: string | null }) {
  const t = useT();
  const [data, setData] = useState<{ lines: KLine[]; truncated: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [bad, setBad] = useState(true);
  const [avc, setAvc] = useState(false);
  const asked = useRef<string | null>(null);

  const read = async () => {
    if (!device) return;
    setBusy(true);
    setErr(null);
    try {
      setData(await readDmesg(device));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    if (device && asked.current !== device) {
      asked.current = device;
      void read();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [device]);

  const lines = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (data?.lines ?? []).filter((l) => (!bad || l.level !== "I") && (!avc || l.text.includes("avc")) && (!needle || l.text.toLowerCase().includes(needle)));
  }, [data, q, bad, avc]);
  const SHOW = 2000;

  return (
    <div id="p-dmesg" role="tabpanel">
      <div className="log-pad log-tools">
        <label className="input--search">
          <Icon name="search" size="sm" />
          <input value={q} onChange={(e) => setQ(e.target.value)} aria-label={t("搜索内核日志", "Search kernel messages")} placeholder={t("搜索内核日志", "Search kernel messages")} />
        </label>
        <button type="button" className="chip" aria-pressed={bad} onClick={() => setBad((v) => !v)}>{t("错误和警告", "Errors and warnings")}</button>
        <button type="button" className="chip" aria-pressed={avc} onClick={() => setAvc((v) => !v)}>avc</button>
        <button type="button" className="btn btn--text btn--sm" disabled={!device || busy} onClick={() => void read()}>
          {busy ? <span className="spinner" style={{ width: 14, height: 14, borderWidth: 2 }} /> : <Icon name="refresh" size="sm" />}
          <span>{t("重新读取", "Read again")}</span>
        </button>
      </div>
      {err ? (
        <div style={{ padding: "0 16px 8px" }}><ErrorBanner>{t("读不到 dmesg:", "Could not read dmesg: ")}{err}</ErrorBanner></div>
      ) : null}
      {!device ? (
        <Empty icon="cpu" title={t("还没有选设备", "No device selected")} />
      ) : busy && !data ? (
        <div className="skeleton" style={{ height: 200, margin: "0 16px 16px" }} />
      ) : !data ? (
        <Empty icon="cpu" title={t("还没有读过 dmesg", "dmesg not read yet")} />
      ) : lines.length === 0 ? (
        <Empty icon="cpu" title={t("没有符合的内核日志", "No matching kernel messages")}>{t("放开「错误和警告」或清掉搜索词。", "Turn off Errors and warnings or clear the search.")}</Empty>
      ) : (
        <div className="logs log-pane" style={{ "--log-cols": "120px 22px minmax(0,1fr)", height: 420 } as CSSProperties}>
          {lines.slice(-SHOW).map((l, i) => (
            <div key={i} className={`log${l.level === "E" || l.level === "F" ? " log--E" : l.level === "W" ? " log--W" : ""}`}>
              <span className="log__t">{l.t}</span>
              <span className={`lvl lvl--${l.level}`}>{l.level}</span>
              <span className="log__msg">{l.text}</span>
            </div>
          ))}
        </div>
      )}
      {data ? (
        <div className="log-status">
          <span>{t(`显示 ${Math.min(SHOW, lines.length)} / ${data.lines.length} 行`, `Showing ${Math.min(SHOW, lines.length)} of ${data.lines.length} lines`)}</span>
          {data.truncated ? <span>{t("文件超过 1 MB,只读了开头。", "The file is over 1 MB; only the start was read.")}</span> : null}
        </div>
      ) : null}
    </div>
  );
}

/* ---------------- serial ---------------- */

const TERM_CAP = 500;

export function SerialPanel({ device }: { device: string | null }) {
  const t = useT();
  const { state, error, connect, disconnect, onBytes } = useUartStream();
  const [rows, setRows] = useState<string[]>([]);
  const buf = useRef<string[]>([]);
  const dirty = useRef(false);
  const [capturing, setCapturing] = useState(false);
  const board = useDashboardQuery({ queryKey: ["bench-board"], queryFn: ({ signal }) => fetchBenchBoard(signal), refetchMs: 30_000 });
  const term = useRef<HTMLPreElement>(null);

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

  useEffect(() => {
    if (!device) return;
    buf.current = [];
    setRows([]);
    connect(device);
    return () => disconnect();
  }, [device, connect, disconnect]);

  useEffect(() => {
    const el = term.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [rows]);

  const capture = async () => {
    setCapturing(true);
    try {
      const r = await triggerUartCapture(60, device);
      if (r.ok) toast(t(`抓好了,${r.lines ?? 0} 行,存为 ${r.filename ?? ""}。`, `Captured ${r.lines ?? 0} lines to ${r.filename ?? ""}.`));
      else toast(r.error ?? t("抓取失败。", "Capture failed."));
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e));
    } finally {
      setCapturing(false);
    }
  };

  const b = board.data;
  const tone = state === "ready" ? "ok" : state === "connecting" ? "busy" : state === "error" ? "bad" : "idle";
  return (
    <div id="p-uart" role="tabpanel">
      <div className="log-pad log-tools">
        {b?.serial?.com ? (
          <span className="chip"><Dot tone={tone} /><b>{b.serial.com}</b><span>{b.serial.baud}</span></span>
        ) : (
          <span className="chip"><Dot tone={tone} /><span>{state === "ready" ? t("已连上", "connected") : state === "connecting" ? t("连接中…", "connecting…") : t("未连接", "not connected")}</span></span>
        )}
        {b?.serial ? (
          <span className="t-body-small t-muted">{t(`${b.serial.readers} 个读者共用这个口`, `${b.serial.readers} readers share the port`)}</span>
        ) : null}
        <span className="grow" />
        <button type="button" className="btn btn--outlined btn--sm" disabled={!device || capturing} onClick={() => void capture()}>
          {capturing ? <span className="spinner" style={{ width: 14, height: 14, borderWidth: 2 }} /> : <Icon name="record" size="sm" />}
          <span>{t("抓取 60 秒", "Capture 60 s")}</span>
        </button>
        <Link className="btn btn--tonal btn--sm" to="/console"><Icon name="terminal" size="sm" /><span>{t("打开控制台输入", "Type into it")}</span></Link>
      </div>
      {error ? (
        <div style={{ padding: "0 16px 8px" }}>
          <ErrorBanner action={<button type="button" className="btn btn--text btn--sm" onClick={() => device && connect(device)}>{t("重连", "Reconnect")}</button>}>
            {t("串口流断了:", "The serial stream failed: ")}{error}
          </ErrorBanner>
        </div>
      ) : null}
      {!device ? (
        <Empty icon="plug" title={t("还没有选设备", "No device selected")} />
      ) : rows.length === 0 ? (
        <Empty icon="plug" title={state === "ready" ? t("连上了,串口还没有输出", "Connected, nothing on the serial line yet") : t("等待串口", "Waiting for the serial port")}>
          {t("板子往串口打印时,这里会逐行出现。", "Lines appear here as the board prints to the console.")}
        </Empty>
      ) : (
        <pre className="term" ref={term} style={{ margin: "0 16px 16px", height: 380 }}>{rows.join("\n")}</pre>
      )}
    </div>
  );
}

/* ---------------- search ---------------- */

const CAPTURE_RE = /capture-\d{4}-\d\d-\d\dT\d\d-\d\d-\d\d(?:-\d{1,3})?/;

function sourceOf(path: string): "dmesg" | "uart" | "logcat" {
  if (/dmesg/i.test(path)) return "dmesg";
  if (/uart|serial/i.test(path)) return "uart";
  return "logcat";
}

export function SearchPanel({ device }: { device: string | null }) {
  const t = useT();
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<{ matches: LogSearchMatch[]; truncated: boolean } | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const run = async () => {
    const pattern = q.trim();
    if (!pattern) return;
    setBusy(true);
    setErr(null);
    try {
      const r = await fetchLogSearch(pattern, { device, max: 200 });
      if (r.ok && r.data) setRes({ matches: r.data.matches, truncated: r.data.truncated });
      else {
        setRes(null);
        setErr(`${r.error?.code ?? "ERROR"}: ${r.error?.message ?? ""}`);
      }
    } catch (e) {
      setRes(null);
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div id="p-search" role="tabpanel">
      <div className="log-pad stack">
        <form className="log-tools" onSubmit={(e) => { e.preventDefault(); void run(); }}>
          <label className="input--search" style={{ flex: "1 1 320px" }}>
            <Icon name="search" size="sm" />
            <input value={q} onChange={(e) => setQ(e.target.value)} aria-label={t("搜索", "search")} placeholder={t("正则,例如 Unable to handle kernel", "Regex, e.g. Unable to handle kernel")} />
          </label>
          <button type="submit" className="btn btn--filled btn--sm" disabled={busy || !q.trim()}>
            {busy ? <span className="spinner" style={{ width: 14, height: 14, borderWidth: 2 }} /> : <Icon name="search" size="sm" />}
            <span>{t("搜索", "Search")}</span>
          </button>
        </form>
        {err ? <ErrorBanner>{err}</ErrorBanner> : null}
        {res === null && !err ? (
          <Empty icon="search" title={t("在所有已录的日志里搜索", "Search everything already recorded")}>
            {t("时间轴录制、串口抓取、dmesg 和 logcat 抓取都会被搜到。", "Timeline recordings, serial captures, dmesg and logcat captures are all searched.")}
          </Empty>
        ) : res && res.matches.length === 0 ? (
          <Empty icon="search" title={t("没有命中", "No hits")}>{t("换个关键词,或者先录一段时间轴。", "Try another pattern, or record a timeline first.")}</Empty>
        ) : res ? (
          <>
            <div className="t-body-small t-muted">
              {t(`${res.matches.length} 处命中`, `${res.matches.length} hits`)}{res.truncated ? t(",只列前面这些", ", first ones only") : ""}
            </div>
            <ul className="list">
              {res.matches.map((m, i) => {
                const cap = CAPTURE_RE.exec(m.path)?.[0];
                const src = sourceOf(m.path);
                return (
                  <li className="list__item log-search-hit" key={`${m.path}-${m.line_number}-${i}`}>
                    <span className="list__lead"><span className={`src-tag ${src === "uart" ? "src--uart" : src === "dmesg" ? "src--kernel" : "src--logcat"}`}>{src === "uart" ? t("串口", "serial") : src}</span></span>
                    <span className="list__title mono">{m.content}</span>
                    <span className="list__trail">
                      {cap ? <Link className="btn btn--text btn--sm" to="/timeline" search={{ name: cap } as never}>{t("打开", "Open")}</Link> : null}
                    </span>
                    <span className="list__meta mono">{m.path} · {t("第", "line ")}{m.line_number}{t(" 行", "")}</span>
                  </li>
                );
              })}
            </ul>
          </>
        ) : null}
      </div>
    </div>
  );
}
