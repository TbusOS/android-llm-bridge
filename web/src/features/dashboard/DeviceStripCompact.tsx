/**
 * Compact device cards strip — auto-fill (220px min) grid.  Each card
 * has status dot, name, model line, transport mono badge, CPU + temp
 * mini sparklines.  Active card highlighted with orange border + glow
 * + ACTIVE corner badge.  Last card is the dashed "+ Add device" entry.
 */
import { Plus } from "lucide-react";
import { useApp } from "../../stores/app";
import { Sparkline } from "./Sparkline";
import type { DeviceCardData } from "./types";

interface Props {
  devices: DeviceCardData[];
  onSelect?: (deviceId: string) => void;
  onAdd?: () => void;
}

export function DeviceStripCompact({ devices, onSelect, onAdd }: Props) {
  const lang = useApp((s) => s.lang);
  const active = useApp((s) => s.device);

  return (
    <div className="dev-strip">
      {devices.map((dev) => {
        const isActive = dev.id === active;
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

        return (
          <button
            key={dev.id}
            type="button"
            className={className}
            onClick={() => onSelect?.(dev.id)}
            aria-label={`${dev.name} · ${dev.status}`}
          >
            <div className="dev-head">
              <span
                className={statusClass}
                aria-label={dev.status}
                aria-hidden={false}
              />
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
                    dev.cpu === null
                      ? "dev-metric-val is-muted"
                      : "dev-metric-val"
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
                    dev.tempC === null
                      ? "dev-metric-val is-muted"
                      : "dev-metric-val"
                  }
                >
                  {dev.tempC ?? "—"}
                  <span className="unit">°C</span>
                </div>
              </div>
            </div>
          </button>
        );
      })}

      <button
        type="button"
        className="dev-card is-add"
        onClick={onAdd}
        aria-label={lang === "zh" ? "添加设备" : "Add device"}
      >
        <div>
          <div className="add-title">
            <Plus size={14} style={{ verticalAlign: "-2px" }} />{" "}
            {lang === "zh" ? "添加设备" : "Add device"}
          </div>
          <div className="add-sub">
            {lang === "zh" ? "adb / ssh / 串口 向导" : "adb / ssh / uart wizard"}
          </div>
        </div>
      </button>
    </div>
  );
}
