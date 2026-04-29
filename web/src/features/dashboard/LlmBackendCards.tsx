/**
 * LLM backend health grid (be-grid + be-card).  Each card: name +
 * model · 3 stats (latency / tps / errors or status / last-used /
 * budget) · inline sparkline of last 5-min throughput.
 */
import { useApp } from "../../stores/app";
import { Sparkline } from "./Sparkline";
import type { BackendCardData } from "./types";

interface Props {
  backends: BackendCardData[];
}

export function LlmBackendCards({ backends }: Props) {
  const lang = useApp((s) => s.lang);

  return (
    <div className="be-grid">
      {backends.map((be) => (
        <article key={be.name} className="be-card">
          <div className="be-head">
            <span className="be-name">{be.name}</span>
            <span className="be-model">{be.model}</span>
          </div>

          <div className="be-grid-stats">
            {be.status === "up" ? (
              <>
                <div>
                  <div className="be-stat-label">
                    {lang === "zh" ? "延迟 p50" : "latency p50"}
                  </div>
                  <div className="be-stat-value">
                    {/* Runtime health metrics aren't sourced yet —
                     * registry only tells us a backend is implemented,
                     * not its live latency. Show "—" rather than 0 to
                     * avoid implying a real measurement. */}
                    {be.latencyMs !== undefined ? (
                      <>
                        {be.latencyMs}
                        <span className="unit">ms</span>
                      </>
                    ) : (
                      "—"
                    )}
                  </div>
                </div>
                <div>
                  <div className="be-stat-label">tok/s</div>
                  <div className="be-stat-value">
                    {be.tps !== undefined ? be.tps : "—"}
                  </div>
                </div>
                <div>
                  <div className="be-stat-label">
                    {lang === "zh" ? "错误" : "errors"}
                  </div>
                  <div className="be-stat-value">
                    {be.errors !== undefined ? (
                      <>
                        {be.errors.count}
                        <span className="unit">/ {be.errors.total}</span>
                      </>
                    ) : (
                      "—"
                    )}
                  </div>
                </div>
              </>
            ) : (
              <>
                <div>
                  <div className="be-stat-label">
                    {lang === "zh" ? "状态" : "status"}
                  </div>
                  <div className="be-stat-value is-text">
                    {labelStatus(be.status, lang)}
                  </div>
                </div>
                <div>
                  <div className="be-stat-label">
                    {lang === "zh" ? "上次使用" : "last used"}
                  </div>
                  <div className="be-stat-value is-text">
                    {(lang === "zh" ? be.lastUsedZh : be.lastUsed) ?? "—"}
                  </div>
                </div>
                <div>
                  <div className="be-stat-label">
                    {lang === "zh" ? "预算" : "budget"}
                  </div>
                  <div className="be-stat-value is-text">{be.budget ?? "—"}</div>
                </div>
              </>
            )}
          </div>

          <Sparkline
            points={be.spark}
            width={280}
            height={32}
            color="blue"
            strokeWidth={2}
            className="be-spark"
            ariaLabel="last 5 min throughput"
            empty={be.spark.length === 0}
          />
        </article>
      ))}
    </div>
  );
}

function labelStatus(status: BackendCardData["status"], lang: string): string {
  if (lang === "zh") {
    return status === "paused" ? "暂停" : status === "unconfigured" ? "未配置" : "在线";
  }
  return status;
}
