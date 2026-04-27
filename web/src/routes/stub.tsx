/**
 * Placeholder page — every feature module that hasn't shipped its real
 * implementation renders this.  Visually matches the section blocks in
 * docs/webui-preview.html so users see continuity with the mockup.
 */
import { useQuery } from "@tanstack/react-query";
import { useApp } from "../stores/app";
import type { ApiVersion } from "../lib/api";
import { fetchApiVersion } from "../lib/api";

export interface StubPageProps {
  title: string;
  titleZh: string;
  summary: string;
  summaryZh: string;
  /** REST paths / WS paths this module will consume. */
  consumes: string[];
}

export function StubPage(props: StubPageProps) {
  const lang = useApp((s) => s.lang);
  const { data } = useQuery<ApiVersion>({
    queryKey: ["apiVersion"],
    queryFn: ({ signal }) => fetchApiVersion(signal),
    staleTime: 60_000,
  });

  const availablePaths = new Set<string>();
  data?.rest.forEach((e) => availablePaths.add(e.path));
  data?.ws.forEach((w) => availablePaths.add(w.path));

  return (
    <section>
      <div className="section-head">
        <h1>{lang === "zh" ? props.titleZh : props.title}</h1>
        <span className="status-pill status-pill--plan">
          {lang === "zh" ? "待实现" : "Planned"}
        </span>
      </div>
      <p className="section-sub">
        {lang === "zh" ? props.summaryZh : props.summary}
      </p>

      <div className="mock-card">
        <div
          style={{
            fontFamily: "var(--font-heading)",
            fontSize: 11,
            fontWeight: 600,
            textTransform: "uppercase",
            letterSpacing: "0.06em",
            color: "var(--anth-text-secondary)",
            marginBottom: "var(--space-3)",
          }}
        >
          {lang === "zh" ? "依赖端点" : "Consumes"}
        </div>
        <div>
          {props.consumes.map((entry) => {
            const path = pathOf(entry);
            const ok = data ? availablePaths.has(path) : null;
            return (
              <span
                key={entry}
                className={
                  ok === null
                    ? "need-pill"
                    : ok
                      ? "need-pill is-ok"
                      : "need-pill is-miss"
                }
                title={
                  ok === null
                    ? "checking…"
                    : ok
                      ? lang === "zh"
                        ? "服务端已就位"
                        : "server supports"
                      : lang === "zh"
                        ? "服务端缺失"
                        : "server missing"
                }
              >
                {entry}
              </span>
            );
          })}
        </div>
      </div>
    </section>
  );
}

/** "GET /devices" → "/devices"; "WS /chat/ws" → "/chat/ws". */
function pathOf(entry: string): string {
  return entry.replace(/^(WS|GET|POST|PUT|DELETE|PATCH)\s+/, "").split(" ")[0] ?? "";
}
