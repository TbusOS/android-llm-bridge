/** Logs page: the live logcat tab. Baseline: docs/webui-preview-v3-logs.html (#p-logcat). */
import { Link } from "@tanstack/react-router";
import { type CSSProperties, useEffect, useMemo, useRef, useState } from "react";

import { Icon } from "../../../components/v3/Icon";
import { Dot, Empty, ErrorBanner, toast } from "../../../components/v3/ui";
import { useT } from "../../../lib/i18n";
import { useShell } from "../../../stores/shell";
import { LEVELS, type Level, type LogEntry, makeMatcher, parseQuery, type useLogcat } from "./logcatModel";

const PAGE = 1000;

export function LogcatPanel({ lc, device }: { lc: ReturnType<typeof useLogcat>; device: string | null }) {
  const t = useT();
  const openSheet = useShell((s) => s.openSheet);
  const [levels, setLevels] = useState<Set<Level>>(new Set(["I", "W", "E", "F"]));
  const [q, setQ] = useState("");
  const [regex, setRegex] = useState(false);
  const [wrap, setWrap] = useState(false);
  const [follow, setFollow] = useState(true);
  const [limit, setLimit] = useState(PAGE);
  const [selId, setSelId] = useState<number | null>(null);
  const pane = useRef<HTMLDivElement>(null);

  const matcher = useMemo(() => makeMatcher(q, levels, regex), [q, levels, regex]);
  const filtered = useMemo(() => lc.visible.filter(matcher.test), [lc.visible, matcher]);
  const shown = filtered.length > limit ? filtered.slice(filtered.length - limit) : filtered;
  const hidden = lc.visible.length - filtered.length;
  const parsed = parseQuery(q);

  const lastId = shown.length ? (shown[shown.length - 1] as LogEntry).id : 0;
  useEffect(() => {
    const el = pane.current;
    if (follow && el) el.scrollTop = el.scrollHeight;
  }, [follow, lastId, selId]);

  const toggleLevel = (l: Level) =>
    setLevels((p) => {
      const n = new Set(p);
      if (n.has(l)) n.delete(l);
      else n.add(l);
      return n;
    });
  const dropToken = (tok: string) => setQ((p) => p.split(/\s+/).filter((x) => x && x !== tok).join(" "));

  const rowText = (e: LogEntry) => `${e.ts} ${e.pid} ${e.level} ${e.tag}: ${e.msg}${e.cont.length ? `\n${e.cont.join("\n")}` : ""}`;
  const ask = (e: LogEntry) => {
    const i = filtered.findIndex((x) => x.id === e.id);
    const near = filtered.slice(Math.max(0, i - 10), i + 11).map(rowText).join("\n").slice(0, 6000);
    openSheet({ zh: `日志 · ${e.tag}`, en: `Log · ${e.tag}`, detail: `Live logcat on device ${device}. Selected line and its neighbours:\n${near}` });
  };
  const copy = async (e: LogEntry) => {
    try {
      await navigator.clipboard.writeText(rowText(e));
      toast(t("已复制。", "Copied."));
    } catch {
      toast(t("复制失败。", "Copy failed."));
    }
  };

  const max = Math.max(1, ...lc.histogram.map((b) => b.d + b.i + b.w + b.e));
  const w = 1000 / lc.histogram.length;
  const live = lc.state === "ready";
  const stateTone = live ? "live" : lc.state === "connecting" ? "busy" : lc.state === "error" ? "bad" : "idle";

  return (
    <div id="p-logcat" role="tabpanel">
      <div className="log-pad stack">
        <div className="log-tools">
          <div className="seg seg--sm" role="group" aria-label={t("级别", "levels")}>
            {LEVELS.map((l) => (
              <button key={l} type="button" aria-pressed={levels.has(l)} onClick={() => toggleLevel(l)}>{l}</button>
            ))}
          </div>
          <label className="input--search">
            <Icon name="search" size="sm" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              aria-label={t("过滤", "filter")}
              placeholder={t("搜索内容;tag:Sensor* pid:1234", "Search text; tag:Sensor* pid:1234")}
            />
            <button type="button" className="chip chip--sm" aria-pressed={regex} onClick={() => setRegex((v) => !v)} title={t("正则", "regex")}>.*</button>
          </label>
          {parsed.tags.map((x) => (
            <button key={`t-${x}`} type="button" className="chip" aria-pressed="true" title={t("点一下去掉", "click to remove")} onClick={() => dropToken(`tag:${x}`)}>
              <Icon name="filter" size="sm" />tag: {x}
            </button>
          ))}
          {parsed.pids.map((x) => (
            <button key={`p-${x}`} type="button" className="chip" aria-pressed="true" title={t("点一下去掉", "click to remove")} onClick={() => dropToken(`pid:${x}`)}>
              <Icon name="filter" size="sm" />pid: {x}
            </button>
          ))}
          <button type="button" className="chip" aria-pressed={wrap} onClick={() => setWrap((v) => !v)}>{t("自动换行", "Wrap lines")}</button>
          <button type="button" className="chip" aria-pressed={follow} onClick={() => setFollow((v) => !v)}>{t("跟随最新", "Follow")}</button>
          {matcher.badRegex ? <span className="t-body-small" style={{ color: "var(--md-sys-color-error)" }}>{t("正则写得不对,先不按文字过滤。", "Invalid regex; text filter ignored.")}</span> : null}
        </div>
        <div>
          <div className="row row--between t-body-small t-muted">
            <span>{t("每 2 秒行数,最近 5 分钟", "Lines per 2 s, last 5 minutes")}</span>
            <span className="legend">
              <span style={{ "--src": "var(--alb-color-log-e)" } as CSSProperties}><i />E/F</span>
              <span style={{ "--src": "var(--alb-color-log-w)" } as CSSProperties}><i />W</span>
              <span style={{ "--src": "var(--alb-color-log-i)" } as CSSProperties}><i />I</span>
              <span style={{ "--src": "var(--alb-color-log-d)" } as CSSProperties}><i />D/V</span>
            </span>
          </div>
          <svg className="histo" role="img" aria-label={t("每 2 秒行数", "lines per 2 s")} viewBox="0 0 1000 56" preserveAspectRatio="none">
            {lc.histogram.map((b, i) => {
              let y = 56;
              return (
                [
                  [b.d, "--alb-color-log-d"],
                  [b.i, "--alb-color-log-i"],
                  [b.w, "--alb-color-log-w"],
                  [b.e, "--alb-color-log-e"],
                ] as const
              ).map(([v, c]) => {
                if (!v) return null;
                const h = (v / max) * 54;
                y -= h;
                return <rect key={`${i}-${c}`} x={(i * w + 0.5).toFixed(1)} width={(w - 1).toFixed(1)} y={y.toFixed(1)} height={h.toFixed(1)} style={{ fill: `var(${c})` }} />;
              });
            })}
          </svg>
        </div>
      </div>

      {lc.state === "error" ? (
        <div style={{ padding: "0 16px 8px" }}>
          <ErrorBanner action={<button type="button" className="btn btn--text btn--sm" onClick={lc.reconnect}>{t("重连", "Reconnect")}</button>}>
            {t("logcat 流断了:", "The logcat stream failed: ")}{lc.error}
          </ErrorBanner>
        </div>
      ) : null}

      {!device ? (
        <Empty icon="logs" title={t("还没有选设备", "No device selected")}>{t("在顶栏选一台设备后,这里会开始显示它的 logcat。", "Pick a device in the top bar to see its logcat here.")}</Empty>
      ) : shown.length === 0 ? (
        <Empty icon="logs" title={lc.received === 0 ? (live ? t("连上了,还没有新日志", "Connected, no lines yet") : t("等待 logcat", "Waiting for logcat")) : t("过滤条件把所有行都滤掉了", "The filters hide every line")}>
          {lc.received === 0 ? t("板子有输出时这里会自动出现。", "Lines appear here as soon as the board prints.") : t("放开级别或清掉搜索词。", "Turn on more levels or clear the search.")}
        </Empty>
      ) : (
        <div className={`logs log-pane${wrap ? "" : " logs--nowrap"}`} ref={pane} onScroll={(ev) => {
          const el = ev.currentTarget;
          if (follow && el.scrollTop + el.clientHeight < el.scrollHeight - 40) setFollow(false);
        }}>
          {filtered.length > shown.length ? (
            <div className="log-more">
              <button type="button" className="btn btn--text btn--sm" onClick={() => { setFollow(false); setLimit((n) => n + PAGE); }}>
                {t(`再显示 ${PAGE} 条更早的`, `Show ${PAGE} older lines`)}
              </button>
            </div>
          ) : null}
          {shown.map((e) => {
            const sel = e.id === selId;
            const cls = e.level === "E" || e.level === "F" ? "E" : e.level === "W" ? "W" : "";
            return (
              <div key={e.id}>
                <button type="button" className={`log${cls ? ` log--${cls}` : ""}`} aria-expanded={sel} onClick={() => setSelId(sel ? null : e.id)}>
                  <span className="log__t">{e.ts.slice(6)}</span>
                  <span className="log__pid t-muted">{e.pid}</span>
                  <span className={`lvl lvl--${e.level}`}>{e.level}</span>
                  <span className="log__tag">{e.tag}</span>
                  <span className="log__msg">{e.msg}</span>
                </button>
                {sel ? (
                  <div className="log-detail">
                    {e.cont.length ? e.cont.join("\n") : `${e.ts}  pid ${e.pid}  tid ${e.tid}\n${e.tag}: ${e.msg}`}
                    <div className="row">
                      <button type="button" className="btn btn--tonal btn--sm" onClick={() => ask(e)}><Icon name="spark" size="sm" /><span>{t("问助手", "Ask the assistant")}</span></button>
                      <Link className="btn btn--text btn--sm" to="/timeline">{t("在时间轴中看", "Open in timeline")}</Link>
                      <button type="button" className="btn btn--text btn--sm" onClick={() => void copy(e)}>{t("复制", "Copy")}</button>
                    </div>
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      )}

      <div className="log-status">
        <span><Dot tone={stateTone} inline />{live ? t(`实时 · 每分钟 ${lc.perMinute.toLocaleString()} 行`, `Live · ${lc.perMinute.toLocaleString()} lines/min`) : lc.state === "connecting" ? t("连接中…", "Connecting…") : t("未连接", "Not connected")}{lc.paused ? t(` · 已暂停,缓存 ${lc.buffered} 行`, ` · paused, ${lc.buffered} buffered`) : ""}</span>
        <span>{t(`显示 ${shown.length.toLocaleString()} / ${lc.visible.length.toLocaleString()} 行 · 过滤掉 ${hidden.toLocaleString()} 行`, `Showing ${shown.length.toLocaleString()} of ${lc.visible.length.toLocaleString()} lines · ${hidden.toLocaleString()} hidden by filters`)}</span>
        <span>{selId === null ? t("没有选中的行", "No line selected") : t("已选 1 行", "Selected: 1 line")}</span>
        {lc.stopped || lc.state === "idle" || lc.state === "ended" ? (
          <button type="button" className="btn btn--text btn--sm" onClick={lc.reconnect} disabled={!device}>{t("重新连接", "Reconnect")}</button>
        ) : null}
      </div>
    </div>
  );
}
