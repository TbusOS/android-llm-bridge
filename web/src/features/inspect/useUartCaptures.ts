/**
 * UART capture hooks (PR-C.a).
 *
 * Three pieces:
 *   - useUartCaptures(device): list of past captures, refetched after
 *     a fresh capture mutation.
 *   - useUartCaptureText(name, device): read one capture's text on
 *     selection.
 *   - useTriggerUartCapture(): mutation that runs a fresh capture and
 *     invalidates the list cache so the new file appears at the top.
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import {
  deleteUartCapture,
  fetchUartCaptures,
  readUartCapture,
  triggerUartCapture,
} from "../../lib/api";

const LIST_KEY = (device: string | null | undefined) => ["uart-captures", device ?? null];
const READ_KEY = (
  name: string | null | undefined,
  device: string | null | undefined,
) => ["uart-capture", device ?? null, name ?? null];
const STALE_MS = 10_000;

export function useUartCaptures(device?: string | null) {
  return useQuery({
    queryKey: LIST_KEY(device),
    queryFn: ({ signal }) => fetchUartCaptures(device, signal),
    // No auto-poll — captures only land when the user (or a future
    // background job) explicitly triggers one.
    // ui-fluency 2026-05-07 MID-3: explicit window-focus + stale flags
    // (mirror useFileBrowser pattern) so alt-tab back doesn't re-fire
    // the list every time.
    staleTime: STALE_MS,
    refetchOnWindowFocus: false,
  });
}

export function useUartCaptureText(
  name: string | null,
  device?: string | null,
) {
  return useQuery({
    queryKey: READ_KEY(name, device),
    enabled: !!name,
    queryFn: ({ signal }) => {
      if (!name) throw new Error("missing capture name");
      return readUartCapture(name, device, signal);
    },
    staleTime: STALE_MS,
    refetchOnWindowFocus: false,
  });
}

export function useTriggerUartCapture(device?: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (duration: number) => triggerUartCapture(duration, device),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: LIST_KEY(device) });
    },
  });
}

export function useDeleteUartCapture(device?: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => deleteUartCapture(name, device),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: LIST_KEY(device) });
    },
  });
}
