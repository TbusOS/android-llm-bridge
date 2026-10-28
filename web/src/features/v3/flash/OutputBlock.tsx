/**
 * Fixed-height output on the Flash page, with an "Open large" button that
 * shows every line in the shared LogDialog (numbers, wrap, copy, save).
 * Baseline: docs/webui-preview-v3-flash.html (the "Open large" buttons).
 */
import { useMemo, useState } from "react";

import { Icon } from "../../../components/v3/Icon";
import { LogDialog } from "../../../components/v3/LogDialog";
import { useT } from "../../../lib/i18n";

export function OpenLargeButton({ onClick, tonal = false }: { onClick: () => void; tonal?: boolean }) {
  const t = useT();
  return (
    <button type="button" className={`btn btn--sm ${tonal ? "btn--tonal" : "btn--text"}`} onClick={onClick}>
      <Icon name="open-full" size="sm" />
      {t("放大查看", "Open large")}
    </button>
  );
}

/** Text lines of a block, without the empty line a trailing newline leaves. */
export function linesOf(text: string): string[] {
  if (!text) return [];
  const out = text.split(/\r?\n/);
  if (out[out.length - 1] === "") out.pop();
  return out;
}

export interface OutputBlockProps {
  text: string;
  title: string;
  fileName: string;
  maxHeight?: number;
}

/** A job's stdout / stderr: a short scrolling block plus the full view. */
export function OutputBlock({ text, title, fileName, maxHeight = 140 }: OutputBlockProps) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const lines = useMemo(() => linesOf(text), [text]);
  return (
    <>
      <pre className="term" style={{ fontSize: 12, maxHeight }} aria-label={title} onDoubleClick={() => setOpen(true)} title={t("双击放大", "Double-click to open large")}>
        {text}
      </pre>
      <div className="row">
        <OpenLargeButton onClick={() => setOpen(true)} />
      </div>
      <LogDialog open={open} onClose={() => setOpen(false)} title={title} lines={lines} fileName={fileName} />
    </>
  );
}
