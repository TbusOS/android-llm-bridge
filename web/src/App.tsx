import { useQuery } from "@tanstack/react-query";
import { type ApiVersion, fetchApiVersion } from "./lib/api";
import { Banner } from "./components/Banner";

/**
 * Boot-time smoke screen. Calls GET /api/version and surfaces the
 * schema + alb version so the scaffolding is obviously alive.
 *
 * Replaced in the next commit by the real router + feature modules.
 */
export function App() {
  const { data, error, isLoading } = useQuery<ApiVersion>({
    queryKey: ["apiVersion"],
    queryFn: ({ signal }) => fetchApiVersion(signal),
  });

  return (
    <main className="anth-container" style={{ padding: "var(--space-8) 0" }}>
      <Banner />
      <h1 style={{ marginBottom: "var(--space-5)" }}>alb · Web UI</h1>
      <p style={{ color: "var(--anth-text-secondary)", maxWidth: "60ch" }}>
        React 19 + Vite + TypeScript scaffold is alive. Panels from
        <code> docs/webui-preview.html </code> land module by module in upcoming
        commits; this placeholder reports the live API schema so you can verify
        the wiring to a running <code>alb serve</code>.
      </p>

      {isLoading && (
        <p style={{ marginTop: "var(--space-5)" }}>Probing <code>/api/version</code>…</p>
      )}

      {error && (
        <div
          style={{
            marginTop: "var(--space-5)",
            padding: "var(--space-4)",
            border: "1px solid var(--anth-danger)",
            borderRadius: "var(--radius-md)",
            background: "rgba(161, 66, 56, 0.08)",
          }}
        >
          <strong>Could not reach the API.</strong>{" "}
          <code style={{ display: "block", marginTop: "var(--space-2)" }}>
            {String(error instanceof Error ? error.message : error)}
          </code>
          <p style={{ marginTop: "var(--space-2)", marginBottom: 0 }}>
            Run <code>alb serve</code> on the same host and refresh.
          </p>
        </div>
      )}

      {data && (
        <section
          style={{
            marginTop: "var(--space-5)",
            padding: "var(--space-5)",
            background: "#fff",
            border: "1px solid var(--anth-light-gray)",
            borderRadius: "var(--radius-lg)",
            boxShadow: "var(--shadow-card)",
          }}
        >
          <h3 style={{ marginTop: 0 }}>
            Schema <code>v{data.version}</code> · alb{" "}
            <code>{data.alb_version}</code>
          </h3>
          <p style={{ color: "var(--anth-text-secondary)" }}>
            {data.rest.length} REST endpoints · {data.ws.length} WebSocket
            channels documented.
          </p>
          <details style={{ marginTop: "var(--space-3)" }}>
            <summary style={{ cursor: "pointer", fontFamily: "var(--font-heading)" }}>
              Endpoint list
            </summary>
            <ul style={{ fontFamily: "var(--font-mono)", fontSize: 13 }}>
              {data.rest.map((e) => (
                <li key={`${e.method} ${e.path}`}>
                  <strong>{e.method}</strong> {e.path}
                </li>
              ))}
              {data.ws.map((w) => (
                <li key={w.path}>
                  <strong>WS</strong> {w.path}
                </li>
              ))}
            </ul>
          </details>
        </section>
      )}
    </main>
  );
}
