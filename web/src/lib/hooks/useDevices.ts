/**
 * Devices hook (raw).
 *
 * Wraps `GET /devices` and exposes the raw `ApiDevice[]` payload plus
 * the transport class name + loading/error metadata. **No view-model
 * projection here** — feature consumers do their own shaping:
 *
 *   - `features/dashboard/DashboardPage.tsx` projects via inline
 *     `useMemo` + `features/dashboard/mappers.ts::mapToDeviceCard`
 *     (ADR-043 · N=1 consumer · no wrapper hook)
 *   - `components/DevicePicker.tsx` reads raw + uses
 *     `lib/deviceFormat.ts` utilities for transport/status labels
 *
 * Layering invariant: lib/hooks/ MUST NOT import features/. The earlier
 * version of this file imported `DeviceCardData` + `mapToDeviceCard`
 * (5/22 arch H6 fix that proved incomplete); 5/25 arch HIGH-4 surfaced
 * the residual reverse-dep, fixed here.
 */
import { useMemo } from "react";

import { useDashboardQuery } from "../dashboardQuery";
import { fetchDevices, type ApiDevice, type DevicesResponse } from "../api";

const REFETCH_MS = 5_000;
const EMPTY_DEVICES: readonly ApiDevice[] = Object.freeze([]);

export interface DevicesRawViewModel {
  /** Raw devices as returned by `GET /devices`. Empty array when the
   *  backend has no transport / probe failed (see backendError). */
  devices: ApiDevice[];
  /** Server-side Transport class name (e.g. "AdbUsbTransport") or null
   *  when the backend returned ok=false. Used by lib/deviceFormat
   *  utilities to derive a UI transport label. */
  transportName: string | null;
  /** Backend returned ok=false (transport build / probe failure). */
  backendError: string | null;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => void;
}

export function useDevices(): DevicesRawViewModel {
  const q = useDashboardQuery<DevicesResponse>({
    queryKey: ["devices"],
    queryFn: ({ signal }) => fetchDevices(signal),
    refetchMs: REFETCH_MS,
  });
  const data = q.data;
  // 5/25 code-r MID-3: `data?.devices ?? []` was constructing a new []
  // every render when data was undefined (loading / error / refetch
  // gap). Downstream useMemo deps `[devices]` then never hit cache.
  // Freeze a shared empty sentinel; when data is present, react-query
  // structural sharing keeps the array reference stable so .map()
  // memoization works.
  const devices = data?.devices ?? EMPTY_DEVICES;
  // Stabilize the return identity too — every consumer that does
  // `useMemo([devicesVm])` benefits. Wrapping in useMemo lets React
  // bail out of downstream re-renders when nothing actually changed.
  return useMemo(
    () => ({
      devices: devices as ApiDevice[],
      transportName: data?.transport ?? null,
      backendError:
        data && !data.ok ? data.error ?? "transport unavailable" : null,
      isLoading: q.isLoading,
      isError: q.isError,
      error: q.error,
      refetch: q.refetch,
    }),
    [
      devices,
      data,
      q.isLoading,
      q.isError,
      q.error,
      q.refetch,
    ],
  );
}
