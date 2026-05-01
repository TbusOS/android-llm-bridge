/**
 * Compact device cards strip — auto-fill (220px min) grid.  Per-card
 * detail (SoC / RAM / display / temp / battery) is owned by
 * <DeviceCard>, which keeps its own `useDeviceDetails(serial)` query
 * so each card polls independently at 30 s (ADR-029 (a)).
 */
import { Plus } from "lucide-react";
import { useApp } from "../../stores/app";
import { DeviceCard } from "./DeviceCard";
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
      {devices.map((dev) => (
        <DeviceCard
          key={dev.id}
          dev={dev}
          isActive={dev.id === active}
          onSelect={onSelect}
        />
      ))}

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
