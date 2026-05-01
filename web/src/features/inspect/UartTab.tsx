/**
 * UART captures tab (PR-C.a).
 *
 * Stateless capture viewer:
 *   - Top bar: duration input (1..300 s) + Capture button.
 *   - Left list: past captures (newest first) — name + size + relative time.
 *   - Right pane: selected capture's raw text in a <pre> with monospace
 *     font + auto-scroll to end on initial render.
 *
 * Real-time stream (xterm.js + WS) lands in PR-C.b.
 */

import { useEffect, useRef, useState } from "react";
import { Play, RefreshCw } from "lucide-react";

import { useApp } from "../../stores/app";
import {
  useTriggerUartCapture,
  useUartCaptureText,
  useUartCaptures,
} from "./useUartCaptures";

const DEFAULT_DURATION = 30;
const MIN_DURATION = 1;
const MAX_DURATION = 300;

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

function relativeTime(mtimeSec: number, lang: "zh" | "en"): string {
  const diffSec = Math.max(0, Date.now() / 1000 - mtimeSec);
  if (diffSec < 60) {
    return lang === "zh" ? `${Math.floor(diffSec)} 秒前` : `${Math.floor(diffSec)}s ago`;
  }
  if (diffSec < 3600) {
    return lang === "zh"
      ? `${Math.floor(diffSec / 60)} 分钟前`
      : `${Math.floor(diffSec / 60)}m ago`;
  }
  if (diffSec < 86400) {
    return lang === "zh"
      ? `${Math.floor(diffSec / 3600)} 小时前`
      : `${Math.floor(diffSec / 3600)}h ago`;
  }
  return lang === "zh"
    ? `${Math.floor(diffSec / 86400)} 天前`
    : `${Math.floor(diffSec / 86400)}d ago`;
}

export function UartTab() {
  const lang = useApp((s) => s.lang);
  const device = useApp((s) => s.device);
  const [duration, setDuration] = useState(DEFAULT_DURATION);
  const [selected, setSelected] = useState<string | null>(null);

  const list = useUartCaptures(device);
  const trigger = useTriggerUartCapture(device);
  const text = useUartCaptureText(selected, device);

  // Auto-select newest after a successful capture, or on first load.
  useEffect(() => {
    const newest = list.data?.captures?.[0];
    if (!selected && newest) {
      setSelected(newest.name);
    }
  }, [list.data, selected]);

  useEffect(() => {
    if (trigger.data?.ok && trigger.data.filename) {
      setSelected(trigger.data.filename);
    }
  }, [trigger.data]);

  const preRef = useRef<HTMLPreElement | null>(null);
  useEffect(() => {
    if (preRef.current) preRef.current.scrollTop = preRef.current.scrollHeight;
  }, [text.data]);

  const onCapture = () => {
    const d = Math.max(MIN_DURATION, Math.min(MAX_DURATION, duration));
    trigger.mutate(d);
  };

  return (
    <div className="uart-tab">
      <div className="uart-tab__bar">
        <label className="uart-tab__duration">
          {lang === "zh" ? "时长（秒）" : "Duration (s)"}
          <input
            type="number"
            min={MIN_DURATION}
            max={MAX_DURATION}
            value={duration}
            onChange={(e) => setDuration(Number(e.target.value) || DEFAULT_DURATION)}
            disabled={trigger.isPending}
          />
        </label>
        <button
          type="button"
          className="btn btn--primary"
          onClick={onCapture}
          disabled={trigger.isPending}
        >
          <Play size={12} style={{ verticalAlign: "-2px" }} />{" "}
          {trigger.isPending
            ? lang === "zh"
              ? `抓取中… (${duration}s)`
              : `Capturing… (${duration}s)`
            : lang === "zh"
              ? "抓取"
              : "Capture"}
        </button>
        <button
          type="button"
          className="btn"
          onClick={() => list.refetch()}
          aria-label={lang === "zh" ? "刷新历史列表" : "Refresh history"}
        >
          <RefreshCw size={12} style={{ verticalAlign: "-2px" }} />
        </button>
        {trigger.data?.ok && (
          <span className="uart-tab__last">
            {lang === "zh"
              ? `上次：${trigger.data.lines ?? 0} 行 / ${trigger.data.errors ?? 0} 错误`
              : `last: ${trigger.data.lines ?? 0} lines / ${trigger.data.errors ?? 0} errors`}
          </span>
        )}
        {trigger.data?.ok === false && (
          <span className="uart-tab__last uart-tab__last--err">
            {lang === "zh" ? `失败：${trigger.data.error}` : `failed: ${trigger.data.error}`}
          </span>
        )}
      </div>

      <div className="uart-tab__body">
        <aside className="uart-tab__list">
          <div className="uart-tab__list-head">
            {lang === "zh" ? "历史 captures" : "Captures"}
            {list.data?.captures && (
              <span className="uart-tab__count">{list.data.captures.length}</span>
            )}
          </div>
          {list.isLoading && (
            <div className="uart-tab__empty">
              {lang === "zh" ? "加载中…" : "Loading…"}
            </div>
          )}
          {list.isError && (
            <div className="uart-tab__empty uart-tab__empty--err">
              {lang === "zh" ? "无法获取列表" : "Couldn't load list"}
            </div>
          )}
          {list.data?.captures && list.data.captures.length === 0 && (
            <div className="uart-tab__empty">
              {lang === "zh"
                ? "还没有 capture · 点击上方「抓取」"
                : "No captures yet — press Capture above"}
            </div>
          )}
          <ul>
            {list.data?.captures?.map((c) => (
              <li
                key={c.name}
                className={selected === c.name ? "is-active" : undefined}
              >
                <button type="button" onClick={() => setSelected(c.name)}>
                  <div className="uart-tab__cap-name">{c.name}</div>
                  <div className="uart-tab__cap-meta">
                    {formatBytes(c.size_bytes)} · {relativeTime(c.mtime, lang)}
                  </div>
                </button>
              </li>
            ))}
          </ul>
        </aside>

        <main className="uart-tab__viewer">
          {!selected && (
            <div className="uart-tab__empty">
              {lang === "zh" ? "选一个 capture 查看" : "Pick a capture to view"}
            </div>
          )}
          {selected && text.isLoading && (
            <div className="uart-tab__empty">{lang === "zh" ? "加载中…" : "Loading…"}</div>
          )}
          {selected && text.isError && (
            <div className="uart-tab__empty uart-tab__empty--err">
              {lang === "zh" ? "读取失败" : "Read failed"}
            </div>
          )}
          {selected && text.data && (
            <pre ref={preRef} className="uart-tab__pre">
              {text.data.text || (lang === "zh" ? "（空）" : "(empty)")}
            </pre>
          )}
        </main>
      </div>
    </div>
  );
}
