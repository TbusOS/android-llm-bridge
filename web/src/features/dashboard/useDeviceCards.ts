/**
 * Dashboard view-model wrapper around the raw `useDevices` hook.
 *
 * Projects each `ApiDevice` into the `DeviceCardData` shape that
 * `<DeviceStripCompact>` / `<DeviceCard>` consume. CPU / temp series
 * stay empty here — those come from `/metrics/stream` in a later step.
 *
 * Layering rationale: the raw fetch lives in `lib/hooks/useDevices`
 * (so cross-feature consumers like DevicePicker can use it without
 * pulling in dashboard view-models). Anything dashboard-specific
 * stays in this wrapper.
 */
import { useMemo } from "react";

import type { ApiDevice } from "../../lib/api";
import {
  transportFromName,
  transportLabel,
  statusFrom,
} from "../../lib/deviceFormat";
import { useDevices } from "../../lib/hooks/useDevices";
import type { DeviceCardData } from "./types";

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

export interface DeviceCardsViewModel {
  devices: DeviceCardData[];
  transportName: string | null;
  backendError: string | null;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => void;
}

export function useDeviceCards(): DeviceCardsViewModel {
  const raw = useDevices();
  const devices = useMemo(
    () => raw.devices.map((d) => mapToDeviceCard(raw.transportName, d)),
    [raw.devices, raw.transportName],
  );
  return {
    devices,
    transportName: raw.transportName,
    backendError: raw.backendError,
    isLoading: raw.isLoading,
    isError: raw.isError,
    error: raw.error,
    refetch: raw.refetch,
  };
}
