/**
 * Devices hook (raw).
 *
 * Wraps `GET /devices` and exposes the raw `ApiDevice[]` payload plus
 * the transport class name + loading/error metadata. **No view-model
 * projection here** — feature consumers do their own shaping:
 *
 *   - `features/dashboard/useDeviceCards.ts` wraps this + projects to
 *     `DeviceCardData` for `<DeviceStripCompact>`
 *   - `components/DevicePicker.tsx` reads raw + uses
 *     `lib/deviceFormat.ts` utilities for transport/status labels
 *
 * Layering invariant: lib/hooks/ MUST NOT import features/. The earlier
 * version of this file imported `DeviceCardData` + `mapToDeviceCard`
 * (5/22 arch H6 fix that proved incomplete); 5/25 arch HIGH-4 surfaced
 * the residual reverse-dep, fixed here.
 */
import { useDashboardQuery } from "../dashboardQuery";
import { fetchDevices, type ApiDevice, type DevicesResponse } from "../api";

const REFETCH_MS = 5_000;

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
  return {
    devices: data?.devices ?? [],
    transportName: data?.transport ?? null,
    backendError: data && !data.ok ? data.error ?? "transport unavailable" : null,
    isLoading: q.isLoading,
    isError: q.isError,
    error: q.error,
    refetch: q.refetch,
  };
}
