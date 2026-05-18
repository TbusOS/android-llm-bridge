/**
 * DoctorPage — six-layer environment health snapshot.
 *
 * Mirror of the CLI's `alb doctor` output (one source of truth at
 * `src/alb/capabilities/doctor.py`) rendered as a web panel. Layers
 * render as cards in an auto-fit grid; each check is a row with a
 * status icon, name, and detail. The visual baseline is
 * `docs/webui-preview-v2-doctor.html` — class names here must match
 * that mockup verbatim (mockup-baseline-checker rule L-028).
 */
import { useEffect, useState } from "react";

import { useApp } from "../../stores/app";
import type { DoctorCheck, DoctorLayer, DoctorStatus } from "../../lib/api";
import { useDoctor } from "./useDoctor";

const STATUS_ICON: Record<DoctorStatus, string> = {
  ok: "✓",
  warn: "!",
  err: "✗",
  skip: "○",
};

const STATUS_LABEL: Record<DoctorStatus, string> = {
  ok: "ok",
  warn: "warn",
  err: "err",
  skip: "skip",
};

const SUMMARY_ORDER: DoctorStatus[] = ["ok", "warn", "err", "skip"];

function formatRelativeSeconds(ms: number, lang: "en" | "zh"): string {
  if (ms < 1000) return lang === "zh" ? "刚刚" : "just now";
  const s = Math.floor(ms / 1000);
  if (s < 60)
    return lang === "zh" ? `${s} 秒前刷新` : `Updated ${s} s ago`;
  const m = Math.floor(s / 60);
  return lang === "zh" ? `${m} 分钟前刷新` : `Updated ${m} min ago`;
}

function CheckRow({ check }: { check: DoctorCheck }) {
  return (
    <li className="doctor-check" data-status={check.status}>
      <span className="doctor-check__icon">{STATUS_ICON[check.status]}</span>
      <span className="doctor-check__name">{check.name}</span>
      <span className="doctor-check__detail">{check.detail}</span>
    </li>
  );
}

function LayerCard({ layer }: { layer: DoctorLayer }) {
  return (
    <article className="doctor-layer-card" data-status={layer.status}>
      <header className="doctor-layer-card__header">
        <h2 className="doctor-layer-card__title">{layer.name}</h2>
        <span className="doctor-layer-card__status">
          {STATUS_ICON[layer.status]} {STATUS_LABEL[layer.status]}
        </span>
      </header>
      <ul className="doctor-checks">
        {layer.checks.map((c) => (
          <CheckRow key={c.name} check={c} />
        ))}
      </ul>
    </article>
  );
}

export function DoctorPage() {
  const lang = useApp((s) => s.lang);
  const { data, isLoading, isError, error, isFetching, refetch, dataUpdatedAt } =
    useDoctor();

  // Tick once per second so the "Updated N s ago" label stays fresh
  // without invalidating the cached doctor payload.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  return (
    <main className="doctor-page" role="main">
      <header className="doctor-page__header">
        <div className="doctor-page__heading">
          <h1>
            {lang === "zh" ? "Doctor 环境健康检查" : "Doctor — environment health"}
          </h1>
          <p>
            {lang === "zh" ? (
              <>
                六层快照：env、binaries、config、adb、serial、ssh。和{" "}
                <code>alb doctor</code> 命令一致。
              </>
            ) : (
              <>
                Six-layer snapshot: env, binaries, config, adb, serial, ssh.
                Same probes as <code>alb doctor</code>.
              </>
            )}
          </p>
        </div>
        <div className="doctor-page__actions">
          <span className="doctor-page__updated">
            {dataUpdatedAt
              ? formatRelativeSeconds(now - dataUpdatedAt, lang)
              : lang === "zh"
                ? "尚未加载"
                : "Not loaded yet"}
          </span>
          <button
            type="button"
            className="doctor-page__refresh"
            disabled={isFetching}
            onClick={() => {
              void refetch();
            }}
          >
            {isFetching
              ? lang === "zh"
                ? "刷新中…"
                : "Refreshing…"
              : lang === "zh"
                ? "刷新"
                : "Refresh"}
          </button>
        </div>
      </header>

      {isLoading && (
        <div className="doctor-state-banner">
          {lang === "zh" ? "正在加载六层探测…" : "Probing six layers…"}
        </div>
      )}

      {isError && (
        <div className="doctor-state-banner" role="alert">
          {lang === "zh" ? "加载失败：" : "Failed to load: "}
          {error instanceof Error ? error.message : String(error)}
        </div>
      )}

      {data && (
        <>
          <div className="doctor-summary" role="status">
            {SUMMARY_ORDER.map((status) => (
              <span
                key={status}
                className="doctor-summary__chip"
                data-status={status}
              >
                <span className="doctor-summary__icon">{STATUS_ICON[status]}</span>
                <span>
                  {data.summary[status] ?? 0} {STATUS_LABEL[status]}
                </span>
              </span>
            ))}
          </div>

          <div className="doctor-layers">
            {data.layers.map((layer) => (
              <LayerCard key={layer.name} layer={layer} />
            ))}
          </div>
        </>
      )}
    </main>
  );
}
