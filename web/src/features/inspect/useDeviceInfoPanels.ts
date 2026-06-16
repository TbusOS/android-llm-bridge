/**
 * Per-device info panels hook (ARCH-2) — security / gpu / processes.
 *
 * These panels back the CLI `alb info <panel>` / MCP `alb_info` but had
 * no web entry, so high-value fields (verified boot / AVB / SELinux, GPU
 * governor, top processes) were CLI-only. SystemInfoTab pulls them
 * alongside the `/devices/{serial}/system` aggregate. On-demand, no
 * auto-refetch (same posture as useDeviceSystem).
 */

import { useQueries } from "@tanstack/react-query";

import {
  fetchInfoPanel,
  type ApiGpuInfo,
  type ApiProcessesInfo,
  type ApiSecurityInfo,
  type InfoPanelEnvelope,
} from "../../lib/api";

export interface DeviceInfoPanels {
  security: InfoPanelEnvelope<ApiSecurityInfo> | undefined;
  gpu: InfoPanelEnvelope<ApiGpuInfo> | undefined;
  processes: InfoPanelEnvelope<ApiProcessesInfo> | undefined;
  isLoading: boolean;
}

export function useDeviceInfoPanels(
  serial: string | null | undefined,
): DeviceInfoPanels {
  const [sec, gpu, proc] = useQueries({
    queries: (["security", "gpu", "processes"] as const).map((panel) => ({
      queryKey: ["device-info-panel", panel, serial],
      enabled: !!serial,
      staleTime: 60_000,
      refetchOnWindowFocus: false,
      queryFn: ({ signal }: { signal?: AbortSignal }) => {
        if (!serial) throw new Error("missing serial");
        return fetchInfoPanel(panel, serial, signal);
      },
    })),
  });

  return {
    security: sec?.data as InfoPanelEnvelope<ApiSecurityInfo> | undefined,
    gpu: gpu?.data as InfoPanelEnvelope<ApiGpuInfo> | undefined,
    processes: proc?.data as InfoPanelEnvelope<ApiProcessesInfo> | undefined,
    isLoading:
      (sec?.isLoading ?? false) ||
      (gpu?.isLoading ?? false) ||
      (proc?.isLoading ?? false),
  };
}
