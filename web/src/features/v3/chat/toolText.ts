/**
 * Pure helpers for showing tool calls and the files an answer produced.
 * Shared by the live conversation (components/v3/Assistant) and the
 * read-only history on the Chat page.
 */

function clip(s: string, max: number): string {
  return s.length > max ? `${s.slice(0, max)}…` : s;
}

/** One-line `k=v` summary of a call's arguments; the full JSON is shown on expand. */
export function argsSummary(args: Record<string, unknown> | string | null | undefined, max = 96): string {
  if (args == null) return "";
  const line =
    typeof args === "string"
      ? args
      : Object.entries(args)
          .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
          .join(" ");
  return clip(line.replace(/\s+/g, " ").trim(), max);
}

/** Full, indented text of an argument or result value. JSON strings are re-indented. */
export function pretty(v: unknown): string {
  if (v === undefined) return "";
  if (typeof v === "string") {
    const s = v.trim();
    if (s.startsWith("{") || s.startsWith("[")) {
      try {
        return JSON.stringify(JSON.parse(s), null, 2);
      } catch {
        // not JSON: show as written
      }
    }
    return v;
  }
  try {
    return JSON.stringify(v, null, 2) ?? String(v);
  } catch {
    return String(v);
  }
}

/** Session history stores a tool result as a JSON string; give back the value. */
export function parseResult(content: string): unknown {
  const s = content.trim();
  if (s.startsWith("{") || s.startsWith("[")) {
    try {
      return JSON.parse(s);
    } catch {
      // fall through
    }
  }
  return content;
}

const TEXT_KEYS = ["stdout", "text", "output", "content", "log", "lines"] as const;

/**
 * The log-like text inside a tool result, if there is one: the result
 * itself when it is a string, `data` when that is a string (log tail), or
 * `data.stdout` / `data.text` / … (shell and friends). Null for results
 * that are only structured fields.
 */
export function logText(result: unknown): string | null {
  if (typeof result === "string") return result;
  if (!result || typeof result !== "object" || Array.isArray(result)) return null;
  const data = (result as Record<string, unknown>).data;
  if (typeof data === "string") return data;
  if (!data || typeof data !== "object" || Array.isArray(data)) return null;
  const d = data as Record<string, unknown>;
  for (const k of TEXT_KEYS) {
    const v = d[k];
    if (typeof v === "string" && v) {
      if (k === "stdout" && typeof d.stderr === "string" && d.stderr) return `${v}\n${d.stderr}`;
      return v;
    }
    if (Array.isArray(v) && v.length && v.every((x) => typeof x === "string")) return v.join("\n");
  }
  return null;
}

/** First non-empty line of the result's text, for the collapsed card. */
export function firstOutputLine(result: unknown, max = 140): string {
  const text = logText(result);
  if (!text) return "";
  // scan instead of split: the text can be a whole log tail
  let from = 0;
  while (from < text.length) {
    const nl = text.indexOf("\n", from);
    const line = text.slice(from, nl === -1 ? text.length : nl).trim();
    if (line) return clip(line, max);
    if (nl === -1) break;
    from = nl + 1;
  }
  return "";
}

/** The error of a tool result in the standard `{ok:false, error}` envelope. */
export function toolError(result: unknown): { code: string; message: string } | null {
  if (!result || typeof result !== "object" || Array.isArray(result)) return null;
  const r = result as { ok?: unknown; error?: unknown };
  if (r.ok !== false) return null;
  const e = (r.error && typeof r.error === "object" ? r.error : {}) as { code?: unknown; message?: unknown };
  return { code: typeof e.code === "string" ? e.code : "ERROR", message: typeof e.message === "string" ? e.message : "" };
}

/** Lines for the large log view, without the empty line a trailing newline leaves. */
export function toLines(text: string): string[] {
  if (!text) return [];
  return text.replace(/\r\n/g, "\n").replace(/\n$/, "").split("\n");
}

/* ---------------- files ---------------- */

/**
 * Workspace-relative form of an absolute artifact path, or null when the
 * file is outside the workspace. The hub reports its root with symlinks
 * resolved while tools may write through the unresolved one, so when the
 * plain prefix does not match, the root's own folder name is looked up in
 * the path instead.
 */
export function workspaceRel(path: string, root: string | null | undefined): string | null {
  if (!root) return null;
  const p = path.replace(/\\/g, "/");
  const r = root.replace(/\\/g, "/").replace(/\/+$/, "");
  let rel: string | null = null;
  if (r && p.startsWith(`${r}/`)) rel = p.slice(r.length + 1);
  else {
    const base = r.split("/").pop();
    if (base) {
      const at = p.lastIndexOf(`/${base}/`);
      if (at >= 0) rel = p.slice(at + base.length + 2);
    }
  }
  if (!rel || rel.split("/").some((seg) => seg === "..")) return null;
  return rel;
}

export type FileKind = "image" | "binary" | "text";

export function fileKind(name: string): FileKind {
  if (/\.(png|jpe?g|gif|webp)$/i.test(name)) return "image";
  if (/\.(zip|gz|tgz|tar|xz|bz2|7z|img|bin|apk|so|elf|pb)$/i.test(name)) return "binary";
  return "text";
}

/** `devices/<serial>/screenshots/<name>.png` — the hub serves these as images. */
export function screenshotRef(rel: string): { serial: string; name: string } | null {
  const m = /^devices\/([^/]+)\/screenshots\/([^/]+\.png)$/i.exec(rel);
  return m ? { serial: m[1] as string, name: m[2] as string } : null;
}

export function baseName(path: string): string {
  return path.split(/[\\/]/).pop() || path;
}

export function shortId(id: string): string {
  return id.length > 16 ? `${id.slice(0, 8)}…${id.slice(-4)}` : id;
}
