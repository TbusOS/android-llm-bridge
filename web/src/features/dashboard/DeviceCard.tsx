/**
 * Single device card (DEBT-022 PR-A).
 *
 * Owns the per-card `useDeviceDetails(serial)` query so each card
 * polls independently at 30 s. The DeviceStripCompact parent stays
 * stateless and just maps over devices.
 *
 * Extra fields (SoC / cores / RAM usage / display / temp / battery)
 * come from `GET /devices/{serial}/details`. While the detail query
 * is pending the card falls back to the list-view fields so it never
 * goes blank.
 */

import { useApp } from "../../stores/app";
import { Sparkline } from "./Sparkline";
import type { DeviceCardData } from "./types";
import { useDeviceDetails } from "./useDeviceDetails";

interface Props {
  dev: DeviceCardData;
  isActive: boolean;
  onSelect?: (deviceId: string) => void;
}

export function DeviceCard({ dev, isActive, onSelect }: Props) {
  const lang = useApp((s) => s.lang);
  const { data: details } = useDeviceDetails(
    dev.status === "offline" ? null : dev.id,
  );

  const className =
    dev.status === "offline"
      ? "dev-card is-offline"
      : isActive
        ? "dev-card is-active"
        : dev.status === "warn"
          ? "dev-card is-warn"
          : "dev-card";

  const statusClass =
    dev.status === "warn"
      ? "dev-status dev-status--warn"
      : dev.status === "offline"
        ? "dev-status dev-status--off"
        : "dev-status";

  const tempC = details?.tempC ?? dev.tempC;
  const tempLabel =
    typeof tempC === "number" && tempC > 0 ? tempC.toFixed(1) : "—";

  return (
    <button
      type="button"
      className={className}
      onClick={() => onSelect?.(dev.id)}
      aria-label={`${dev.name} · ${dev.status}`}
    >
      <div className="dev-head">
        <span className={statusClass} aria-hidden={false} />
        <span className="dev-name">{dev.name}</span>
      </div>
      <div className="dev-meta">
        {dev.modelLine ? (
          <>
            {dev.modelLine} ·{" "}
            <span className="dev-transport">{dev.transportLabel}</span>
          </>
        ) : (
          <span style={{ fontStyle: "italic" }}>
            {lang === "zh"
              ? dev.offlineNote ?? "未连接"
              : dev.offlineNote ?? "unreachable"}
          </span>
        )}
      </div>
      {details ? (
        <dl className="dev-detail">
          <div className="dev-detail__row">
            <dt>{lang === "zh" ? "芯片" : "SoC"}</dt>
            <dd>{details.soc || "—"}</dd>
          </div>
          <div className="dev-detail__row">
            <dt>CPU</dt>
            <dd>
              {details.cpuCores > 0 ? `${details.cpuCores}c` : "—"}
              {details.cpuMaxGhz > 0 ? ` · ${details.cpuMaxGhz.toFixed(2)} GHz` : ""}
            </dd>
          </div>
          <div className="dev-detail__row">
            <dt>RAM</dt>
            <dd>
              {details.ramTotalGb > 0
                ? `${details.ramUsedPct}% · ${details.ramTotalGb.toFixed(1)} GB`
                : "—"}
            </dd>
          </div>
          <div className="dev-detail__row">
            <dt>{lang === "zh" ? "屏幕" : "Display"}</dt>
            <dd>
              {details.displaySize
                ? `${details.displaySize}${
                    details.displayDensity ? ` · ${details.displayDensity}` : ""
                  }`
                : "—"}
            </dd>
          </div>
          <div className="dev-detail__row">
            <dt>{lang === "zh" ? "电池" : "Battery"}</dt>
            <dd>{details.batteryPct >= 0 ? `${details.batteryPct}%` : "—"}</dd>
          </div>
        </dl>
      ) : null}
      <div className="dev-metrics">
        <div className="dev-metric">
          <Sparkline
            points={dev.cpuTrend}
            color={dev.cpuColor}
            className="dev-metric-spark"
            ariaLabel="cpu trend"
            empty={dev.status === "offline"}
          />
          <div
            className={
              dev.cpu === null ? "dev-metric-val is-muted" : "dev-metric-val"
            }
          >
            {dev.cpu ?? "—"}
            <span className="unit">% cpu</span>
          </div>
        </div>
        <div className="dev-metric">
          <Sparkline
            points={dev.tempTrend}
            color={dev.tempColor}
            className="dev-metric-spark"
            ariaLabel="temp trend"
            empty={dev.status === "offline"}
          />
          <div
            className={
              tempLabel === "—" ? "dev-metric-val is-muted" : "dev-metric-val"
            }
          >
            {tempLabel}
            <span className="unit">°C</span>
          </div>
        </div>
      </div>
    </button>
  );
}
