/**
 * One tool call in a conversation (live or read-only history). Collapsed it
 * is a single line: name, a short argument summary, time and the first line
 * of output. Expanded it shows the full arguments and the full result;
 * "Open large" puts the output in the log dialog, which is where a long
 * logcat / dmesg / shell result is readable.
 */
import { useMemo, useState } from "react";

import { Icon } from "../../../components/v3/Icon";
import { LogDialog } from "../../../components/v3/LogDialog";
import { toast } from "../../../components/v3/ui";
import { copyText } from "../../../lib/clipboard";
import { useT } from "../../../lib/i18n";
import { argsSummary, firstOutputLine, logText, parseResult, pretty, toLines, toolError } from "./toolText";
import "./assistant.css";

export interface ToolCardProps {
  name: string;
  args?: Record<string, unknown> | string | null;
  /** Raw result; undefined while the call runs or when history has none. */
  result?: unknown;
  running?: boolean;
  /** Seconds the call took; omitted when unknown (history). */
  secs?: string | null;
}

export function ToolCard({ name, args, result, running = false, secs }: ToolCardProps) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const [large, setLarge] = useState(false);
  const [view, setView] = useState<"text" | "json">("text");
  const has = result !== undefined && result !== null && result !== "";
  const err = has ? toolError(result) : null;
  // history keeps arguments as a JSON string; summarise it like a live call
  const summary = useMemo(() => {
    const v = typeof args === "string" ? parseResult(args) : args;
    return argsSummary(v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : args);
  }, [args]);
  const failed = err !== null;
  const preview = useMemo(() => (has && !failed ? firstOutputLine(result) : ""), [has, failed, result]);

  // Results can be large (a whole log tail); only stringify while shown.
  const shown = open || large;
  const argsText = useMemo(() => (open ? pretty(args ?? {}) : ""), [open, args]);
  const json = useMemo(() => (shown && has ? pretty(result) : ""), [shown, has, result]);
  const text = useMemo(() => (shown && has ? logText(result) : null), [shown, has, result]);
  const asText = text !== null && text !== json && view === "text";
  const body = asText ? (text as string) : json;
  const lines = useMemo(() => (large ? toLines(asText ? (text as string) : json) : []), [large, asText, text, json]);
  const bodyLines = useMemo(() => (open ? toLines(body).length : 0), [open, body]);

  const copy = async () => {
    toast((await copyText(body)) ? t("已复制输出。", "Output copied.") : t("复制失败。", "Copy failed."));
  };

  return (
    <div className="tool">
      {running ? (
        <span className="spinner" style={{ width: 16, height: 16, borderWidth: 2 }} />
      ) : (
        <Icon name={err ? "error" : "check-circle"} size="sm" className={err ? "tool__fail" : undefined} />
      )}
      <code>
        {name}
        {summary ? ` ${summary}` : ""}
      </code>
      {running ? <span className="pill">{t("运行中", "running")}</span> : secs ? <span className="pill">{secs} s</span> : null}
      {err ? (
        <small className="tool__err">
          {err.code}
          {err.message ? `: ${err.message}` : ""}
        </small>
      ) : preview && !open ? (
        <small>{preview}</small>
      ) : null}
      <div className="tool__bar">
        <button type="button" className="btn btn--text btn--sm" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
          <Icon name={open ? "chevron-down" : "chevron-right"} size="sm" />
          {has ? t("参数和输出", "Arguments and output") : t("参数", "Arguments")}
        </button>
        {has ? (
          <button type="button" className="btn btn--text btn--sm" onClick={() => setLarge(true)}>
            <Icon name="open-full" size="sm" />
            {t("放大查看", "Open large")}
          </button>
        ) : null}
      </div>
      {open ? (
        <div className="tool__body">
          <span className="tool__label">{t("参数", "Arguments")}</span>
          <pre className="term" aria-label={t(`${name} 的参数`, `${name} arguments`)}>
            {argsText}
          </pre>
          {has ? (
            <>
              <div className="tool__label">
                <span>
                  {t("输出", "Output")} · {t(`${bodyLines} 行`, `${bodyLines} lines`)}
                </span>
                <div className="row">
                  {text !== null && text !== json ? (
                    // biome-ignore lint/a11y/useSemanticElements: M3 segmented button, same markup as the model source switch
                    <div className="seg seg--sm" role="group" aria-label={t("输出格式", "Output format")}>
                      <button type="button" aria-pressed={view === "text"} onClick={() => setView("text")}>
                        {t("文本", "Text")}
                      </button>
                      <button type="button" aria-pressed={view === "json"} onClick={() => setView("json")}>
                        JSON
                      </button>
                    </div>
                  ) : null}
                  <button type="button" className="icon-btn icon-btn--sm" onClick={() => void copy()} aria-label={t("复制输出", "Copy output")} title={t("复制输出", "Copy output")}>
                    <Icon name="copy" size="sm" />
                  </button>
                </div>
              </div>
              <pre className="term" aria-label={t(`${name} 的输出`, `${name} output`)}>
                {body}
              </pre>
            </>
          ) : running ? (
            <span className="tool__label">{t("还在运行,输出回来后显示在这里。", "Still running; the output shows here when it returns.")}</span>
          ) : null}
        </div>
      ) : null}
      {has ? (
        <LogDialog
          open={large}
          onClose={() => setLarge(false)}
          title={name}
          sub={summary || undefined}
          lines={lines}
          fileName={`${name}-output.txt`}
          emptyText={t("这次调用没有输出。", "This call returned no output.")}
        />
      ) : null}
    </div>
  );
}
