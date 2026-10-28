/**
 * LogDialog (v3): a log opened over most of the screen.
 *
 * A card only has room for a dozen lines; this shows the same lines with
 * numbers, wrap on/off, copy and save. The caller keeps the data and the
 * controls that fetch it (source, keyword, line count) and passes those
 * controls in as `tools`, so the card and the dialog always show the
 * same read and changing a filter in either changes both.
 *
 * Modal behaviour follows features/inspect/ScreenshotZoom: rendered into
 * <body>, the app root made `inert` while open, Esc closes, focus starts
 * on the close button and goes back to the opener afterwards.
 */
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { copyText } from "../../lib/clipboard";
import { useT } from "../../lib/i18n";
import { Icon } from "./Icon";
import { ErrorBanner, toast } from "./ui";

export interface LogDialogProps {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  sub?: ReactNode;
  /** The caller's own controls, bound to the same state as its card. */
  tools?: ReactNode;
  lines: string[];
  loading?: boolean;
  error?: string | null;
  note?: string;
  /** Case-insensitive substring to mark in every line. */
  highlight?: string;
  /** Name for the saved file, e.g. "bench-agent.log". */
  fileName: string;
  onRefresh?: () => void;
  emptyText?: string;
}

const ERROR_RE = /\b(error|fail|failed|fatal|exception)\b/i;
const WARN_RE = /\b(warn|warning)\b/i;

export function lineTone(line: string): "e" | "w" | undefined {
  if (ERROR_RE.test(line)) return "e";
  if (WARN_RE.test(line)) return "w";
  return undefined;
}

/** Split `line` around case-insensitive matches of `needle`. */
export function markMatches(line: string, needle: string): ReactNode {
  if (!needle) return line;
  const lower = line.toLowerCase();
  const n = needle.toLowerCase();
  const out: ReactNode[] = [];
  let from = 0;
  let at = lower.indexOf(n);
  while (at !== -1) {
    if (at > from) out.push(line.slice(from, at));
    out.push(<mark key={at}>{line.slice(at, at + n.length)}</mark>);
    from = at + n.length;
    at = lower.indexOf(n, from);
  }
  if (from === 0) return line;
  if (from < line.length) out.push(line.slice(from));
  return out;
}

export function LogDialog({ open, onClose, title, sub, tools, lines, loading, error, note, highlight = "", fileName, onRefresh, emptyText }: LogDialogProps) {
  const t = useT();
  const [wrap, setWrap] = useState(true);
  const bodyRef = useRef<HTMLPreElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const openerRef = useRef<HTMLElement | null>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    if (!open) return;
    openerRef.current = document.activeElement as HTMLElement | null;
    const root = document.getElementById("root");
    const rootWasInert = root?.hasAttribute("inert") ?? false;
    if (root && !rootWasInert) root.setAttribute("inert", "");
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onCloseRef.current();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      if (root && !rootWasInert) root.removeAttribute("inert");
      const back = openerRef.current;
      if (back?.isConnected) back.focus();
    };
  }, [open]);

  // The newest lines are at the bottom; land there whenever a read arrives.
  useEffect(() => {
    if (!open) return;
    const el = bodyRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [open, lines]);

  const numberWidth = useMemo(() => `${Math.max(3, String(lines.length).length) + 1}ch`, [lines.length]);

  if (!open) return null;

  const text = lines.join("\n");
  const copy = async () => {
    toast((await copyText(text)) ? t(`已复制 ${lines.length} 行。`, `Copied ${lines.length} lines.`) : t("复制失败。", "Copy failed."));
  };
  const save = () => {
    const url = URL.createObjectURL(new Blob([`${text}\n`], { type: "text/plain;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = fileName;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const matches = highlight ? lines.filter((l) => l.toLowerCase().includes(highlight.toLowerCase())).length : 0;
  const errors = lines.filter((l) => lineTone(l) === "e").length;

  return createPortal(
    // biome-ignore lint/a11y/useKeyWithClickEvents: Esc is handled on window; the scrim click is a mouse convenience
    <div className="dialog-scrim" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="log-dialog-title">
        <div className="dialog__head">
          <div className="dialog__title">
            <h2 id="log-dialog-title">{title}</h2>
            {sub ? <p>{sub}</p> : null}
          </div>
          {onRefresh ? (
            <button type="button" className="icon-btn" onClick={onRefresh} disabled={loading} aria-label={t("重新读取", "Read again")} title={t("重新读取", "Read again")}>
              {loading ? <span className="spinner" style={{ width: 18, height: 18, borderWidth: 2 }} /> : <Icon name="refresh" />}
            </button>
          ) : null}
          <button type="button" className="icon-btn" aria-pressed={wrap} onClick={() => setWrap((w) => !w)} aria-label={t("自动换行", "Wrap lines")} title={t("自动换行", "Wrap lines")}>
            <Icon name="wrap" />
          </button>
          <button type="button" className="icon-btn" onClick={() => void copy()} disabled={lines.length === 0} aria-label={t("复制全部", "Copy all")} title={t("复制全部", "Copy all")}>
            <Icon name="copy" />
          </button>
          <button type="button" className="icon-btn" onClick={save} disabled={lines.length === 0} aria-label={t("存为文件", "Save as file")} title={t("存为文件", "Save as file")}>
            <Icon name="download" />
          </button>
          <button type="button" className="icon-btn" ref={closeRef} onClick={onClose} aria-label={t("关闭", "Close")} title={t("关闭(Esc)", "Close (Esc)")}>
            <Icon name="close" />
          </button>
        </div>
        {tools ? <div className="dialog__tools">{tools}</div> : null}
        {error ? (
          <div style={{ padding: "0 16px 12px" }}>
            <ErrorBanner>{error}</ErrorBanner>
          </div>
        ) : null}
        <div className="dialog__body">
          <pre
            ref={bodyRef}
            className={`term term--lines${wrap ? "" : " term--nowrap"}`}
            style={{ ["--ln-w" as string]: numberWidth }}
            aria-label={typeof title === "string" ? title : t("日志", "log")}
            // biome-ignore lint/a11y/noNoninteractiveTabindex: the log scrolls; keyboard users need to reach it
            tabIndex={0}
          >
            {lines.length === 0 ? (
              <div>
                <i />
                <span>{loading ? t("读取中…", "Reading…") : (emptyText ?? t("没有内容。", "Nothing to show."))}</span>
              </div>
            ) : (
              lines.map((l, i) => (
                // biome-ignore lint/suspicious/noArrayIndexKey: a log line's position is its identity
                <div key={i} className={lineTone(l)}>
                  <i>{i + 1}</i>
                  <span>{markMatches(l, highlight)}</span>
                </div>
              ))
            )}
          </pre>
        </div>
        <div className="dialog__foot" aria-live="polite">
          <span>{t(`${lines.length} 行`, `${lines.length} lines`)}</span>
          {highlight ? <span>{t(`含「${highlight}」的 ${matches} 行`, `${matches} lines contain "${highlight}"`)}</span> : null}
          {errors ? <span>{t(`${errors} 行像错误`, `${errors} lines look like errors`)}</span> : null}
          {note ? <span>{note}</span> : null}
        </div>
      </div>
    </div>,
    document.body,
  );
}
