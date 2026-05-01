/**
 * Per-device full system snapshot hook (PR-B).
 *
 * Single useQuery against `/devices/{serial}/system`. ADR-029 (a)
 * doesn't apply here — system data is mostly static and pulled on
 * demand. No auto-refetch; user clicks Refresh in the UI.
 */

import { useQuery } from "@tanstack/react-query";

import { fetchDeviceSystem } from "../../lib/api";

export function useDeviceSystem(serial: string | null | undefined) {
  return useQuery({
    queryKey: ["device-system", serial],
    enabled: !!serial,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
    queryFn: ({ signal }) => {
      if (!serial) throw new Error("missing serial");
      return fetchDeviceSystem(serial, signal);
    },
  });
}
