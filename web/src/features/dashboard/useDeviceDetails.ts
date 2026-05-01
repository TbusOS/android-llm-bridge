/**
 * Per-device details hook (DEBT-022 PR-A).
 *
 * Wraps `GET /devices/{serial}/details` and projects the raw API
 * payload into the narrow `DeviceDetailsSummary` shape that the
 * dashboard device strip renders. ADR-029 (a) — single useQuery with
 * `refetchInterval: 30s` for now; static-vs-dynamic field split is
 * deferred to PR-B (inspect page) where the value is higher.
 *
 * Disabled when `serial` is empty / falsy so the hook is safe to call
 * from a parent that doesn't yet know which serial to query.
 */

import { useQuery } from "@tanstack/react-query";

import { fetchDeviceDetails, type ApiDeviceDetails } from "../../lib/api";
import type { DeviceDetailsSummary } from "./types";

const REFETCH_MS = 30_000;

function projectSummary(api: ApiDeviceDetails): DeviceDetailsSummary {
  const total = api.extras.ram_total_kb;
  const avail = api.extras.ram_avail_kb;
  const used = total > 0 ? total - avail : 0;
  return {
    soc: api.extras.soc || "",
    cpuCores: api.extras.cpu_cores || 0,
    cpuMaxGhz: api.extras.cpu_max_khz > 0 ? api.extras.cpu_max_khz / 1_000_000 : 0,
    ramTotalGb: total > 0 ? total / 1024 / 1024 : 0,
    ramUsedPct: total > 0 ? Math.round((used / total) * 100) : 0,
    displaySize: api.extras.display.size ?? "",
    displayDensity: api.extras.display.density ? `${api.extras.display.density} dpi` : "",
    tempC: typeof api.extras.temp_c === "number" ? api.extras.temp_c : -1,
    batteryPct: typeof api.battery_level === "number" ? api.battery_level : -1,
    androidRelease: api.release,
    sdk: api.sdk,
    uptimeSec: api.uptime_sec,
    buildFingerprint: api.build_fingerprint,
    fetchedAt: Date.now(),
  };
}

export function useDeviceDetails(serial: string | null | undefined) {
  return useQuery({
    queryKey: ["device-details", serial],
    enabled: !!serial,
    refetchInterval: REFETCH_MS,
    queryFn: async ({ signal }) => {
      if (!serial) throw new Error("missing serial");
      const r = await fetchDeviceDetails(serial, signal);
      if (!r.ok || !r.device) {
        throw new Error(r.error ?? "device-details fetch failed");
      }
      return projectSummary(r.device);
    },
  });
}
