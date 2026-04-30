/**
 * LLM backend health grid (be-grid + be-card).
 *
 * Each card: name + model + a 1- or 3-stat row that depends on the
 * runtime state (DEBT-017 closed). All inline styles were lifted into
 * BEM modifiers in `components.css` (`be-stat--full`,
 * `be-stat-value--ellipsis`) so the React layout matches the v2.html
 * mockup baseline 1:1.
 *
 * Six runtime states, all six covered in the mockup:
 *   - up (reachable=true): latency ping (ms or "—") · status=reachable · model
 *   - down (reachable=false, real probe failure): status=down · reason · truncated error
 *   - unprobed (no concrete probe wired OR reachable=null): "registered · runtime: unknown"
 *   - planned (registry status='planned'): "planned · not implemented"
 *   - error (the GET /health request itself failed): "fetch failed"
 *   - loading (probe in flight): "probing… —"
 */
import { useApp } from "../../stores/app";
import type { BackendRuntimeState } from "./useBackends";
import type { BackendCardData } from "./types";

interface Props {
  backends: BackendCardData[];
  runtime: Record<string, BackendRuntimeState>;
}

export function LlmBackendCards({ backends, runtime }: Props) {
  const lang = useApp((s) => s.lang);

  return (
    <div className="be-grid">
      {backends.map((be) => {
        const rt = runtime[be.name] ?? { kind: "loading" };
        return (
          <article key={be.name} className="be-card">
            <div className="be-head">
              <span className="be-name">{be.name}</span>
              <span className="be-model">{be.model}</span>
            </div>
            <div className="be-grid-stats">{renderStats(rt, lang)}</div>
          </article>
        );
      })}
    </div>
  );
}

type Lang = "en" | "zh";
type StateKind = BackendRuntimeState["kind"];

const RENDERERS: { [K in StateKind]: (rt: Extract<BackendRuntimeState, { kind: K }>, lang: Lang) => JSX.Element } = {
  up: renderUp,
  down: renderDown,
  unprobed: renderUnprobed,
  planned: renderPlanned,
  error: renderError,
  loading: renderLoading,
};

function renderStats(rt: BackendRuntimeState, lang: Lang) {
  // Record-based dispatch via a discriminated union: TypeScript checks
  // the table is exhaustive (adding a new BackendRuntimeState kind
  // without updating RENDERERS becomes a compile error).
  const fn = RENDERERS[rt.kind] as (rt: BackendRuntimeState, lang: Lang) => JSX.Element;
  return fn(rt, lang);
}

function renderUp(rt: Extract<BackendRuntimeState, { kind: "up" }>, lang: Lang) {
  return (
    <>
      <div>
        <div className="be-stat-label">
          {lang === "zh" ? "延迟 ping" : "latency ping"}
        </div>
        <div className="be-stat-value">
          {rt.latencyMs === null ? (
            "—"
          ) : (
            <>
              {rt.latencyMs}
              <span className="unit">ms</span>
            </>
          )}
        </div>
      </div>
      <div>
        <div className="be-stat-label">
          {lang === "zh" ? "状态" : "status"}
        </div>
        <div className="be-stat-value is-text">
          {lang === "zh" ? "可达" : "reachable"}
        </div>
      </div>
      <div>
        <div className="be-stat-label">
          {lang === "zh" ? "模型" : "model"}
        </div>
        <div className="be-stat-value is-text">
          {rt.model || (lang === "zh" ? "未知" : "unknown")}
        </div>
      </div>
    </>
  );
}

function renderDown(rt: Extract<BackendRuntimeState, { kind: "down" }>, lang: Lang) {
  return (
    <>
      <div>
        <div className="be-stat-label">
          {lang === "zh" ? "状态" : "status"}
        </div>
        <div className="be-stat-value is-text">
          {lang === "zh" ? "不可达" : "down"}
        </div>
      </div>
      <div>
        <div className="be-stat-label">
          {lang === "zh" ? "原因" : "reason"}
        </div>
        <div className="be-stat-value is-text">
          {rt.reason ?? (lang === "zh" ? "未知" : "unknown")}
        </div>
      </div>
      <div>
        <div className="be-stat-label">
          {lang === "zh" ? "错误" : "error"}
        </div>
        <div
          className="be-stat-value is-text be-stat-value--ellipsis"
          title={rt.error ?? undefined}
        >
          {rt.error ? truncate(rt.error, 28) : "—"}
        </div>
      </div>
    </>
  );
}

function renderUnprobed(_rt: Extract<BackendRuntimeState, { kind: "unprobed" }>, lang: Lang) {
  return (
    <div className="be-stat--full">
      <div className="be-stat-label">
        {lang === "zh" ? "运行状态" : "runtime"}
      </div>
      <div className="be-stat-value is-text">
        {lang === "zh"
          ? "已注册 · 未做活性探测"
          : "registered · runtime: unknown"}
      </div>
    </div>
  );
}

function renderPlanned(_rt: Extract<BackendRuntimeState, { kind: "planned" }>, lang: Lang) {
  return (
    <div className="be-stat--full">
      <div className="be-stat-label">
        {lang === "zh" ? "状态" : "status"}
      </div>
      <div className="be-stat-value is-text">
        {lang === "zh" ? "计划中 · 待实现" : "planned · not implemented"}
      </div>
    </div>
  );
}

function renderError(_rt: Extract<BackendRuntimeState, { kind: "error" }>, lang: Lang) {
  return (
    <div className="be-stat--full">
      <div className="be-stat-label">
        {lang === "zh" ? "探测" : "probe"}
      </div>
      <div className="be-stat-value is-text">
        {lang === "zh" ? "请求失败" : "fetch failed"}
      </div>
    </div>
  );
}

function renderLoading(_rt: Extract<BackendRuntimeState, { kind: "loading" }>, lang: Lang) {
  return (
    <div className="be-stat--full">
      <div className="be-stat-label">
        {lang === "zh" ? "探测中…" : "probing…"}
      </div>
      <div className="be-stat-value">—</div>
    </div>
  );
}

function truncate(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}
