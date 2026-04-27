/**
 * 4 KPI tiles in a 2x2 grid (mounted next to LiveSessionCard inside
 * .hero-row).  Mockup spec: 26px Poppins 700 number + uppercase mini
 * label + delta line.
 */
import { useApp } from "../../stores/app";
import type { KpiCardData } from "./types";

interface Props {
  items: KpiCardData[];
}

export function KpiStrip({ items }: Props) {
  const lang = useApp((s) => s.lang);

  return (
    <div className="stat-strip">
      {items.map((kpi) => (
        <div key={kpi.label} className="stat-card">
          <div className="stat-label">
            {lang === "zh" ? kpi.labelZh : kpi.label}
          </div>
          <div className="stat-value">
            {kpi.value}
            {kpi.unit ? <span className="unit">{kpi.unit}</span> : null}
          </div>
          <div className="stat-foot">
            {kpi.delta ? (
              <span
                className={kpi.delta.sign === "up" ? "delta--up" : "delta--down"}
              >
                {kpi.delta.text}
              </span>
            ) : null}
            {kpi.deltaText ? (
              <span>{lang === "zh" ? kpi.deltaTextZh : kpi.deltaText}</span>
            ) : null}
          </div>
        </div>
      ))}
    </div>
  );
}
