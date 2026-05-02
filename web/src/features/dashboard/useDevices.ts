/**
 * Devices hook + mapping.
 *
 * Wraps `GET /devices` and projects the backend payload into the
 * `DeviceCardData` shape that `<DeviceStripCompact>` already
 * understands. CPU / temp series are intentionally empty here — those
 * come from the `/metrics/stream` WS in a later step.
 */
import { useDashboardQuery } from "../../lib/dashboardQuery";

import { fetchDevices, type ApiDevice, type DevicesResponse } from "../../lib/api";
import type { DeviceCardData, DeviceStatus, Transport } from "./types";

const REFETCH_MS = 5_000;

/** Map a Transport class name to the UI's `Transport` union. */
export function transportFromName(name: string | null | undefined): Transport {
  if (!name) return "adb-usb";
  if (name.includes("Ssh")) return "ssh";
  if (name.includes("Serial")) return "uart";
  return "adb-usb";
}

function transportLabel(t: Transport): string {
  switch (t) {
    case "adb-usb":
      return "adb";
    case "adb-wifi":
      return "adb wifi";
    case "adb-tcp":
      return "adb tcp";
    case "uart":
      return "uart";
    case "ssh":
      return "ssh";
  }
}

function statusFrom(state: string): DeviceStatus {
  if (state === "device") return "online";
  if (state === "offline" || state === "unauthorized") return "offline";
  return "warn";
}

export function mapToDeviceCard(
  transportName: string | null,
  d: ApiDevice,
): DeviceCardData {
  const transport = transportFromName(transportName);
  const status = statusFrom(d.state);
  const modelLine = [d.product, d.model].filter(Boolean).join(" · ");
  return {
    id: d.serial,
    name: d.serial,
    modelLine,
    transport,
    transportLabel: transportLabel(transport),
    status,
    cpu: null,
    cpuTrend: [],
    cpuColor: "blue",
    tempC: null,
    tempTrend: [],
    tempColor: "blue",
    offlineNote: status === "offline" ? d.state : undefined,
  };
}

export interface DevicesViewModel {
  devices: DeviceCardData[];
  transportName: string | null;
  /** Backend returned ok=false (transport build / probe failure). */
  backendError: string | null;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => void;
}

export function useDevices(): DevicesViewModel {
  const q = useDashboardQuery<DevicesResponse>({
    queryKey: ["devices"],
    queryFn: ({ signal }) => fetchDevices(signal),
    refetchMs: REFETCH_MS,
  });
  const data = q.data;
  return {
    devices: data?.devices.map((d) => mapToDeviceCard(data.transport, d)) ?? [],
    transportName: data?.transport ?? null,
    backendError: data && !data.ok ? data.error ?? "transport unavailable" : null,
    isLoading: q.isLoading,
    isError: q.isError,
    error: q.error,
    refetch: q.refetch,
  };
}
