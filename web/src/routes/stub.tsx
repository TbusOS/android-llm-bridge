/**
 * Placeholder page — each feature module currently renders this until
 * its real implementation lands.  Lists the backend endpoints the
 * module will consume so the wiring intent is visible during
 * scaffolding.
 */
import { useQuery } from "@tanstack/react-query";
import type { ApiVersion } from "../lib/api";
import { fetchApiVersion } from "../lib/api";

export interface StubPageProps {
  title: string;
  titleZh: string;
  summary: string;
  summaryZh: string;
  /** REST paths / WS paths this module will use. */
  consumes: string[];
}

export function StubPage(props: StubPageProps) {
  const { data } = useQuery<ApiVersion>({
    queryKey: ["apiVersion"],
    queryFn: ({ signal }) => fetchApiVersion(signal),
    staleTime: 60_000,
  });

  const availablePaths = new Set<string>();
  data?.rest.forEach((e) => availablePaths.add(e.path));
  data?.ws.forEach((w) => availablePaths.add(w.path));

  return (
    <section style={{ maxWidth: "72ch" }}>
      <header style={{ marginBottom: "var(--space-5)" }}>
        <h1 style={{ marginBottom: "var(--space-2)" }}>{props.title}</h1>
        <p style={{ color: "var(--anth-text-secondary)", marginBottom: 0 }}>
          {props.summary}
        </p>
      </header>

      <div
        style={{
          background: "var(--anth-bg-subtle)",
          border: "1px dashed var(--anth-mid-gray)",
          borderRadius: "var(--radius-md)",
          padding: "var(--space-4) var(--space-5)",
        }}
      >
        <strong style={{ fontFamily: "var(--font-heading)", fontSize: 13 }}>
          Upcoming · consumes
        </strong>
        <ul style={{ marginTop: "var(--space-3)", fontFamily: "var(--font-mono)", fontSize: 13 }}>
          {props.consumes.map((p) => (
            <li key={p}>
              {p}
              {data && (
                <span
                  style={{
                    marginLeft: "var(--space-2)",
                    fontSize: 11,
                    color: availablePaths.has(p.replace(/^(WS|GET|POST) /, "").split(" ")[0] ?? "")
                      ? "var(--anth-green)"
                      : "var(--anth-danger)",
                  }}
                >
                  {availablePaths.has(p.replace(/^(WS|GET|POST) /, "").split(" ")[0] ?? "")
                    ? "server supports ✓"
                    : "server missing ✗"}
                </span>
              )}
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
