/**
 * LogSearchTab — Inspect sub-tab for historical regex search across
 * workspace logs (logcat / dmesg / uart artifacts).
 *
 * Complements the live `LogcatTab` xterm stream: that tab is "what's
 * happening now", this tab is "what's already been collected". Calls
 * `GET /api/log/search` (single source of truth: the CLI
 * `alb log search` and MCP `alb_log_search` use the same capability).
 *
 * Mockup baseline: `docs/webui-preview-v2-log-search.html` — class
 * names here must mirror it (L-028).
 */
import { useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";

import { useApp } from "../../stores/app";
import { fetchLogSearch, type LogSearchMatch } from "../../lib/api";

/** Split `content` into [text, hit?, text, hit?, ...] segments so we
 *  can wrap matches in <span class="log-search__hit"> for visual highlight.
 *  Falls back to the raw content if the pattern can't be compiled in the
 *  browser — server has the authoritative parse already. */
function splitHighlights(
  content: string,
  pattern: string,
): Array<{ text: string; hit: boolean }> {
  if (!pattern) return [{ text: content, hit: false }];
  let re: RegExp;
  try {
    re = new RegExp(pattern, "g");
  } catch {
    return [{ text: content, hit: false }];
  }
  const out: Array<{ text: string; hit: boolean }> = [];
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(content)) !== null) {
    if (m.index > last) {
      out.push({ text: content.slice(last, m.index), hit: false });
    }
    out.push({ text: m[0], hit: true });
    last = m.index + m[0].length;
    // Guard against zero-width matches looping forever (e.g. /(?:)/g).
    if (m[0].length === 0) re.lastIndex++;
  }
  if (last < content.length) out.push({ text: content.slice(last), hit: false });
  return out;
}

/** Group matches by file path so we can render a header before each
 *  file's hits (mirrors `grep -H` style output). */
function groupByPath(matches: LogSearchMatch[]): Array<{
  path: string;
  hits: LogSearchMatch[];
}> {
  const order: string[] = [];
  const buckets = new Map<string, LogSearchMatch[]>();
  for (const m of matches) {
    if (!buckets.has(m.path)) {
      order.push(m.path);
      buckets.set(m.path, []);
    }
    buckets.get(m.path)!.push(m);
  }
  return order.map((p) => ({ path: p, hits: buckets.get(p)! }));
}

export function LogSearchTab() {
  const lang = useApp((s) => s.lang);
  const device = useApp((s) => s.device);
  const [pattern, setPattern] = useState("panic|oops|fatal");
  const [maxMatches, setMaxMatches] = useState(200);

  const search = useMutation({
    mutationFn: ({ p, max }: { p: string; max: number }) =>
      fetchLogSearch(p, { device, max }),
  });

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!pattern.trim() || search.isPending) return;
    search.mutate({ p: pattern.trim(), max: maxMatches });
  };

  const grouped = useMemo(() => {
    if (!search.data?.ok || !search.data.data) return [];
    return groupByPath(search.data.data.matches);
  }, [search.data]);

  const statusText = (() => {
    if (search.isPending) return lang === "zh" ? "搜索中…" : "searching…";
    if (search.isError) {
      const msg =
        search.error instanceof Error ? search.error.message : String(search.error);
      return msg;
    }
    if (!search.data) return lang === "zh" ? "未搜索" : "no search yet";
    if (!search.data.ok) {
      return `${search.data.error?.code ?? "ERROR"}: ${search.data.error?.message ?? ""}`;
    }
    const d = search.data.data!;
    const filesN = grouped.length;
    const baseEn = `${d.match_count} match${d.match_count === 1 ? "" : "es"} in ${filesN} file${filesN === 1 ? "" : "s"}`;
    const baseZh = `${d.match_count} 处命中 / ${filesN} 个文件`;
    const ms = search.data.timing_ms != null ? ` · ${search.data.timing_ms} ms` : "";
    return (lang === "zh" ? baseZh : baseEn) + ms;
  })();

  const statusClass = (() => {
    if (search.isError) return "log-search__status log-search__status--err";
    if (!search.data) return "log-search__status";
    if (!search.data.ok) return "log-search__status log-search__status--err";
    if (search.data.data?.truncated)
      return "log-search__status log-search__status--truncated";
    return "log-search__status";
  })();

  return (
    <div className="log-search">
      <form className="log-search__bar" onSubmit={onSubmit}>
        <input
          className="log-search__input"
          type="search"
          value={pattern}
          onChange={(e) => setPattern(e.target.value)}
          placeholder={
            lang === "zh"
              ? "正则，如 panic|oops|BUG|fatal"
              : 'regex e.g. "panic|oops|BUG|fatal"'
          }
          aria-label={lang === "zh" ? "搜索正则" : "search regex"}
        />
        <label className="log-search__status" htmlFor="log-search-max">
          {lang === "zh" ? "上限" : "max"}
        </label>
        <input
          id="log-search-max"
          className="log-search__limit"
          type="number"
          min={1}
          max={1000}
          value={maxMatches}
          onChange={(e) => setMaxMatches(Number(e.target.value) || 200)}
        />
        <button
          type="submit"
          className="log-search__submit"
          disabled={search.isPending || !pattern.trim()}
        >
          {search.isPending
            ? lang === "zh"
              ? "搜索中…"
              : "searching…"
            : lang === "zh"
              ? "搜索"
              : "search"}
        </button>
        <span className={statusClass} role="status" aria-live="polite">
          {statusText}
          {search.data?.ok && search.data.data?.truncated && (
            <> · {lang === "zh" ? "已截断" : "truncated"}</>
          )}
        </span>
      </form>

      {!device && (
        <div className="log-search__empty">
          {lang === "zh"
            ? "未选设备 —— 先在顶栏选一个设备，再来搜索。"
            : "No device selected — pick one from the top-bar first."}
        </div>
      )}

      {search.data?.ok &&
        search.data.data &&
        search.data.data.matches.length === 0 && (
          <div className="log-search__empty">
            {lang === "zh"
              ? "没有命中。换个正则、或先去 Logcat / UART 抓一些日志。"
              : "No matches. Try a different regex or capture some logs first."}
          </div>
        )}

      {grouped.length > 0 && (
        <div className="log-search__results" role="region" aria-label="Matches">
          {grouped.map((g) => (
            <div key={g.path}>
              <div className="log-search__path">{g.path}</div>
              {g.hits.map((m) => (
                <div
                  key={`${g.path}#${m.line_number}`}
                  className="log-search__row"
                >
                  <span className="log-search__line">{m.line_number}</span>
                  <span>
                    {splitHighlights(m.content, pattern).map((seg, i) =>
                      seg.hit ? (
                        <span
                          key={i}
                          className="log-search__hit"
                        >
                          {seg.text}
                        </span>
                      ) : (
                        <span key={i}>{seg.text}</span>
                      ),
                    )}
                  </span>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
