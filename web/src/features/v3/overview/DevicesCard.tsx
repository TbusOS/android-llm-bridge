/**
 * Every device the transport lists, one tile each, when there is more
 * than one to choose from (or the chosen one is gone and another is
 * there). A tile shows what tells boards apart at a glance: model, SoC,
 * memory in use, battery, temperature. Clicking a tile selects it.
 */
import { useQueryClient } from "@tanstack/react-query";

import { Icon } from "../../../components/v3/Icon";
import { CardHead, Dot, Pill } from "../../../components/v3/ui";
import { type ApiDevice, type DeviceDetailsResponse, fetchDeviceDetails } from "../../../lib/api";
import { useDashboardQuery } from "../../../lib/dashboardQuery";
import { useDevices } from "../../../lib/hooks/useDevices";
import { useT } from "../../../lib/i18n";
import { useApp } from "../../../stores/app";
import { transportLabel } from "../../../lib/overviewData";

/** Refetch the list and drop cached details so every tile re-reads. */
export function useRefreshDevices() {
  const qc = useQueryClient();
  const { refetch } = useDevices();
  return () => {
    void refetch();
    void qc.invalidateQueries({ queryKey: ["device-details"] });
  };
}

export function showDeviceStrip(devices: readonly ApiDevice[], selected: string | null): boolean {
  if (devices.length > 1) return true;
  // One device left, but not the chosen one: offer it.
  return devices.length === 1 && !!selected && devices[0]?.serial !== selected;
}

function DeviceTile({ d, selected, onPick }: { d: ApiDevice; selected: boolean; onPick: () => void }) {
  const t = useT();
  const online = d.state === "device";
  const q = useDashboardQuery<DeviceDetailsResponse>({
    queryKey: ["device-details", d.serial],
    queryFn: ({ signal }) => fetchDeviceDetails(d.serial, signal),
    refetchMs: 30_000,
    enabled: online,
  });
  const x = q.data?.device ?? null;
  const ramPct = x && x.extras.ram_total_kb > 0 ? Math.round(((x.extras.ram_total_kb - x.extras.ram_avail_kb) / x.extras.ram_total_kb) * 100) : null;
  const facts: [string, string][] = [
    [t("芯片", "SoC"), x ? x.extras.soc || x.hardware || "—" : "—"],
    [t("内存", "Memory"), x && ramPct !== null ? `${ramPct}% · ${(x.extras.ram_total_kb / 1048576).toFixed(1)} GiB` : "—"],
    [t("电池", "Battery"), x && x.battery_level >= 0 ? `${x.battery_level}%` : "—"],
    [t("温度", "Temp"), x && x.extras.temp_c > 0 ? `${x.extras.temp_c.toFixed(1)} °C` : "—"],
  ];
  return (
    <button type="button" className="dev" aria-pressed={selected} onClick={onPick}>
      <span className="dev__head">
        <Dot tone={online ? "ok" : "warn"} />
        <b className="mono">{d.serial}</b>
        {selected ? <Pill tone="info">{t("已选", "selected")}</Pill> : null}
      </span>
      <span className="dev__sub">{[x ? `${x.brand} ${x.model}`.trim() : d.model ?? d.product, online ? null : d.state].filter(Boolean).join(" · ") || "—"}</span>
      <span className="dev__facts">
        {facts.map(([k, v]) => (
          <span key={k}>
            <small>{k}</small>
            <span className="num">{v}</span>
          </span>
        ))}
      </span>
    </button>
  );
}

export function DevicesCard() {
  const t = useT();
  const device = useApp((s) => s.device);
  const setDevice = useApp((s) => s.setDevice);
  const list = useDevices();
  const refresh = useRefreshDevices();
  const tl = transportLabel(list.transportName);
  const online = list.devices.filter((d) => d.state === "device").length;
  const others = list.devices.length - online;
  const gone = !!device && !list.devices.some((d) => d.serial === device);
  return (
    <article className="card span-12">
      <CardHead
        title={t("设备", "Devices")}
        sub={t(`${online} 台在线 · ${others} 台连不上 · 经 ${tl}`, `${online} online · ${others} not reachable · via ${tl}`)}
        actions={
          <button type="button" className="btn btn--text btn--sm" onClick={refresh}>
            <Icon name="refresh" size="sm" />
            {t("刷新", "Refresh")}
          </button>
        }
      />
      <div className="devs">
        {gone ? (
          <div className="dev dev--gone" aria-current="true">
            <span className="dev__head">
              <Dot tone="warn" />
              <b className="mono">{device}</b>
              <Pill tone="warn">{t("离线", "offline")}</Pill>
            </span>
            <span className="dev__sub">{t(`已选,但 ${tl} 里找不到。可能在重启;点旁边一台换过去。`, `Selected, but ${tl} cannot see it. It may be rebooting; click another tile to switch.`)}</span>
          </div>
        ) : null}
        {list.devices.map((d) => (
          <DeviceTile key={d.serial} d={d} selected={d.serial === device} onPick={() => setDevice(d.serial)} />
        ))}
      </div>
    </article>
  );
}
