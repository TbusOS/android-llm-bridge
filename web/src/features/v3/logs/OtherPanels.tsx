/** Logs page: the dmesg and search-all-recordings tabs (serial is in SerialPanel.tsx). */
import { Link } from "@tanstack/react-router";
import { type CSSProperties, type ReactNode, useEffect, useMemo, useRef, useState } from "react";

import { Icon } from "../../../components/v3/Icon";
import { Empty, ErrorBanner } from "../../../components/v3/ui";
import { fetchLogSearch, type LogSearchMatch, postDmesg } from "../../../lib/api";
import { fetchWorkspace } from "../../../lib/api3";
import { useT } from "../../../lib/i18n";

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

/* ---------------- search ---------------- */

const CAPTURE_RE = /capture-\d{4}-\d\d-\d\dT\d\d-\d\d-\d\d(?:-\d{1,3})?/;

function sourceOf(path: string): "dmesg" | "uart" | "logcat" {
  if (/dmesg/i.test(path)) return "dmesg";
  if (/uart|serial/i.test(path)) return "uart";
  return "logcat";
}

/**
 * Mark every match of `pattern` in `text`. The server runs Python `re`;
 * a pattern the browser cannot compile is shown without marks.
 */
export function markRegex(text: string, pattern: string): ReactNode {
  let re: RegExp;
  try {
    re = new RegExp(pattern, "g");
  } catch {
    return text;
  }
  const out: ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null = re.exec(text);
  while (m !== null) {
    if (m[0].length === 0) {
      // zero-width match (e.g. `^`): step past it so the loop ends
      re.lastIndex++;
    } else {
      if (m.index > last) out.push(text.slice(last, m.index));
      out.push(<mark key={m.index}>{m[0]}</mark>);
      last = m.index + m[0].length;
    }
    m = re.exec(text);
  }
  if (!out.length) return text;
  if (last < text.length) out.push(text.slice(last));
  return out;
}

const LIMITS = [50, 200, 500, 1000] as const;

interface SearchResult {
  pattern: string;
  matches: LogSearchMatch[];
  truncated: boolean;
  max: number;
  ms: number | null;
  older: number;
}

/** `since` choices: files last written within this long, counted on the hub's clock. */
const SINCE = ["", "1h", "24h", "7d"] as const;

export function SearchPanel({ device }: { device: string | null }) {
  const t = useT();
  const [q, setQ] = useState("panic|oops|fatal");
  const [max, setMax] = useState<number>(200);
  const [since, setSince] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<SearchResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [collecting, setCollecting] = useState(false);
  const [collected, setCollected] = useState<{ ok: boolean; text: string } | null>(null);

  const run = async () => {
    const pattern = q.trim();
    if (!pattern) return;
    setBusy(true);
    setErr(null);
    try {
      const r = await fetchLogSearch(pattern, { device, max, since: since || null });
      if (r.ok && r.data) setRes({ pattern, matches: r.data.matches, truncated: r.data.truncated, max, ms: r.timing_ms ?? null, older: r.data.files_older ?? 0 });
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

  // A fresh dmesg lands in the workspace; search again so it is included.
  const collectDmesg = async () => {
    if (!device) return;
    setCollecting(true);
    try {
      const r = await postDmesg(device, 5);
      if (r.ok && r.data) {
        setCollected({ ok: true, text: t(`已采集 dmesg:${r.data.lines} 行,其中 ${r.data.errors} 行带错误字样。`, `dmesg collected: ${r.data.lines} lines, ${r.data.errors} with error keywords.`) });
        if (q.trim()) await run();
      } else {
        setCollected({ ok: false, text: `${r.error?.code ?? "ERROR"}: ${r.error?.message ?? ""}` });
      }
    } catch (e) {
      setCollected({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setCollecting(false);
    }
  };

  const files = useMemo(() => new Set(res?.matches.map((m) => m.path) ?? []).size, [res]);

  return (
    <div id="p-search" role="tabpanel">
      <div className="log-pad stack">
        <form className="log-tools" onSubmit={(e) => { e.preventDefault(); void run(); }}>
          <label className="input--search" style={{ flex: "1 1 320px" }}>
            <Icon name="search" size="sm" />
            <input value={q} onChange={(e) => setQ(e.target.value)} aria-label={t("搜索", "search")} placeholder={t("正则,例如 Unable to handle kernel", "Regex, e.g. Unable to handle kernel")} />
          </label>
          <select
            className="select"
            style={{ width: "auto" }}
            aria-label={t("时间范围", "time range")}
            title={t("按文件最后写入的时间筛:只搜这段时间里还在写的文件。日志行自己的时间不统一(logcat 是板子时钟、没有年份),所以按文件筛。", "Filters by when a file was last written: only files still being written in this window are searched. Log lines carry no common clock (logcat has the board's, without a year), so files are filtered instead.")}
            value={since}
            onChange={(e) => setSince(e.target.value)}
          >
            {SINCE.map((s) => (
              <option key={s} value={s}>
                {s === "" ? t("全部时间", "any time") : s === "1h" ? t("最近 1 小时", "last hour") : s === "24h" ? t("最近 24 小时", "last 24 hours") : t("最近 7 天", "last 7 days")}
              </option>
            ))}
          </select>
          <select className="select" style={{ width: "auto" }} aria-label={t("最多列出几处命中", "most hits to list")} value={max} onChange={(e) => setMax(Number(e.target.value))}>
            {LIMITS.map((n) => (
              <option key={n} value={n}>{t(`最多 ${n} 处`, `up to ${n} hits`)}</option>
            ))}
          </select>
          <button type="submit" className="btn btn--filled btn--sm" disabled={busy || !q.trim()}>
            {busy ? <span className="spinner" style={{ width: 14, height: 14, borderWidth: 2 }} /> : <Icon name="search" size="sm" />}
            <span>{t("搜索", "Search")}</span>
          </button>
          <button
            type="button"
            className="btn btn--text btn--sm"
            disabled={!device || collecting}
            onClick={() => void collectDmesg()}
            title={device ? t("现在读一次内核日志存进工作区,然后重新搜索", "Read the kernel log into the workspace now, then search again") : t("要先在顶栏选一台 adb 设备", "Pick an adb device in the top bar first")}
          >
            {collecting ? <span className="spinner" style={{ width: 14, height: 14, borderWidth: 2 }} /> : <Icon name="cpu" size="sm" />}
            <span>{t("采集 dmesg 再搜", "Collect dmesg and search")}</span>
          </button>
        </form>
        {collected ? (
          <div className="t-body-small" aria-live="polite" style={collected.ok ? undefined : { color: "var(--md-sys-color-error)" }}>{collected.text}</div>
        ) : null}
        {err ? <ErrorBanner>{err}</ErrorBanner> : null}
        {res === null && !err ? (
          <Empty icon="search" title={t("在所有已录的日志里搜索", "Search everything already recorded")}>
            {t("时间轴录制、串口抓取、dmesg 和 logcat 抓取都会被搜到。", "Timeline recordings, serial captures, dmesg and logcat captures are all searched.")}
          </Empty>
        ) : res && res.matches.length === 0 ? (
          <Empty icon="search" title={t("没有命中", "No hits")}>
            {res.older
              ? t(`${res.older} 个更早的文件没在这个时间范围里,没搜。放宽时间范围再试。`, `${res.older} older files fall outside this time range and were not searched. Widen the range and try again.`)
              : t("换个关键词,或者先录一段时间轴。", "Try another pattern, or record a timeline first.")}
          </Empty>
        ) : res ? (
          <>
            <div className="t-body-small t-muted" aria-live="polite">
              {t(`${res.matches.length} 处命中,在 ${files} 个文件里`, `${res.matches.length} hits in ${files} files`)}
              {res.ms !== null ? t(` · 用时 ${res.ms} ms`, ` · ${res.ms} ms`) : ""}
              {res.truncated ? t(`,只列了前 ${res.max} 处,调大上限能看到更多`, `; only the first ${res.max} are listed, raise the limit to see more`) : ""}
              {res.older ? t(` · ${res.older} 个更早的文件没搜`, ` · ${res.older} older files not searched`) : ""}
            </div>
            <ul className="list">
              {res.matches.map((m, i) => {
                const cap = CAPTURE_RE.exec(m.path)?.[0];
                const src = sourceOf(m.path);
                return (
                  <li className="list__item log-search-hit" key={`${m.path}-${m.line_number}-${i}`}>
                    <span className="list__lead"><span className={`src-tag ${src === "uart" ? "src--uart" : src === "dmesg" ? "src--kernel" : "src--logcat"}`}>{src === "uart" ? t("串口", "serial") : src}</span></span>
                    <span className="list__title mono">{markRegex(m.content, res.pattern)}</span>
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
