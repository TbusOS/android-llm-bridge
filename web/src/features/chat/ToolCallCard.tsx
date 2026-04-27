/**
 * Collapsible card for one tool call inside an assistant turn.
 * Class-based — see `.tool-call*` rules in src/styles/components.css.
 */
import { ChevronDown, ChevronRight, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import type { ToolCallEntry } from "./types";

interface Props {
  entry: ToolCallEntry;
}

export function ToolCallCard({ entry }: Props) {
  const [open, setOpen] = useState(entry.status === "running");

  useEffect(() => {
    if (entry.status === "running") setOpen(true);
  }, [entry.status]);

  const elapsed =
    entry.endedAt && entry.startedAt
      ? `${entry.endedAt - entry.startedAt} ms`
      : null;
  const argsSummary = summariseArgs(entry.arguments);

  return (
    <div className="tool-call">
      <button
        type="button"
        className="tool-call-head"
        onClick={() => setOpen((v) => !v)}
      >
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span style={{ color: "var(--anth-orange)" }} aria-hidden="true">
          ●
        </span>
        <span className="name">{entry.name}</span>
        <span
          style={{
            color: "var(--anth-text-secondary)",
            fontWeight: 400,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            flex: 1,
            minWidth: 0,
          }}
        >
          {argsSummary}
        </span>
        <span className="meta">
          {entry.status === "running" ? (
            <>
              <Loader2 size={12} className="spin" />
              <span>running</span>
            </>
          ) : (
            <span>completed{elapsed ? ` · ${elapsed}` : ""}</span>
          )}
        </span>
      </button>

      {open && (
        <div className="tool-call-body">
          <span className="label">arguments</span>
          <pre>{safeStringify(entry.arguments)}</pre>
          {entry.result !== undefined && (
            <>
              <span className="label">result</span>
              <pre>{safeStringify(entry.result)}</pre>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function summariseArgs(args: Record<string, unknown>): string {
  const keys = Object.keys(args);
  if (keys.length === 0) return "()";
  return keys
    .slice(0, 3)
    .map((k) => `${k}=${shortValue(args[k])}`)
    .join(" ");
}

function shortValue(v: unknown): string {
  if (v == null) return String(v);
  if (typeof v === "string")
    return v.length > 24 ? `"${v.slice(0, 24)}…"` : `"${v}"`;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  if (Array.isArray(v)) return `[${v.length}]`;
  return "{…}";
}

function safeStringify(v: unknown): string {
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}
