/** Saved console commands, kept per browser. Every storage access is guarded: it can throw or be empty. */
export interface Snippet {
  id: string;
  cmd: string;
  /** Changes the board (state or files): Run asks first. */
  risky: boolean;
}

const KEY = "alb.console.snippets";

export const DEFAULT_SNIPPETS: Snippet[] = [
  { id: "d1", cmd: "getprop ro.build.fingerprint", risky: false },
  { id: "d2", cmd: "dumpsys battery", risky: false },
  { id: "d3", cmd: "logcat -b crash -d", risky: false },
  { id: "d4", cmd: "setenforce 0", risky: true },
];

export function loadSnippets(): Snippet[] {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return DEFAULT_SNIPPETS;
    const data: unknown = JSON.parse(raw);
    if (!Array.isArray(data)) return DEFAULT_SNIPPETS;
    const out: Snippet[] = [];
    for (const it of data) {
      if (it && typeof it === "object" && typeof (it as Snippet).cmd === "string" && (it as Snippet).cmd.trim()) {
        const s = it as Snippet;
        out.push({ id: String(s.id ?? s.cmd), cmd: s.cmd, risky: !!s.risky });
      }
    }
    return out;
  } catch {
    return DEFAULT_SNIPPETS;
  }
}

export function saveSnippets(list: Snippet[]): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(list));
  } catch {
    /* private window or blocked storage: the list lives for this page only */
  }
}
